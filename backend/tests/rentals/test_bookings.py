"""Bookings (ТЗ § 14.1, § 14.2.2, § 14.4.1, § 14.6.2, § 14.7.3, § 14.8.1, § 12.1.8)."""

from datetime import datetime, timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.orders.models import Order
from c4rsys.rentals import bookings
from c4rsys.rentals.models import Rental
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import Scenario, add_vehicle, build, issue_body

pytestmark = pytest.mark.django_db


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _at(days: float) -> datetime:
    base = timezone.now().replace(microsecond=0, second=0)
    return base + timedelta(days=days)


def _book(client: Client, s: Scenario, start: float = 1, end: float = 3, **data: Any) -> Any:
    body = {
        "vehicle_card_id": s.vehicle,
        "planned_start": _at(start).isoformat(),
        "planned_end": _at(end).isoformat(),
        **data,
    }
    return post(client, f"/api/c/{s.cmp}/bookings", body, key())


def _patch(client: Client, s: Scenario, rental: dict[str, Any], **data: Any) -> Any:
    return client.patch(
        f"/api/c/{s.cmp}/bookings/{rental['id']}",
        data={"expected_version": rental["version"], **data},
        content_type="application/json",
        headers=key(),
    )


def test_booking_after_a_call_holds_the_vehicle_without_a_client(
    owner: Client, s: Scenario
) -> None:
    # § 14.1.8–14.1.9, § 12.1.8.
    response = _book(owner, s, contact="+66 81 000 0000")

    assert response.status_code == 201, response.content
    rental = response.json()["rental"]
    assert rental["state"] == "booked"
    assert rental["booking"]["number"] == 1
    assert rental["booking"]["client_missing"] is True
    assert rental["calculation"] is None  # no tariff — no cost yet
    order = Order.objects.get(pk=rental["order_id"])
    assert order.client_id is None and order.is_internal is False


def test_bookings_of_one_vehicle_do_not_overlap(owner: Client, s: Scenario) -> None:
    _book(owner, s, 1, 3)

    overlapping = _book(owner, s, 2, 4)
    next_one = _book(owner, s, 3, 5)

    assert overlapping.json()["error"]["code"] == "VEHICLE_NOT_AVAILABLE"
    assert next_one.status_code == 201, next_one.content
    free = owner.get(
        f"/api/c/{s.cmp}/bookings/availability",
        {"vehicle_card_id": s.vehicle, "start": _at(5).isoformat(), "end": _at(6).isoformat()},
    ).json()
    assert free == {"free": True, "conflicts": []}


def test_details_added_later_and_issue_by_the_booking(owner: Client, s: Scenario) -> None:
    rental = _book(owner, s, 0.0, 2).json()["rental"]
    changed = _patch(
        owner,
        s,
        rental,
        client_id=s.client,
        tariff_id=s.tariff,
        issue_location_id=s.location,
        place_kind="location",
    )
    assert changed.status_code == 200, changed.content
    rental = changed.json()["rental"]
    assert rental["calculation"]["kind"] == "preliminary"
    assert Order.objects.get(pk=rental["order_id"]).client_id is not None

    body = issue_body(s, actual_start=(timezone.now() - timedelta(minutes=1)).isoformat())
    url = f"/api/c/{s.cmp}/rentals/issue"
    # There is no issue by a booking (decision of 16.11.2026): it becomes a rental first.
    direct = post(owner, url, {**body, "booking_id": rental["id"]}, key())
    assert direct.json()["error"]["code"] == "BOOKING_NOT_RENTAL"
    turned = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{rental['id']}/to-rental",
        {"expected_version": rental["version"], "without_inspection": True},
        key(),
    )
    assert turned.status_code == 200, turned.content
    issued = post(owner, url, {**body, "booking_id": rental["id"]}, key())

    assert issued.status_code == 201, issued.content
    active = issued.json()["rental"]
    # § 14.4.1: the same rental and order; the booking keeps its number.
    assert (active["id"], active["order_id"], active["state"]) == (
        rental["id"],
        rental["order_id"],
        "active",
    )
    assert active["booking"]["number"] == 1
    assert Rental.objects.count() == 1


def test_issue_without_the_booking_cannot_take_its_period(owner: Client, s: Scenario) -> None:
    _book(owner, s, -0.1, 1)

    body = issue_body(s, actual_start=(timezone.now() - timedelta(minutes=1)).isoformat())
    response = post(owner, f"/api/c/{s.cmp}/rentals/issue", body, key())

    assert response.json()["error"]["code"] == "VEHICLE_BOOKED"


def test_cancel_frees_the_period(owner: Client, s: Scenario) -> None:
    rental = _book(owner, s).json()["rental"]

    cancelled = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{rental['id']}/cancel",
        {"reason": "Client called", "expected_version": rental["version"]},
        key(),
    )

    assert cancelled.json()["rental"]["state"] == "cancelled"
    assert _book(owner, s).status_code == 201


def test_auto_cancel_at_no_show(owner: Client, s: Scenario) -> None:
    # § 14.8.1: off by default; the booking keeps the applied rule and moment.
    assert _book(owner, s, 1, 2).json()["rental"]["booking"]["auto_cancel_at"] is None
    put = owner.put(
        f"/api/c/{s.cmp}/rental-policy?location_id={s.location}",
        data={"enabled": True, "after_minutes": 30},
        content_type="application/json",
        headers=key(),
    )
    assert put.status_code == 200, put.content

    rental = _book(owner, s, 3, 4, issue_location_id=s.location).json()["rental"]
    planned = datetime.fromisoformat(rental["booking"]["planned_start"])
    assert datetime.fromisoformat(rental["booking"]["auto_cancel_at"]) == planned + timedelta(
        minutes=30
    )

    assert bookings.cancel_no_shows(planned + timedelta(minutes=29)) == 0
    assert bookings.cancel_no_shows(planned + timedelta(minutes=31)) == 1
    stored = Rental.objects.get(pk=rental["id"])
    assert (stored.state, stored.cancel_reason) == ("cancelled", "no_show")
    # Customer's decision of 16.11.2026: the booking was the only position of its order, so the
    # order is cancelled with it and does not stay open, forgotten.
    order = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    assert (order["state"], order["cancel_reason"]) == ("cancelled", "no_show")


def test_new_start_moves_the_auto_cancel_and_keeps_the_end(owner: Client, s: Scenario) -> None:
    owner.put(
        f"/api/c/{s.cmp}/rental-policy",
        data={"enabled": True, "after_minutes": 60},
        content_type="application/json",
        headers=key(),
    )
    rental = _book(owner, s, 1, 3).json()["rental"]

    moved = _patch(owner, s, rental, planned_start=_at(2).isoformat()).json()["rental"]

    assert moved["planned_end"] == rental["planned_end"]
    assert datetime.fromisoformat(moved["booking"]["auto_cancel_at"]) == _at(2) + timedelta(hours=1)


def test_deposit_of_a_booking_is_changed_before_the_issue(owner: Client, s: Scenario) -> None:
    rental = _book(owner, s, tariff_id=s.tariff).json()["rental"]
    url = f"/api/c/{s.cmp}/bookings/{rental['id']}/deposit"

    without_reason = post(
        owner, url, {"required": True, "amount": "3000", "expected_version": 1}, key()
    )
    changed = post(
        owner,
        url,
        {"required": True, "amount": "3000", "reason": "Regular client", "expected_version": 1},
        key(),
    )

    assert without_reason.status_code == 400
    assert changed.status_code == 200, changed.content
    deposit = changed.json()["rental"]["deposit"]
    assert deposit["required"] == "3000.00"


def test_late_return_marks_the_next_booking(owner: Client, s: Scenario) -> None:
    # § 14.6.2: the booking stays; a conflict is shown, nothing changes automatically.
    body = issue_body(
        s,
        actual_start=(timezone.now() - timedelta(days=2)).isoformat(),
        planned_end=(timezone.now() - timedelta(hours=1)).isoformat(),
    )
    post(owner, f"/api/c/{s.cmp}/rentals/issue", body, key())
    rental = _book(owner, s, 0.01, 1).json()["rental"]

    assert rental["booking"]["delay_conflict"]["overdue"] is True
    assert rental["state"] == "booked"


def test_another_vehicle_for_the_booking(owner: Client, s: Scenario) -> None:
    rental = _book(owner, s, tariff_id=s.tariff).json()["rental"]
    second = add_vehicle(owner, s, s.location, name="Honda City")
    response = owner.post(
        f"/api/c/{s.cmp}/vehicles",
        {"name": "Mazda 2", "location_id": s.location, "odometer_unit": "km"},
        content_type="application/json",
        headers=key(),
    )
    without_tariff = response.json()["vehicle"]["id"]

    refused = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{rental['id']}/vehicle",
        {"vehicle_card_id": without_tariff, "expected_version": rental["version"]},
        key(),
    )
    replaced = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{rental['id']}/vehicle",
        {"vehicle_card_id": second, "expected_version": rental["version"]},
        key(),
    )

    assert refused.json()["error"]["code"] == "TARIFF_NOT_AVAILABLE_FOR_VEHICLE"
    assert replaced.status_code == 200, replaced.content
    assert replaced.json()["rental"]["vehicle_card_id"] == second
    assert replaced.json()["rental"]["tariff_id"] == s.tariff


def test_booking_needs_its_right(owner: Client, s: Scenario) -> None:
    manager, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])
    driver, _ = hire(owner, s.cmp, "d@example.com", [{"type": "set", "set": "driver"}])

    assert _book(manager, s).status_code == 201
    assert _book(driver, s, 5, 6).status_code == 403


def test_rental_created_ahead_of_its_issue(owner: Client, s: Scenario) -> None:
    # Decision of 15.11.2026: a rental is created first and issued later from its card; it
    # holds the vehicle as a booking does, and needs a client and a tariff.
    assert _book(owner, s, direct=True).json()["error"]["code"] == "CLIENT_REQUIRED"
    assert _book(owner, s, direct=True, client_id=s.client).json()["error"]["code"] == (
        "TARIFF_REQUIRED"
    )

    response = _book(owner, s, direct=True, client_id=s.client, tariff_id=s.tariff)

    assert response.status_code == 201, response.content
    rental = response.json()["rental"]
    assert rental["state"] == "booked"
    assert rental["booking"]["direct"] is True
    assert _book(owner, s).json()["error"]["code"] == "VEHICLE_NOT_AVAILABLE"
    assert _book(owner, s, 5, 6).json()["rental"]["booking"]["direct"] is False


def test_rental_created_with_a_planned_inspection_unless_told_otherwise(
    owner: Client, s: Scenario
) -> None:
    # Decision of 15.11.2026: «without an inspection» is said before the rental is created;
    # otherwise an inspection at the issue is created with it, to be conducted later.
    from c4rsys.inspections.models import Inspection

    made = _book(owner, s, direct=True, client_id=s.client, tariff_id=s.tariff).json()["rental"]

    inspection = Inspection.objects.get(rental_id=made["id"])
    assert (inspection.kind, inspection.state) == ("issue", "planned")
    assert inspection.planned_at == Rental.objects.get(pk=made["id"]).planned_start

    bare = _book(
        owner, s, 5, 6, direct=True, client_id=s.client, tariff_id=s.tariff, without_inspection=True
    ).json()["rental"]
    assert not Inspection.objects.filter(rental_id=bare["id"]).exists()
    # A booking has no inspection of its own.
    booked = _book(owner, s, 8, 9).json()["rental"]
    assert not Inspection.objects.filter(rental_id=booked["id"]).exists()

    # Cancelling the rental cancels the inspection that was waiting.
    cancelled = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{made['id']}/cancel",
        {"reason": "client changed plans", "expected_version": made["version"]},
        key(),
    )
    assert cancelled.status_code == 200, cancelled.content
    inspection.refresh_from_db()
    assert inspection.state == "cancelled"


def test_issue_takes_the_readings_of_the_conducted_inspection(owner: Client, s: Scenario) -> None:
    # Decision of 16.11.2026: the odometer and the fuel are given in the inspection created
    # with the rental; the issue waits for it and asks for no readings of its own. A rental
    # created without an inspection gives them at the issue.
    from c4rsys.inspections.models import Inspection
    from tests.inspections.helpers import act, change, ok

    made = _book(owner, s, -0.1, 2, direct=True, client_id=s.client, tariff_id=s.tariff).json()[
        "rental"
    ]
    body = issue_body(
        s,
        actual_start=(timezone.now() - timedelta(minutes=1)).isoformat(),
        odometer=None,
        fuel=None,
    )
    url = f"/api/c/{s.cmp}/rentals/issue"

    waits = post(owner, url, {**body, "booking_id": made["id"]}, key())
    assert waits.json()["error"]["code"] == "ISSUE_INSPECTION_NOT_DONE"

    planned = Inspection.objects.get(rental_id=made["id"])
    item = {"id": str(planned.id), "version": planned.version}
    item = ok(act(owner, s, item, "start"))
    changed = change(owner, s, item, odometer="10000", fuel="8")
    assert changed.status_code == 200, changed.content
    ok(act(owner, s, changed.json()["inspection"], "complete", conducted=True))

    issued = post(owner, url, {**body, "booking_id": made["id"]}, key())
    assert issued.status_code == 201, issued.content
    assert issued.json()["inspection_id"] == str(planned.id)
    assert Inspection.objects.filter(rental_id=made["id"]).count() == 1


def test_issue_without_an_inspection_asks_for_the_readings(owner: Client, s: Scenario) -> None:
    # Decision of 16.11.2026: a rental created «without an inspection» gives the odometer and
    # the fuel at the issue.
    bare = _book(
        owner,
        s,
        -0.1,
        2,
        direct=True,
        client_id=s.client,
        tariff_id=s.tariff,
        without_inspection=True,
    ).json()["rental"]
    start = (timezone.now() - timedelta(minutes=1)).isoformat()
    url = f"/api/c/{s.cmp}/rentals/issue"

    asked = post(
        owner,
        url,
        {**issue_body(s, actual_start=start, odometer=None, fuel=None), "booking_id": bare["id"]},
        key(),
    )
    assert asked.json()["error"]["code"] == "ODOMETER_REQUIRED"

    issued = post(
        owner, url, {**issue_body(s, actual_start=start), "booking_id": bare["id"]}, key()
    )
    assert issued.status_code == 201, issued.content


def test_own_booking_charge_is_not_a_debt_at_the_issue(owner: Client, s: Scenario) -> None:
    # Fix of 16.11.2026: the cost of the booking is charged into its order at once; at the
    # issue it was shown as a debt of the client and counted against the payment.
    rental = _book(owner, s, 1, 3, client_id=s.client, tariff_id=s.tariff).json()["rental"]
    body = {
        "vehicle_card_id": s.vehicle,
        "tariff_id": s.tariff,
        "client_id": s.client,
        "booking_id": rental["id"],
        "actual_start": _at(1).isoformat(),
        "planned_end": _at(3).isoformat(),
    }

    quote = post(owner, f"/api/c/{s.cmp}/rentals/quote", body).json()

    codes = {w["code"]: w.get("amounts") for w in quote["warnings"]}
    assert "CLIENT_HAS_DEBT" not in codes
    assert codes["PAYMENT_INCOMPLETE"] == {"THB": quote["calculation"]["base_amount"]}


def test_booking_turned_into_a_rental_waiting_for_its_issue(owner: Client, s: Scenario) -> None:
    # Decision of 16.11.2026: from a booking the rental is made first, with its inspection at
    # the issue; the issue follows the inspection.
    from c4rsys.inspections.models import Inspection

    rental = _book(owner, s, 1, 3).json()["rental"]
    url = f"/api/c/{s.cmp}/bookings/{rental['id']}/to-rental"
    body = {"expected_version": rental["version"]}

    assert post(owner, url, body, key()).json()["error"]["code"] == "CLIENT_REQUIRED"

    done = post(owner, url, {**body, "client_id": s.client, "tariff_id": s.tariff}, key())

    assert done.status_code == 200, done.content
    made = done.json()["rental"]
    assert (made["state"], made["booking"]["direct"], made["client_id"]) == (
        "booked",
        True,
        s.client,
    )
    planned = Inspection.objects.get(rental_id=made["id"])
    assert (planned.kind, planned.state) == ("issue", "planned")

    # «Without an inspection» is said at that moment: no inspection is created.
    other = _book(owner, s, 5, 6, client_id=s.client, tariff_id=s.tariff).json()["rental"]
    bare = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{other['id']}/to-rental",
        {"expected_version": other["version"], "without_inspection": True},
        key(),
    )
    assert bare.status_code == 200, bare.content
    assert not Inspection.objects.filter(rental_id=other["id"]).exists()
