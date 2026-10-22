"""Completion of the rental formalization (ТЗ § 14.5.6–14.5.7; T14 done-criterion: without the
required readings the formalization is not completed)."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.platform.models import AuditEvent
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from c4rsys.rentals.models import Rental
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import NOW, Scenario, add_location, add_vehicle, build, issue

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


def _return(owner: Client, s: Scenario, rental: dict[str, Any], **extra: Any) -> Any:
    body = {
        "actual_end": NOW.isoformat(),
        "return_location_id": s.location,
        "expected_version": rental["version"],
        **extra,
    }
    return post(owner, f"/api/c/{s.cmp}/rentals/{rental['id']}/return", body, key())


def _complete(client: Client, s: Scenario, rental_id: str, version: int) -> Any:
    return post(
        client,
        f"/api/c/{s.cmp}/rentals/{rental_id}/complete-formalization",
        {"expected_version": version},
        key(),
    )


def test_completed_after_return_with_readings(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    returned = _return(owner, s, rental, odometer="10300", fuel="8").json()["rental"]
    assert returned["formalization"] == {"completed_at": None, "missing": []}

    response = _complete(owner, s, rental["id"], returned["version"])

    assert response.status_code == 200, response.content
    assert response.json()["rental"]["formalization"]["completed_at"] is not None
    assert AuditEvent.objects.filter(action="rental.formalization_completed").count() == 1
    order = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    assert order["operations"][0]["finished"] is True
    assert order["state"] == "ready_to_close"  # debt does not prevent it (§ 14.5.7)
    assert order["amounts"]["THB"]["to_pay"] == "2000.00"


def test_not_completed_without_return_readings(owner: Client, s: Scenario) -> None:
    # § 14.5.5–14.5.6: returned, but the return inspection is a draft.
    rental = issue(owner, s).json()["rental"]
    returned = _return(owner, s, rental).json()["rental"]
    assert returned["formalization"]["missing"] == ["return_inspection_not_saved"]

    refused = _complete(owner, s, rental["id"], returned["version"])

    assert refused.json()["error"]["code"] == "FORMALIZATION_INCOMPLETE"
    assert refused.json()["error"]["params"]["missing"] == ["return_inspection_not_saved"]
    assert Rental.objects.get().formalization_completed_at is None

    inspection = returned["inspections"]["return"]["id"]
    post(
        owner,
        f"/api/c/{s.cmp}/inspections/{inspection}/save",
        {"odometer": "10300", "fuel": "8"},
        key(),
    )
    assert _complete(owner, s, rental["id"], returned["version"]).status_code == 200


def test_active_rental_cannot_be_formalized(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]

    response = _complete(owner, s, rental["id"], rental["version"])

    assert response.json()["error"]["code"] == "RENTAL_NOT_RETURNED"


def test_completion_happens_once(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    returned = _return(owner, s, rental, odometer="10300", fuel="8").json()["rental"]
    _complete(owner, s, rental["id"], returned["version"])

    again = _complete(owner, s, rental["id"], returned["version"] + 1)
    stale = _complete(owner, s, rental["id"], returned["version"])

    assert again.json()["error"]["code"] == "FORMALIZATION_ALREADY_COMPLETED"
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"


def test_right_in_issue_or_acceptance_location(owner: Client, s: Scenario) -> None:
    # Decision 22.10.2026: the right in either location is enough; elsewhere it is refused.
    krabi = add_location(owner, s.cmp, "Krabi")
    samui = add_location(owner, s.cmp, "Samui")

    def staff(email: str, location: str) -> Client:
        client, _ = hire(
            owner,
            s.cmp,
            email,
            [
                {
                    "type": "grant",
                    "permission": "rental.complete_formalization",
                    "locations": [location],
                }
            ],
        )
        return client

    at_issue = staff("a@example.com", s.location)
    at_return = staff("b@example.com", krabi)
    elsewhere = staff("c@example.com", samui)
    second_vehicle = add_vehicle(owner, s, s.location, "Honda Click")

    for vehicle, completer in ((s.vehicle, at_return), (second_vehicle, at_issue)):
        rental = issue(owner, s, vehicle_card_id=vehicle).json()["rental"]
        returned = _return(
            owner, s, rental, odometer="10300", fuel="8", return_location_id=krabi
        ).json()["rental"]
        assert _complete(elsewhere, s, rental["id"], returned["version"]).status_code == 403
        assert _complete(completer, s, rental["id"], returned["version"]).status_code == 200
