"""Deactivation of a location and switching off its rental context with rentals and vehicles
(ТЗ § 4.4.1–4.4.2; decisions 23.10.2026: unreturned and unformalized rentals block, a location
without history may be deleted)."""

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.platform.models import AuditEvent
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.rentals.scenario import (
    ISSUE_AT,
    NOW,
    Scenario,
    add_location,
    add_vehicle,
    build,
    issue,
)

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


def _location(owner: Client, s: Scenario, location: str) -> dict[str, Any]:
    body: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/locations/{location}").json()["location"]
    return body


def _deactivate(owner: Client, s: Scenario, location: str) -> Any:
    version = _location(owner, s, location)["version"]
    return post(
        owner,
        f"/api/c/{s.cmp}/locations/{location}/deactivate",
        {"expected_version": version},
        key(),
    )


def _restore(owner: Client, s: Scenario, location: str) -> Any:
    version = _location(owner, s, location)["version"]
    return post(
        owner, f"/api/c/{s.cmp}/locations/{location}/restore", {"expected_version": version}, key()
    )


def _rental_context(owner: Client, s: Scenario, location: str) -> str:
    contexts = _location(owner, s, location)["contexts"]
    context: str = next(c["id"] for c in contexts if c["type"] == "rental")
    return context


def _return(owner: Client, s: Scenario, rental: dict[str, Any], location: str) -> Any:
    body = {
        "actual_end": NOW.isoformat(),
        "return_location_id": location,
        "odometer": "10300",
        "fuel": "8",
        "expected_version": rental["version"],
    }
    return post(owner, f"/api/c/{s.cmp}/rentals/{rental['id']}/return", body, key())


def _complete(owner: Client, s: Scenario, rental: dict[str, Any]) -> Any:
    return post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/complete-formalization",
        {"expected_version": rental["version"]},
        key(),
    )


def test_rentals_and_vehicles_block_until_finished_and_moved(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    refused = _deactivate(owner, s, s.location)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "LOCATION_HAS_UNFINISHED"
    assert set(refused.json()["error"]["params"]["blockers"]) == {
        "rentals_not_finished",
        "vehicles_in_location",
    }

    # Returned to another location: the vehicle moves there, the rental is not formalized yet.
    krabi = add_location(owner, s.cmp, "Krabi")
    returned = _return(owner, s, rental, krabi).json()["rental"]
    still = _deactivate(owner, s, s.location).json()["error"]
    assert still["params"]["blockers"] == ["rentals_not_finished"]

    assert _complete(owner, s, returned).status_code == 200
    done = _deactivate(owner, s, s.location)
    assert done.status_code == 200, done.content
    assert done.json()["location"]["state"] == "deactivated"
    assert AuditEvent.objects.filter(action="location.deactivated").count() == 1


def test_deactivated_location_accepts_no_new_operations_until_restored(
    owner: Client, s: Scenario
) -> None:
    krabi = add_location(owner, s.cmp, "Krabi")
    assert _deactivate(owner, s, krabi).status_code == 200
    rental = issue(owner, s).json()["rental"]

    refused = _return(owner, s, rental, krabi)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "LOCATION_DEACTIVATED"
    second = add_vehicle(owner, s, s.location, "Honda City")
    planned = issue(owner, s, planned_return_location_id=krabi, vehicle_card_id=second)
    assert planned.json()["error"]["code"] == "LOCATION_DEACTIVATED"

    restored = _restore(owner, s, krabi)
    assert restored.status_code == 200
    assert restored.json()["location"]["state"] != "deactivated"
    assert _return(owner, s, rental, krabi).status_code == 200
    assert AuditEvent.objects.filter(action="location.restored").count() == 1


def test_location_with_history_is_not_deleted(owner: Client, s: Scenario) -> None:
    issue(owner, s)

    response = owner.delete(f"/api/c/{s.cmp}/locations/{s.location}", headers=key())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LOCATION_HAS_HISTORY"
    assert _location(owner, s, s.location)["name"] == "Phuket"


def test_rental_context_is_switched_off_after_rentals_finish_and_vehicles_stay(
    owner: Client, s: Scenario
) -> None:
    rental = issue(owner, s).json()["rental"]
    context = _rental_context(owner, s, s.location)
    path = f"/api/c/{s.cmp}/locations/{s.location}/contexts/{context}"

    refused = post(owner, f"{path}/disable", {}, key())
    assert refused.json()["error"]["code"] == "CONTEXT_HAS_UNFINISHED"

    returned = _return(owner, s, rental, s.location).json()["rental"]
    _complete(owner, s, returned)
    # § 4.4.2: vehicles keep their location and do not block switching off a context.
    disabled = post(owner, f"{path}/disable", {}, key())
    assert disabled.status_code == 200, disabled.content
    assert disabled.json()["location"]["contexts"][0]["state"] == "disabled"

    later = issue(owner, s, actual_start=(ISSUE_AT + timedelta(days=2)).isoformat())
    assert later.status_code != 201
    assert post(owner, f"{path}/enable", {}, key()).status_code == 200


def test_rental_cannot_be_switched_off_for_a_vehicle_with_an_active_rental(owner: Client) -> None:
    # Decision of 24.10.2026: a context of a vehicle is switched off only without unfinished
    # operations in it.
    s = build(owner)
    issued = issue(owner, s)
    assert issued.status_code == 201, issued.content
    version = owner.get(f"/api/c/{s.cmp}/vehicles/{s.vehicle}").json()["vehicle"]["version"]

    response = owner.patch(
        f"/api/c/{s.cmp}/vehicles/{s.vehicle}",
        data={"contexts": [], "expected_version": version},
        content_type="application/json",
        headers=key(),
    )

    assert response.json()["error"]["code"] == "VEHICLE_CONTEXT_HAS_UNFINISHED"
    assert response.json()["error"]["params"]["blockers"] == ["rentals_not_finished"]
