"""Delivery and internal move, part Д1 (ТЗ § 15.1–15.5; customer's decisions Д-1…Д-11 of
29.10.2026)."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client

from c4rsys.fleet.models import VehicleCard
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import NOW, Scenario, issue

pytestmark = pytest.mark.django_db


def _at(hours: float) -> str:
    return (NOW + timedelta(hours=hours)).isoformat()


def _code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def _create(client: Client, s: Scenario, to: dict[str, Any], **body: Any) -> Any:
    data = {
        "vehicle_card_id": s.vehicle,
        "to": to,
        "planned_start": _at(1),
        "planned_end": _at(2),
        **body,
    }
    return post(client, f"/api/c/{s.cmp}/deliveries", data, key())


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.content
    d: dict[str, Any] = response.json()["delivery"]
    return d


def _do(client: Client, s: Scenario, d: dict[str, Any], action: str, **body: Any) -> Any:
    data = {"expected_version": d["version"], **body}
    return post(client, f"/api/c/{s.cmp}/deliveries/{d['id']}/{action}", data, key())


def _location(vehicle: str) -> str:
    return str(VehicleCard.objects.get(pk=vehicle).accounting_location_id)


def test_planned_internal_move(owner: Client, s: Scenario, patong: str) -> None:
    no_plan = _create(owner, s, {"kind": "location", "location_id": patong}, planned_start=None)
    assert _code(no_plan) == "FIELD_REQUIRED"
    d = _ok(_create(owner, s, {"kind": "location", "location_id": patong}), 201)
    assert d["state"] == "planned" and d["is_internal"] and d["from"]["location"] == s.location
    # § 15.1.3: the driver may come later, but the start needs one.
    assert _code(_do(owner, s, d, "start", actual_start=_at(-1))) == "DELIVERY_DRIVER_REQUIRED"
    driver = owner.get(f"/api/c/{s.cmp}/deliveries/drivers").json()["drivers"][0]["id"]
    d = _ok(_do(owner, s, d, "assign", driver_id=driver))
    d = _ok(_do(owner, s, d, "start", actual_start=_at(-1)))
    assert d["state"] == "in_transit"
    # In transit the vehicle stays bound to A and is held (§ 8.5.2; decision Д-3).
    assert _location(s.vehicle) == s.location
    assert _code(issue(owner, s, actual_start=NOW.isoformat())) == "VEHICLE_NOT_AVAILABLE"

    d = _ok(_do(owner, s, d, "finish", actual_end=NOW.isoformat(), result="accepted_in_location"))
    assert d["state"] == "completed"
    # Accepted in B: the vehicle is bound to B (decision Д-2).
    assert _location(s.vehicle) == patong
    order = owner.get(f"/api/c/{s.cmp}/orders/{d['order_id']}").json()["order"]
    assert order["closing_conditions"] == []


def test_delivery_to_a_client_address(owner: Client, s: Scenario, patong: str) -> None:
    other = {"kind": "other", "address": "Kata Beach Hotel"}
    order = post(owner, f"/api/c/{s.cmp}/orders", {"client_id": s.client}, key()).json()["order"]
    # «Other» belongs to the location the vehicle is bound to (§ 15.1.7, § 15.1.8).
    wrong = _create(owner, s, {**other, "location_id": patong}, order_id=order["id"])
    assert _code(wrong) == "DELIVERY_OTHER_OF_SOURCE"
    d = _ok(_create(owner, s, other, order_id=order["id"], driver_id=None), 201)
    assert d["to"]["location"] == s.location and not d["is_internal"]
    me = owner.get(f"/api/c/{s.cmp}/deliveries/drivers").json()["drivers"][0]["id"]
    d = _ok(_do(owner, s, d, "assign", driver_id=me))
    d = _ok(_do(owner, s, d, "start", actual_start=_at(-1)))
    d = _ok(_do(owner, s, d, "finish", actual_end=NOW.isoformat(), result="not_handed"))
    # § 15.4.1: the route is done, the client refused; the vehicle stays bound to A.
    assert d["result"] == "not_handed" and _location(s.vehicle) == s.location
    lines = owner.get(f"/api/c/{s.cmp}/orders/{order['id']}").json()["order"]["operations"]
    assert [op["type"] for op in lines] == ["delivery"]


def test_take_once_and_the_driver_sees_own_trips(owner: Client, s: Scenario, patong: str) -> None:
    taker, taker_id = hire(
        owner,
        s.cmp,
        "taker@example.com",
        [{"type": "grant", "permission": p} for p in ("delivery.take", "delivery.view")],
    )
    other, _ = hire(
        owner, s.cmp, "other@example.com", [{"type": "grant", "permission": "delivery.take"}]
    )
    driver, _ = hire(owner, s.cmp, "driver@example.com", [{"type": "set", "set": "driver"}])
    d = _ok(_create(owner, s, {"kind": "location", "location_id": patong}), 201)
    free = taker.get(f"/api/c/{s.cmp}/deliveries?free=1").json()["deliveries"]
    assert [x["id"] for x in free] == [d["id"]]
    d = _ok(_do(taker, s, d, "take"))
    assert d["driver_id"] == taker_id
    # § 15.5.1: the second one is told the move is already taken.
    assert _code(_do(other, s, d, "take")) == "DELIVERY_TAKEN"
    # The driver set sees only trips assigned to them (§ 23.2.5).
    assert driver.get(f"/api/c/{s.cmp}/deliveries").json()["deliveries"] == []
    assert _do(driver, s, d, "start", actual_start=_at(-1)).status_code == 404


def test_driver_runs_own_trip_and_overlaps_warn(owner: Client, s: Scenario, patong: str) -> None:
    driver, driver_id = hire(owner, s.cmp, "driver@example.com", [{"type": "set", "set": "driver"}])
    first = _ok(
        _create(owner, s, {"kind": "location", "location_id": patong}, driver_id=driver_id), 201
    )
    second = _ok(
        _create(
            owner,
            s,
            {"kind": "other", "address": "Airport"},
            driver_id=driver_id,
            planned_start=_at(1.5),
            planned_end=_at(3),
        ),
        201,
    )
    # § 15.2.7: overlapping assignments of one driver are shown, not refused.
    assert [o["number"] for o in second["driver_overlaps"]] == [first["number"]]
    mine = driver.get(f"/api/c/{s.cmp}/deliveries?mine=1").json()["deliveries"]
    assert {x["id"] for x in mine} == {first["id"], second["id"]}
    started = _ok(_do(driver, s, first, "start", actual_start=_at(-1)))
    _ok(
        _do(driver, s, started, "finish", actual_end=NOW.isoformat(), result="accepted_in_location")
    )
    assert _location(s.vehicle) == patong


def test_record_a_move_that_happened(owner: Client, s: Scenario, patong: str) -> None:
    me = owner.get(f"/api/c/{s.cmp}/deliveries/drivers").json()["drivers"][0]["id"]
    body = {
        "vehicle_card_id": s.vehicle,
        "to": {"kind": "location", "location_id": patong},
        "driver_id": me,
        "actual_start": _at(-2),
        "actual_end": _at(-1),
        "result": "accepted_in_location",
    }
    d = _ok(post(owner, f"/api/c/{s.cmp}/deliveries/record", body, key()), 201)
    assert d["state"] == "completed" and d["planned_start"] is None
    assert _location(s.vehicle) == patong


def test_cancel_terminate_and_order_cancel(owner: Client, s: Scenario, patong: str) -> None:
    d = _ok(_create(owner, s, {"kind": "location", "location_id": patong}), 201)
    assert _code(_do(owner, s, d, "cancel")) == "FIELD_REQUIRED"
    d = _ok(_do(owner, s, d, "cancel", reason="Not needed"))
    assert d["state"] == "cancelled"

    me = owner.get(f"/api/c/{s.cmp}/deliveries/drivers").json()["drivers"][0]["id"]
    d = _ok(_create(owner, s, {"kind": "location", "location_id": patong}, driver_id=me), 201)
    d = _ok(_do(owner, s, d, "start", actual_start=_at(-1)))
    d = _ok(_do(owner, s, d, "terminate", actual_end=NOW.isoformat(), reason="Breakdown"))
    assert d["state"] == "terminated" and _location(s.vehicle) == s.location

    planned = _ok(_create(owner, s, {"kind": "location", "location_id": patong}), 201)
    order = owner.get(f"/api/c/{s.cmp}/orders/{planned['order_id']}").json()["order"]
    cancelled = post(
        owner,
        f"/api/c/{s.cmp}/orders/{order['id']}/cancel",
        {"expected_version": order["version"], "reason": "No"},
        key(),
    )
    assert cancelled.status_code == 200, cancelled.content
    again = owner.get(f"/api/c/{s.cmp}/deliveries/{planned['id']}").json()["delivery"]
    assert again["state"] == "cancelled"


def test_booking_over_a_delivery_is_kept(owner: Client, s: Scenario, patong: str) -> None:
    _ok(
        _create(
            owner,
            s,
            {"kind": "location", "location_id": patong},
            planned_start=_at(30),
            planned_end=_at(40),
        ),
        201,
    )
    booking = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {"vehicle_card_id": s.vehicle, "planned_start": _at(35), "planned_end": _at(60)},
        key(),
    )
    # § 14.6.3: saved with a warning, not refused.
    assert booking.status_code == 201, booking.content


def test_attach_to_a_client_order(owner: Client, s: Scenario, patong: str) -> None:
    d = _ok(_create(owner, s, {"kind": "other", "address": "Kata"}), 201)
    order = post(owner, f"/api/c/{s.cmp}/orders", {"client_id": s.client}, key()).json()["order"]
    d = _ok(_do(owner, s, d, "attach", order_id=order["id"]))
    assert d["order_id"] == order["id"] and not d["is_internal"]
