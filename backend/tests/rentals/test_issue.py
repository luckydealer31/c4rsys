"""Issue of a vehicle without a booking (ТЗ § 14.2.2, § 14.3.1, § 14.4.3–14.4.4, § 10.1.5–10.1.6;
acceptance criteria 2, 4, 5, 6)."""

import threading
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock
from uuid import UUID

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import Client

from c4rsys.companies.models import Employment
from c4rsys.fleet.models import OdometerReading, VehicleCard
from c4rsys.inspections.models import Inspection
from c4rsys.orders.models import Order
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.platform.errors import ApiError
from c4rsys.platform.models import AuditEvent, IdempotencyRecord
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals.models import Rental
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key, named_user
from tests.rentals.scenario import (
    ISSUE_AT,
    NOW,
    Scenario,
    add_client,
    add_location,
    add_vehicle,
    build,
    issue,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def frozen_clock() -> Iterator[None]:
    """«Now» for the issue checks is 3 November 2026 10:00 Asia/Bangkok."""
    with mock.patch.object(issue_module, "timezone", SimpleNamespace(now=lambda: NOW)):
        yield


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _nothing_written() -> bool:
    return not (
        Rental.objects.exists()
        or Order.objects.exists()
        or Inspection.objects.exists()
        or OdometerReading.objects.exists()
    )


def test_issue_creates_order_rental_inspection_and_reading(owner: Client, s: Scenario) -> None:
    response = issue(owner, s)

    assert response.status_code == 201, response.content
    body = response.json()
    rental = body["rental"]
    assert body["order_created"] is True
    assert rental["state"] == "active"
    assert rental["calc_timezone"] == "Asia/Bangkok"
    assert rental["actual_start"] == "2026-11-01T03:00:00+00:00"  # stored in UTC (§ 4.2.2)
    snapshot = rental["tariff_snapshot"]
    assert snapshot["ranges"] == [{"from": 1, "to": None, "rate": "1000.0000"}]
    assert snapshot["currency"] == "THB" and snapshot["currency_minor_units"] == 2
    assert snapshot["vehicle_terms"]["mileage"]["mode"] == "unlimited"
    assert snapshot["vehicle_terms"]["deposit"]["required"] is False

    order = Order.objects.get()
    assert (order.number, str(order.id)) == (1, rental["order_id"])
    inspection = Inspection.objects.get()
    assert inspection.kind == "issue" and inspection.state == "saved"
    assert inspection.check_status == "not_required"  # policy of a new company (§ 14.3.6)
    assert (inspection.odometer_value, inspection.fuel_value) == (Decimal("10000"), Decimal("8"))
    reading = OdometerReading.objects.get()
    assert (reading.value, reading.source_id) == (Decimal("10000"), inspection.id)
    actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {
        "order.created",
        "rental.issued",
        "inspection.saved",
        "vehicle.odometer_recorded",
    } <= actions


def test_order_shows_the_rental_and_current_cost(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]

    order = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    detail = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]

    assert order["state"] == "open"
    assert order["operations"] == [
        {"type": "rental", "id": rental["id"], "state": "active", "finished": False}
    ]
    assert detail["calculation"]["kind"] == "current"


def test_inspection_conducted_when_marked(owner: Client, s: Scenario) -> None:
    issue(owner, s, inspection_conducted=True)

    assert Inspection.objects.get().check_status == "conducted"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"client_id": ""}, "CLIENT_REQUIRED"),
        ({"tariff_id": ""}, "TARIFF_REQUIRED"),
        ({"odometer": ""}, "ODOMETER_REQUIRED"),
        ({"fuel": ""}, "FUEL_REQUIRED"),
        ({"actual_start": (NOW + timedelta(minutes=1)).isoformat()}, "FUTURE_TIME_NOT_ALLOWED"),
    ],
)
def test_required_data_of_14_4_4(
    owner: Client, s: Scenario, overrides: dict[str, Any], code: str
) -> None:
    response = issue(owner, s, **overrides)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert _nothing_written()


def test_vehicle_must_be_in_the_issue_location(owner: Client, s: Scenario) -> None:
    # § 14.2.2.
    krabi = add_location(owner, s.cmp, "Krabi")

    response = issue(owner, s, issue_location_id=krabi)

    assert response.json()["error"]["code"] == "VEHICLE_LOCATION_MISMATCH"
    assert _nothing_written()


def test_vehicle_not_allowed_for_rental_is_not_ready(owner: Client, s: Scenario) -> None:
    # § 8.7.7.
    owner.patch(
        f"/api/c/{s.cmp}/vehicles/{s.vehicle}",
        data={"contexts": [], "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )

    response = issue(owner, s)

    assert response.json()["error"]["code"] == "VEHICLE_NOT_READY"
    assert response.json()["error"]["params"]["missing"] == ["rental_not_allowed"]


def test_tariff_must_be_assigned_to_the_vehicle(owner: Client, s: Scenario) -> None:
    other = post(
        owner,
        f"/api/c/{s.cmp}/tariffs",
        {"name": "Not assigned", "ranges": [{"from": 1, "to": None, "rate": "500"}]},
        key(),
    ).json()["tariff"]["id"]

    response = issue(owner, s, tariff_id=other)

    assert response.json()["error"]["code"] == "TARIFF_NOT_AVAILABLE"


def test_archived_tariff_is_not_available(owner: Client, s: Scenario) -> None:
    post(owner, f"/api/c/{s.cmp}/tariffs/{s.tariff}/archive", {"expected_version": 1}, key())

    response = issue(owner, s)

    # The only tariff is archived, so the location is no longer active (§ 4.4.4).
    assert response.json()["error"]["code"] == "LOCATION_NOT_ACTIVE"


def test_odometer_below_an_earlier_measurement_is_refused(owner: Client, s: Scenario) -> None:
    # § 8.2.12.
    issue(owner, s)
    Rental.objects.update(state="cancelled")  # free the vehicle to issue again
    Inspection.objects.all()  # readings stay

    response = issue(
        owner, s, actual_start=(ISSUE_AT + timedelta(hours=5)).isoformat(), odometer="9000"
    )

    assert response.json()["error"]["code"] == "ODOMETER_SEQUENCE_CONFLICT"
    assert Rental.objects.count() == 1


def test_second_issue_of_the_same_vehicle_is_refused(owner: Client, s: Scenario) -> None:
    issue(owner, s)
    other_client = add_client(owner, s.cmp, "Anna", "+66899999999")

    response = issue(owner, s, client_id=other_client)

    assert response.json()["error"]["code"] == "VEHICLE_NOT_AVAILABLE"
    assert Rental.objects.count() == 1


def test_repeat_with_the_same_key_issues_once(owner: Client, s: Scenario) -> None:
    # Acceptance criterion 4.
    headers = {"Idempotency-Key": "issue-1"}
    first = issue(owner, s, headers)
    again = issue(owner, s, headers)
    other_body = issue(owner, s, headers, fuel="5")

    assert again.json() == first.json()
    assert Rental.objects.count() == 1 and Order.objects.count() == 1
    assert other_body.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_add_to_existing_open_order_of_the_same_client(owner: Client, s: Scenario) -> None:
    # § 12.1.1, § 12.1.5; decision 22.10.2026.
    first = issue(owner, s).json()["rental"]
    second_vehicle = add_vehicle(owner, s, s.location, "Honda Click")

    added = issue(owner, s, vehicle_card_id=second_vehicle, order_id=first["order_id"])

    assert added.status_code == 201, added.content
    assert added.json()["order_created"] is False
    assert added.json()["rental"]["order_id"] == first["order_id"]
    other_client = add_client(owner, s.cmp, "Anna", "+66899999999")
    third = add_vehicle(owner, s, s.location, "Yamaha")
    mismatch = issue(
        owner, s, vehicle_card_id=third, client_id=other_client, order_id=first["order_id"]
    )
    assert mismatch.json()["error"]["code"] == "ORDER_CLIENT_MISMATCH"


def test_permissions_of_the_issue(owner: Client, s: Scenario) -> None:
    # Acceptance criterion 6; decision 22.10.2026 on inspection.conduct.
    driver, _ = hire(owner, s.cmp, "driver@example.com", [{"type": "set", "set": "driver"}])
    krabi = add_location(owner, s.cmp, "Krabi")
    elsewhere, _ = hire(
        owner,
        s.cmp,
        "krabi@example.com",
        [{"type": "set", "set": "rental_manager", "locations": [krabi]}],
    )
    issuer, _ = hire(
        owner, s.cmp, "issuer@example.com", [{"type": "grant", "permission": "rental.issue"}]
    )

    assert issue(driver, s).json()["error"]["code"] == "PERMISSION_DENIED"
    assert issue(elsewhere, s).json()["error"]["code"] == "PERMISSION_DENIED"
    conducted = issue(issuer, s, inspection_conducted=True)
    assert conducted.json()["error"]["params"]["permission"] == "inspection.conduct"
    assert issue(issuer, s).status_code == 201


def test_user_of_another_company_gets_not_found(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    stranger = Client()
    named_user(stranger, "c@example.com")
    create_company(stranger, display_name="Other")

    assert stranger.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").status_code == 404
    assert issue(stranger, s).status_code == 404
    assert AuditEvent.objects.filter(action="security.company_access_denied").count() == 2


def test_journal_failure_posts_nothing_and_retry_succeeds(owner: Client, s: Scenario) -> None:
    # Decision 22.10.2026: without the journal the operation is not posted.
    headers = {"Idempotency-Key": "issue-journal"}
    with mock.patch(
        "c4rsys.platform.models.AuditEvent.objects.bulk_create",
        side_effect=DatabaseError("journal unavailable"),
    ):
        failed = issue(owner, s, headers)

    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "OPERATION_NOT_POSTED"
    assert _nothing_written()
    assert not IdempotencyRecord.objects.filter(key="issue-journal").exists()
    assert issue(owner, s, headers).status_code == 201
    assert Rental.objects.count() == 1


def test_saved_inspection_is_immutable(owner: Client, s: Scenario) -> None:
    # § 10.1.6: only marking invalid with a reason is allowed.
    issue(owner, s)
    inspection = Inspection.objects.get()

    with pytest.raises(DatabaseError), transaction.atomic():
        Inspection.objects.filter(pk=inspection.pk).update(odometer_value=1)
    with pytest.raises(DatabaseError), transaction.atomic():
        Inspection.objects.filter(pk=inspection.pk).update(state="invalid")
    with pytest.raises(DatabaseError), transaction.atomic():
        Inspection.objects.filter(pk=inspection.pk).delete()
    Inspection.objects.filter(pk=inspection.pk).update(state="invalid", invalidated_reason="typo")
    assert Inspection.objects.get().state == "invalid"


def test_database_excludes_overlapping_rentals(owner: Client, s: Scenario) -> None:
    issue(owner, s)
    rental = Rental.objects.get()

    with pytest.raises(IntegrityError), transaction.atomic():
        Rental.objects.create(
            company_id=rental.company_id,
            order_id=rental.order_id,
            vehicle_card_id=rental.vehicle_card_id,
            client_id=rental.client_id,
            tariff_id=rental.tariff_id,
            tariff_snapshot={},
            calc_timezone="UTC",
            issue_location_id=rental.issue_location_id,
            issue_place_kind="location",
            actual_start=rental.actual_start + timedelta(days=5),
            actual_period=(rental.actual_start + timedelta(days=5), None),
            state="active",
            created_by_user_id=rental.created_by_user_id,
        )


# --- Acceptance criterion 5: concurrent issues on real PostgreSQL ------------------------------


def _issue_directly(s: Scenario, client_id: str, vehicle_id: str, key_value: str) -> str:
    director = Employment.objects.get(position__is_top_authority=True)
    assert director.user_id is not None
    actor = Actor(
        user_id=director.user_id, company_id=director.company_id, employment_id=director.id
    )
    request = issue_module.IssueRequest(
        vehicle_card_id=UUID(vehicle_id),
        client_id=UUID(client_id),
        tariff_id=UUID(s.tariff),
        issue_location_id=UUID(s.location),
        place_kind="location",
        place_note="",
        actual_start=ISSUE_AT,
        odometer=Decimal("10000"),
        fuel=Decimal("8"),
        inspection_conducted=False,
    )

    def handler(ctx: CommandContext) -> CommandResult:
        rental, _, _ = issue_module.issue(ctx, request)
        return CommandResult(201, {"id": str(rental.id)})

    try:
        run_command(
            command="rental.issue",
            actor=actor,
            idempotency_key=key_value,
            payload={},
            handler=handler,
        )
    except ApiError as error:
        return error.code
    return "OK"


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_concurrent_issues_of_one_vehicle_50_times(owner: Client) -> None:
    s = build(owner)
    other_client = add_client(owner, s.cmp, "Anna", "+66899999999")
    for attempt in range(50):
        vehicle = s.vehicle if attempt == 0 else add_vehicle(owner, s, s.location, f"Car {attempt}")
        outcomes: list[str] = []

        def worker(
            client_id: str,
            n: int,
            vehicle_id: str = vehicle,
            results: list[str] = outcomes,
            round_no: int = attempt,
        ) -> None:
            try:
                results.append(_issue_directly(s, client_id, vehicle_id, f"c-{round_no}-{n}"))
            finally:
                connection.close()

        threads = [
            threading.Thread(target=worker, args=(s.client, 1)),
            threading.Thread(target=worker, args=(other_client, 2)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(outcomes) == ["OK", "VEHICLE_NOT_AVAILABLE"], (attempt, outcomes)
        assert Rental.objects.filter(vehicle_card_id=UUID(vehicle), state="active").count() == 1
    assert VehicleCard.objects.count() == 50


def test_preliminary_calculation_before_issue(owner: Client, s: Scenario) -> None:
    # § 13.6.6: the preliminary calculation and warnings are shown before confirming.
    body = {
        "vehicle_card_id": s.vehicle,
        "tariff_id": s.tariff,
        "client_id": s.client,
        "actual_start": ISSUE_AT.isoformat(),
        "planned_end": (ISSUE_AT + timedelta(days=2, minutes=1)).isoformat(),
    }

    quote = post(owner, f"/api/c/{s.cmp}/rentals/quote", body).json()

    assert quote["calculation"] == {
        "kind": "preliminary",
        "billable_minutes": 2881,
        "units": 3,
        "rate": "1000.0000",
        "base_amount": "3000.00",
        "currency": "THB",
    }
    assert quote["warnings"] == []
    assert not Rental.objects.exists()
    tariffs = owner.get(f"/api/c/{s.cmp}/vehicles/{s.vehicle}/tariffs").json()["tariffs"]
    assert [t["id"] for t in tariffs] == [s.tariff]
