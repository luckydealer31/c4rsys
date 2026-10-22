"""Return of a vehicle (ТЗ § 8.5.4, § 13.5.1, § 14.5.1, § 14.5.3, § 14.5.5–14.5.6; examples
base, E01, E02; acceptance criteria 2 and 4)."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from c4rsys.finance.models import ChargeEntry
from c4rsys.fleet.models import OdometerReading, VehicleCard
from c4rsys.inspections.models import Inspection
from c4rsys.platform.models import AuditEvent
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from c4rsys.rentals.models import Rental
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import BKK, ISSUE_AT, NOW, Scenario, add_location, build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def frozen_clock() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with (
        mock.patch.object(issue_module, "timezone", clock),
        mock.patch.object(returns_module, "timezone", clock),
    ):
        yield


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


@pytest.fixture
def rental(owner: Client, s: Scenario) -> dict[str, Any]:
    body: dict[str, Any] = issue(owner, s).json()["rental"]
    return body


def _return(
    client: Client,
    s: Scenario,
    rental: dict[str, Any],
    headers: dict[str, str] | None = None,
    **overrides: Any,
) -> Any:
    body: dict[str, Any] = {
        "actual_end": NOW.isoformat(),
        "return_location_id": s.location,
        "odometer": "10300",
        "fuel": "8",
        "expected_version": rental["version"],
    }
    body.update(overrides)
    body = {k: v for k, v in body.items() if v is not None}
    return post(client, f"/api/c/{s.cmp}/rentals/{rental['id']}/return", body, headers or key())


def _order(owner: Client, s: Scenario, rental: dict[str, Any]) -> dict[str, Any]:
    order: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    return order


def test_base_example_return_charges_2000(
    owner: Client, s: Scenario, rental: dict[str, Any]
) -> None:
    # Base example: 1 Nov 10:00 → 3 Nov 10:00, 2 880 minutes, 2 days, 2 000 THB.
    response = _return(owner, s, rental)

    assert response.status_code == 200, response.content
    body = response.json()
    returned = body["rental"]
    assert returned["state"] == "returned"
    assert returned["actual_end"] == "2026-11-03T03:00:00+00:00"
    assert returned["calculation"] == {
        "kind": "actual",
        "end": "2026-11-03T03:00:00+00:00",
        "billable_minutes": 2880,
        "units": 2,
        "rate": "1000.0000",
        "base_amount": "2000.00",
        "currency": "THB",
    }
    assert body["inspection_state"] == "saved"
    charge = ChargeEntry.objects.get()
    assert (charge.amount, charge.currency_id, charge.source_type) == (
        Decimal("2000.00"),
        "THB",
        "rental_base",
    )
    assert _order(owner, s, rental)["amounts"] == {
        "THB": {"charged": "2000.00", "paid": "0.00", "to_pay": "2000.00", "overpaid": "0.00"}
    }
    assert OdometerReading.objects.order_by("measured_at").last().value == Decimal("10300")  # type: ignore[union-attr]
    actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"rental.returned", "charge.recorded", "inspection.saved"} <= actions


@pytest.mark.parametrize(
    ("end", "amount"),
    [
        (datetime(2026, 11, 2, 10, 0, tzinfo=BKK), "1000.00"),  # E01: early return, 24 h
        (datetime(2026, 11, 2, 10, 1, tzinfo=BKK), "2000.00"),  # E02: 24 h 1 min
    ],
)
def test_examples_e01_e02(
    owner: Client, s: Scenario, rental: dict[str, Any], end: datetime, amount: str
) -> None:
    # § 14.5.3: the actual period is priced; the planned end does not matter.
    _return(owner, s, rental, actual_end=end.isoformat())

    assert ChargeEntry.objects.get().amount == Decimal(amount)


def test_return_without_readings_leaves_draft_then_readings_save_it(
    owner: Client, s: Scenario, rental: dict[str, Any]
) -> None:
    # § 14.5.5, § 14.5.6: returned, but the inspection is incomplete.
    response = _return(owner, s, rental, odometer=None, fuel=None)

    assert response.status_code == 200, response.content
    assert response.json()["rental"]["state"] == "returned"
    assert response.json()["inspection_state"] == "draft"
    assert ChargeEntry.objects.get().amount == Decimal("2000.00")
    assert OdometerReading.objects.count() == 1  # only the issue reading
    inspection_id = response.json()["inspection_id"]

    saved = post(
        owner,
        f"/api/c/{s.cmp}/inspections/{inspection_id}/save",
        {"odometer": "10300", "fuel": "7.5"},
        key(),
    )
    assert saved.status_code == 200, saved.content
    inspections = saved.json()["rental"]["inspections"]
    assert inspections["return"]["state"] == "saved"
    assert inspections["fuel_differs"] is True  # § 13.5.1: shown, not converted to money
    assert OdometerReading.objects.count() == 2
    again = post(
        owner,
        f"/api/c/{s.cmp}/inspections/{inspection_id}/save",
        {"odometer": "10400", "fuel": "7"},
        key(),
    )
    assert again.json()["error"]["code"] == "INSPECTION_NOT_DRAFT"


def test_acceptance_in_another_location_moves_the_vehicle(
    owner: Client, s: Scenario, rental: dict[str, Any]
) -> None:
    # § 8.5.4.
    krabi = add_location(owner, s.cmp, "Krabi")

    _return(owner, s, rental, return_location_id=krabi)

    assert str(VehicleCard.objects.get().accounting_location_id) == krabi
    assert AuditEvent.objects.filter(action="vehicle.location_changed").count() == 1


def test_other_place_keeps_the_issue_location(
    owner: Client, s: Scenario, rental: dict[str, Any]
) -> None:
    # § 8.5.4: «other» is an outside address of the issue location.
    krabi = add_location(owner, s.cmp, "Krabi")
    wrong = _return(
        owner, s, rental, place_kind="other", place_note="Hotel", return_location_id=krabi
    )
    assert wrong.json()["error"]["code"] == "OTHER_PLACE_OF_ISSUE_LOCATION"

    ok = _return(owner, s, rental, place_kind="other", place_note="Hotel lobby")

    assert ok.json()["rental"]["return_place_kind"] == "other"
    assert str(VehicleCard.objects.get().accounting_location_id) == s.location


def test_only_staff_of_the_acceptance_location_accept(owner: Client, s: Scenario) -> None:
    # Decision 22.10.2026: any planned return place may be named at issue; acceptance needs
    # rights in the location of acceptance.
    krabi = add_location(owner, s.cmp, "Krabi")
    phuket_manager, _ = hire(
        owner,
        s.cmp,
        "p@example.com",
        [{"type": "set", "set": "rental_manager", "locations": [s.location]}],
    )
    krabi_manager, _ = hire(
        owner,
        s.cmp,
        "k@example.com",
        [{"type": "set", "set": "rental_manager", "locations": [krabi]}],
    )
    issued = issue(phuket_manager, s, planned_return_location_id=krabi)
    assert issued.status_code == 201, issued.content
    rental = issued.json()["rental"]
    assert rental["planned_return_location_id"] == krabi

    denied = _return(phuket_manager, s, rental, return_location_id=krabi)
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    accepted = _return(krabi_manager, s, rental, return_location_id=krabi)
    assert accepted.status_code == 200, accepted.content


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"actual_end": (NOW + timedelta(minutes=1)).isoformat()}, "FUTURE_TIME_NOT_ALLOWED"),
        ({"actual_end": ISSUE_AT.isoformat()}, "PERIOD_INVALID"),
        ({"odometer": "9999"}, "ODOMETER_SEQUENCE_CONFLICT"),
        ({"expected_version": 99}, "VERSION_CONFLICT"),
    ],
)
def test_refusals_post_nothing(
    owner: Client, s: Scenario, rental: dict[str, Any], overrides: dict[str, Any], code: str
) -> None:
    response = _return(owner, s, rental, **overrides)

    assert response.json()["error"]["code"] == code
    assert Rental.objects.get().state == "active"
    assert not ChargeEntry.objects.exists()
    assert Inspection.objects.count() == 1


def test_second_return_is_refused(owner: Client, s: Scenario, rental: dict[str, Any]) -> None:
    _return(owner, s, rental)

    again = _return(owner, s, rental, expected_version=rental["version"] + 1)

    assert again.json()["error"]["code"] == "RENTAL_NOT_ACTIVE"
    assert ChargeEntry.objects.count() == 1


def test_repeat_with_same_key_returns_once(
    owner: Client, s: Scenario, rental: dict[str, Any]
) -> None:
    # Acceptance criterion 4.
    headers = {"Idempotency-Key": "return-1"}
    first = _return(owner, s, rental, headers)
    again = _return(owner, s, rental, headers)

    assert again.json() == first.json()
    assert ChargeEntry.objects.count() == 1


def test_journal_failure_posts_nothing(owner: Client, s: Scenario, rental: dict[str, Any]) -> None:
    with mock.patch(
        "c4rsys.platform.models.AuditEvent.objects.bulk_create",
        side_effect=DatabaseError("journal unavailable"),
    ):
        failed = _return(owner, s, rental)

    assert failed.json()["error"]["code"] == "OPERATION_NOT_POSTED"
    assert Rental.objects.get().state == "active"
    assert not ChargeEntry.objects.exists()


def test_vehicle_can_be_issued_again_after_return(
    owner: Client, s: Scenario, rental: dict[str, Any]
) -> None:
    # § 14.6.4–14.6.5: the actual return ends the occupation.
    _return(owner, s, rental, actual_end=datetime(2026, 11, 2, 10, 0, tzinfo=BKK).isoformat())

    before_return = issue(
        owner, s, actual_start=datetime(2026, 11, 2, 9, 0, tzinfo=BKK).isoformat(), odometer="10300"
    )
    after_return = issue(
        owner,
        s,
        actual_start=datetime(2026, 11, 2, 12, 0, tzinfo=BKK).isoformat(),
        odometer="10300",
    )

    assert before_return.json()["error"]["code"] == "VEHICLE_NOT_AVAILABLE"
    assert after_return.status_code == 201, after_return.content


def test_charges_are_append_only(owner: Client, s: Scenario, rental: dict[str, Any]) -> None:
    _return(owner, s, rental)

    with pytest.raises(DatabaseError), transaction.atomic():
        ChargeEntry.objects.update(amount=1)
    with pytest.raises(DatabaseError), transaction.atomic():
        ChargeEntry.objects.all().delete()
