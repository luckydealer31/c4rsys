"""Internal move of a vehicle recorded after it happened (ТЗ § 8.5.2, § 15.1.3–15.1.5,
§ 23.6.2; decision 23.10.2026: one action, the right to move in «Company administrator» and
«Rental manager»)."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.fleet.models import VehicleCard, VehicleMove
from c4rsys.orders.models import Order
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import Scenario, add_location, build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


def _employment(client: Client, cmp: str) -> str:
    employment: str = client.get(f"/api/c/{cmp}").json()["company"]["employment"]["id"]
    return employment


def _move(client: Client, s: Scenario, to: str, driver: str, **overrides: Any) -> Any:
    end = timezone.now() - timedelta(hours=1)
    body: dict[str, Any] = {
        "to_location_id": to,
        "driver_employment_id": driver,
        "actual_start": (end - timedelta(hours=2)).isoformat(),
        "actual_end": end.isoformat(),
    }
    body.update(overrides)
    return post(client, f"/api/c/{s.cmp}/vehicles/{s.vehicle}/move", body, key())


def test_move_changes_the_location_inside_an_internal_order(owner: Client, s: Scenario) -> None:
    krabi = add_location(owner, s.cmp, "Krabi")
    driver = _employment(owner, s.cmp)

    response = _move(owner, s, krabi, driver, note="by road")

    assert response.status_code == 201, response.content
    assert response.json()["vehicle"]["location_id"] == krabi
    move = VehicleMove.objects.get()
    assert (str(move.from_location_id), str(move.to_location_id)) == (s.location, krabi)
    order = Order.objects.get(pk=move.order_id)
    assert order.is_internal and order.client_id is None
    detail = owner.get(f"/api/c/{s.cmp}/orders/{order.id}").json()["order"]
    assert detail["operations"] == [
        {"type": "move", "id": str(move.id), "state": "completed", "finished": True}
    ]
    assert detail["state"] == "ready_to_close"
    actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"vehicle.moved", "vehicle.location_changed", "order.created"} <= actions
    moves = owner.get(f"/api/c/{s.cmp}/vehicles/{s.vehicle}/moves").json()["moves"]
    assert moves[0]["note"] == "by road" and moves[0]["driver"]["employment_id"] == driver


def test_internal_order_of_a_move_closes_and_keeps_its_history(owner: Client, s: Scenario) -> None:
    krabi = add_location(owner, s.cmp, "Krabi")
    order_id = _move(owner, s, krabi, _employment(owner, s.cmp)).json()["move"]["order_id"]

    closed = post(owner, f"/api/c/{s.cmp}/orders/{order_id}/close", {"expected_version": 1}, key())

    assert closed.status_code == 200, closed.content
    history = owner.get(f"/api/c/{s.cmp}/orders/{order_id}/history").json()["history"]
    assert {"order.created", "vehicle.moved", "order.closed"} <= {e["action"] for e in history}


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("same", "MOVE_SAME_LOCATION"),
        ("future", "FUTURE_TIME_NOT_ALLOWED"),
        ("period", "PERIOD_INVALID"),
        ("driver", "DRIVER_UNKNOWN"),
    ],
)
def test_refusals_change_nothing(owner: Client, s: Scenario, case: str, code: str) -> None:
    krabi = add_location(owner, s.cmp, "Krabi")
    driver = _employment(owner, s.cmp)
    now = timezone.now()
    variants: dict[str, tuple[str, str, dict[str, str]]] = {
        "same": (s.location, driver, {}),
        "future": (krabi, driver, {"actual_end": (now + timedelta(hours=1)).isoformat()}),
        "period": (krabi, driver, {"actual_start": (now - timedelta(minutes=30)).isoformat()}),
        "driver": (krabi, "01a1628f-ca1c-71ab-a48b-80eede8d99b1", {}),
    }
    to, who, overrides = variants[case]

    response = _move(owner, s, to, who, **overrides)

    assert response.json()["error"]["code"] == code
    assert not VehicleMove.objects.exists()
    assert not Order.objects.exists()
    assert str(VehicleCard.objects.get().accounting_location_id) == s.location


def test_deactivated_destination_is_refused(owner: Client, s: Scenario) -> None:
    krabi = add_location(owner, s.cmp, "Krabi")
    post(owner, f"/api/c/{s.cmp}/locations/{krabi}/deactivate", {"expected_version": 1}, key())

    response = _move(owner, s, krabi, _employment(owner, s.cmp))

    assert response.json()["error"]["code"] == "LOCATION_DEACTIVATED"


def test_vehicle_in_active_rental_is_not_moved(owner: Client, s: Scenario) -> None:
    issued = issue(owner, s, actual_start=(timezone.now() - timedelta(days=1)).isoformat())
    assert issued.status_code == 201, issued.content
    krabi = add_location(owner, s.cmp, "Krabi")

    response = _move(owner, s, krabi, _employment(owner, s.cmp))

    assert response.json()["error"]["code"] == "VEHICLE_BUSY"
    assert response.json()["error"]["params"]["reasons"] == ["in_rental"]


def test_rental_manager_moves_and_the_driver_set_does_not(owner: Client, s: Scenario) -> None:
    krabi = add_location(owner, s.cmp, "Krabi")
    manager, _ = hire(
        owner,
        s.cmp,
        "m@example.com",
        [{"type": "set", "set": "rental_manager", "locations": [s.location]}],
    )
    driver, _ = hire(owner, s.cmp, "d@example.com", [{"type": "set", "set": "driver"}])
    driver_id = _employment(driver, s.cmp)

    assert _move(driver, s, krabi, driver_id).status_code == 404
    names = {d["employment_id"] for d in manager.get(f"/api/c/{s.cmp}/drivers").json()["drivers"]}
    assert driver_id in names
    # § 15.1.4: the destination does not need access to it.
    assert _move(manager, s, krabi, driver_id).status_code == 201


def test_location_can_be_deactivated_after_its_vehicle_is_moved(owner: Client, s: Scenario) -> None:
    krabi = add_location(owner, s.cmp, "Krabi")
    _move(owner, s, krabi, _employment(owner, s.cmp))
    version = owner.get(f"/api/c/{s.cmp}/locations/{s.location}").json()["location"]["version"]

    response = post(
        owner,
        f"/api/c/{s.cmp}/locations/{s.location}/deactivate",
        {"expected_version": version},
        key(),
    )

    assert response.status_code == 200, response.content
