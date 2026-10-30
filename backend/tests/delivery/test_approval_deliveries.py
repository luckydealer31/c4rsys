"""Requests to the client for deliveries: a waiting pickup does not refuse the return, a cancelled
one withdraws its request, a move entered afterwards into an order in work waits (ТЗ § 12.3,
§ 15.3.3; part А6.2)."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.orders.models import OrderApproval
from c4rsys.rentals import returns as returns_module
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.delivery.test_deliveries import _create, _do, _ok
from tests.rentals.scenario import NOW, Scenario, add_vehicle, issue

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def more_frozen() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with mock.patch.object(returns_module, "timezone", clock):
        yield


def _driver(owner: Client, s: Scenario) -> str:
    driver: str = owner.get(f"/api/c/{s.cmp}/deliveries/drivers").json()["drivers"][0]["id"]
    return driver


def _in_work(owner: Client, s: Scenario) -> dict[str, Any]:
    response = owner.put(
        f"/api/c/{s.cmp}/order-policy",
        data={"require_approval": True},
        content_type="application/json",
        headers=key(),
    )
    assert response.status_code == 200, response.content
    issued = issue(owner, s)
    assert issued.status_code == 201, issued.content
    rental: dict[str, Any] = issued.json()["rental"]
    return rental


def _pickup(owner: Client, s: Scenario, rental: dict[str, Any]) -> dict[str, Any]:
    return _ok(
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


def test_waiting_pickup_does_not_refuse_the_return(owner: Client, s: Scenario) -> None:
    rental = _in_work(owner, s)
    pickup = _pickup(owner, s, rental)
    approval = OrderApproval.objects.get(item_type="delivery", item_id=pickup["id"])
    assert approval.state == "pending"
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
    # § 15.3.3: with a doubt the pickup is not started automatically; the return is done.
    assert returned.status_code == 200, returned.content
    found = owner.get(f"/api/c/{s.cmp}/deliveries/{pickup['id']}").json()["delivery"]
    assert found["state"] == "planned"


def test_cancelled_delivery_withdraws_its_request(owner: Client, s: Scenario) -> None:
    rental = _in_work(owner, s)
    pickup = _pickup(owner, s, rental)
    _ok(_do(owner, s, pickup, "cancel", reason="Not needed"))
    approval = OrderApproval.objects.get(item_type="delivery", item_id=pickup["id"])
    assert approval.state == "withdrawn"
    order = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    assert all(c["condition"] != "awaiting_client" for c in order["closing_conditions"])


def test_delivery_attached_to_an_order_in_work_waits(owner: Client, s: Scenario) -> None:
    rental = _in_work(owner, s)
    other = add_vehicle(owner, s, s.location, name="Honda Click")
    d = _ok(_create(owner, s, {"kind": "other", "address": "Kata"}, vehicle_card_id=other), 201)
    assert not OrderApproval.objects.filter(item_id=d["id"]).exists()  # an internal order
    attached = _do(owner, s, d, "attach", order_id=rental["order_id"])
    assert attached.status_code == 200, attached.content
    approval = OrderApproval.objects.get(item_type="delivery", item_id=d["id"])
    assert approval.state == "pending" and str(approval.order_id) == rental["order_id"]
