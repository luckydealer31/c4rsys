"""Delivery, part Д3: transitions with rentals and the service inside one order (ТЗ § 14.6.3,
§ 15.3.1–15.3.3, § 16.2.8; customer's decision Д-7 of 29.10.2026)."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.fleet.models import VehicleCard
from c4rsys.rentals import returns as returns_module
from c4rsys.service import operations as operations_module
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.delivery.test_deliveries import _at, _create, _do, _ok
from tests.rentals.scenario import NOW, Scenario, issue

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def more_frozen() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with (
        mock.patch.object(returns_module, "timezone", clock),
        mock.patch.object(operations_module, "timezone", clock),
    ):
        yield


def _driver(owner: Client, s: Scenario) -> str:
    driver: str = owner.get(f"/api/c/{s.cmp}/deliveries/drivers").json()["drivers"][0]["id"]
    return driver


def _delivery(owner: Client, s: Scenario, delivery_id: str) -> dict[str, Any]:
    d: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/deliveries/{delivery_id}").json()["delivery"]
    return d


def test_issue_completes_the_delivery_on_the_way(owner: Client, s: Scenario) -> None:
    order = post(owner, f"/api/c/{s.cmp}/orders", {"client_id": s.client}, key()).json()["order"]
    to = {"kind": "other", "address": "Kata Beach Hotel"}
    d = _ok(_create(owner, s, to, order_id=order["id"], driver_id=_driver(owner, s)), 201)
    d = _ok(_do(owner, s, d, "start", actual_start=_at(-1)))
    issued = issue(
        owner,
        s,
        actual_start=NOW.isoformat(),
        order_id=order["id"],
        place_kind="other",
        place_note="Kata Beach Hotel",
    )
    assert issued.status_code == 201, issued.content
    d = _delivery(owner, s, d["id"])
    # The handover to the client completes the delivery at the moment of the issue (§ 14.6.3).
    assert d["state"] == "completed" and d["result"] == "handed_to_client"
    assert d["actual_end"] == issued.json()["rental"]["actual_start"]


def test_a_delivery_of_another_order_keeps_the_issue_refused(owner: Client, s: Scenario) -> None:
    to = {"kind": "other", "address": "Kata Beach Hotel"}
    d = _ok(_create(owner, s, to, driver_id=_driver(owner, s)), 201)
    _ok(_do(owner, s, d, "start", actual_start=_at(-1)))
    # § 15.3.2: operations of different orders do not change each other.
    refused = issue(owner, s, actual_start=NOW.isoformat(), place_kind="other", place_note="Kata")
    assert refused.json()["error"]["code"] == "VEHICLE_NOT_AVAILABLE"


def test_return_at_an_address_starts_the_pickup(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    pickup = _ok(
        _create(
            owner,
            s,
            {"kind": "location", "location_id": s.location},
            **{"from": {"kind": "other", "address": "Kata"}},
            order_id=rental["order_id"],
            driver_id=_driver(owner, s),
        ),
        201,
    )
    returned = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
        {
            "actual_end": NOW.isoformat(),
            "return_location_id": s.location,
            "place_kind": "other",
            "place_note": "Kata",
            "odometer": "10300",
            "fuel": "8",
            "expected_version": rental["version"],
        },
        key(),
    )
    assert returned.status_code == 200, returned.content
    pickup = _delivery(owner, s, pickup["id"])
    # § 15.3.3: the handover by the client starts the pickup of the same order.
    assert pickup["state"] == "in_transit" and pickup["actual_start"] is not None


def test_service_acceptance_completes_the_move(owner: Client, s: Scenario, patong: str) -> None:
    post(owner, f"/api/c/{s.cmp}/locations/{patong}/contexts", {"type": "service"}, key())
    work = post(
        owner,
        f"/api/c/{s.cmp}/service/works",
        {"name": "Oil", "pricing": "fixed", "price": "1"},
        key(),
    ).json()["work"]
    op = post(
        owner,
        f"/api/c/{s.cmp}/service/operations",
        {
            "vehicle_card_id": s.vehicle,
            "location_id": patong,
            "reason": "Service",
            "internal": True,
            "work_ids": [work["id"]],
        },
        key(),
    ).json()["operation"]
    d = _ok(
        _create(
            owner,
            s,
            {"kind": "location", "location_id": patong},
            order_id=op["order_id"],
            driver_id=_driver(owner, s),
        ),
        201,
    )
    _ok(_do(owner, s, d, "start", actual_start=_at(-1)))
    accepted = post(
        owner,
        f"/api/c/{s.cmp}/service/operations/{op['id']}/accept",
        {"expected_version": op["version"], "accepted_at": NOW.isoformat()},
        key(),
    )
    assert accepted.status_code == 200, accepted.content
    # § 16.2.8: the acceptance completes the delivery; the vehicle is bound to the service place.
    assert _delivery(owner, s, d["id"])["result"] == "accepted_in_location"
    assert str(VehicleCard.objects.get(pk=s.vehicle).accounting_location_id) == patong
