"""Place and policies of the issue, exceptions (ТЗ § 14.2.1, § 14.3, § 10.3.3, § 23.4.7;
customer's decisions В1–В9 of 26.10.2026)."""

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.inspections.models import Inspection
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from c4rsys.rentals.models import IssueException, Rental
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import ISSUE_AT, NOW, Scenario, add_client, add_location, build, issue

pytestmark = pytest.mark.django_db

PLANNED_END = (ISSUE_AT + timedelta(days=2)).isoformat()


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


def _policy(client: Client, s: Scenario, location: str | None = None, **values: str) -> Any:
    body = {"debt": "warn", "payment": "warn", "documents": "none", "inspection": "not_required"}
    query = f"?location_id={location}" if location else ""
    return client.put(
        f"/api/c/{s.cmp}/issue-policy{query}",
        data={**body, **values},
        content_type="application/json",
        headers=key(),
    )


def _decide(client: Client, s: Scenario, rental: dict[str, Any], decision: str) -> Any:
    return post(
        client,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/issue-exception/decide",
        {"decision": decision, "reason": "Checked", "expected_version": rental["version"]},
        key(),
    )


def test_other_place_is_described_by_address_coordinates_or_comment(
    owner: Client, s: Scenario
) -> None:
    # § 14.2.1, В1: at least one of them; coordinates come in pairs.
    empty = issue(owner, s, place_kind="other")
    half = issue(owner, s, place_kind="other", place_lat="7.8804")
    issued = issue(
        owner,
        s,
        place_kind="other",
        place_address="Hotel Kata, room 12",
        place_lat="7.8804",
        place_lng="98.3923",
    )

    assert empty.json()["error"]["code"] == "PLACE_NOT_DESCRIBED"
    assert half.json()["error"]["code"] == "COORDINATES_INCOMPLETE"
    assert issued.status_code == 201, issued.content
    rental = issued.json()["rental"]
    assert (rental["issue_place_kind"], rental["issue_place_address"]) == (
        "other",
        "Hotel Kata, room 12",
    )
    assert (rental["issue_place_lat"], rental["issue_place_lng"]) == ("7.880400", "98.392300")
    # «Other» belongs to the issue location (§ 8.5.4).
    assert rental["issue_location_id"] == s.location


def test_start_policies_only_warn(owner: Client, s: Scenario) -> None:
    # § 14.3.6: unpaid rental and missing check do not block a new company's issue.
    response = issue(owner, s, planned_end=PLANNED_END)

    assert response.status_code == 201, response.content
    assert response.json()["warnings"] == [
        {"code": "PAYMENT_INCOMPLETE", "amounts": {"THB": "2000.00"}}
    ]
    assert Inspection.objects.get().check_status == "not_required"
    policy = owner.get(f"/api/c/{s.cmp}/issue-policy").json()["policy"]
    assert policy["effective"] == {
        "debt": "warn",
        "payment": "warn",
        "documents": "none",
        "inspection": "not_required",
        "source": "default",
    }


def test_required_payment_blocks_until_an_exception(owner: Client, s: Scenario) -> None:
    assert _policy(owner, s, payment="require").status_code == 200
    manager, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])

    refused = issue(manager, s, planned_end=PLANNED_END)
    advance = issue(
        manager, s, planned_end=PLANNED_END, exception_mode="advance", exception_reason="x"
    )
    recorded = issue(
        manager,
        s,
        planned_end=PLANNED_END,
        exception_mode="after_fact",
        exception_reason="Client took the car, pays tonight",
    )

    error = refused.json()["error"]
    assert error["code"] == "ISSUE_REQUIREMENTS_UNMET"
    assert error["params"]["can_authorize"] is False
    assert error["params"]["requirements"] == [{"code": "payment", "amounts": {"THB": "2000.00"}}]
    # § 23.4.7: the rental manager has no right to permit; the transfer is recorded instead.
    assert advance.status_code == 403
    assert recorded.status_code == 201, recorded.content
    rental = recorded.json()["rental"]
    assert rental["state"] == "active"
    assert rental["issue_exception"]["state"] == "pending"
    assert rental["issue_exception"]["requirements"] == ["payment"]


def test_rejection_marks_a_violation_and_keeps_the_rental(owner: Client, s: Scenario) -> None:
    _policy(owner, s, payment="require")
    rental = issue(
        owner, s, planned_end=PLANNED_END, exception_mode="after_fact", exception_reason="Late"
    ).json()["rental"]
    manager, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])

    assert _decide(manager, s, rental, "reject").status_code == 403
    rejected = _decide(owner, s, rental, "reject")

    assert rejected.status_code == 200, rejected.content
    after = rejected.json()["rental"]
    assert after["issue_exception"]["state"] == "rejected"
    assert after["state"] == "active"  # § 14.3.7: the fact stays, the vehicle stays busy
    again = _decide(owner, s, after, "confirm")
    assert again.json()["error"]["code"] == "ISSUE_EXCEPTION_NOT_PENDING"


def test_authorized_employee_permits_in_the_issue_form(owner: Client, s: Scenario) -> None:
    _policy(owner, s, payment="require")

    without_reason = issue(owner, s, planned_end=PLANNED_END, exception_mode="advance")
    permitted = issue(
        owner, s, planned_end=PLANNED_END, exception_mode="advance", exception_reason="VIP"
    )

    assert without_reason.json()["error"]["code"] == "FIELD_REQUIRED"
    assert permitted.status_code == 201, permitted.content
    exception = permitted.json()["rental"]["issue_exception"]
    assert (exception["mode"], exception["state"]) == ("advance", "applied")


def test_debt_decision_and_advance_permission_on_the_booking(owner: Client, s: Scenario) -> None:
    # § 14.3.5: «require a decision»; § 14.3.7, В6: permitted on the booking beforehand.
    first = issue(owner, s).json()["rental"]
    post(
        owner,
        f"/api/c/{s.cmp}/rentals/{first['id']}/return",
        {
            "actual_end": (ISSUE_AT + timedelta(days=1)).isoformat(),
            "return_location_id": s.location,
            "odometer": "10100",
            "fuel": "8",
            "expected_version": first["version"],
        },
        key(),
    )
    _policy(owner, s, debt="decide")
    start = NOW - timedelta(hours=1)
    booking = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": s.vehicle,
            "planned_start": start.isoformat(),
            "planned_end": (start + timedelta(days=1)).isoformat(),
            "client_id": s.client,
            "tariff_id": s.tariff,
            "issue_location_id": s.location,
        },
        key(),
    ).json()["rental"]
    later = {"actual_start": start.isoformat(), "booking_id": booking["id"], "odometer": "10200"}
    refused = issue(owner, s, **later)
    granted = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{booking['id']}/issue-exception",
        {"requirements": ["debt"], "reason": "Pays the debt on return", "expected_version": 1},
        key(),
    )

    assert refused.json()["error"]["code"] == "ISSUE_REQUIREMENTS_UNMET"
    assert granted.status_code == 200, granted.content
    assert granted.json()["rental"]["issue_exception"]["state"] == "granted"
    issued = issue(owner, s, **later)
    assert issued.status_code == 201, issued.content
    assert issued.json()["rental"]["issue_exception"]["state"] == "applied"
    assert issued.json()["warnings"][0]["code"] == "CLIENT_HAS_DEBT"


def test_required_check_not_done_is_a_conflict(owner: Client, s: Scenario) -> None:
    # § 10.3.3: not a refusal.
    _policy(owner, s, inspection="required")

    response = issue(owner, s)

    assert response.status_code == 201, response.content
    assert {"code": "INSPECTION_NOT_DONE"} in response.json()["warnings"]
    assert Inspection.objects.get().check_status == "required_not_done"


def test_location_policy_overrides_the_company(owner: Client, s: Scenario) -> None:
    other = add_location(owner, s.cmp, "Patong")
    _policy(owner, s, debt="allow")
    _policy(owner, s, other, debt="decide")

    company = owner.get(f"/api/c/{s.cmp}/issue-policy").json()["policy"]["effective"]
    local = owner.get(f"/api/c/{s.cmp}/issue-policy?location_id={other}").json()["policy"]
    phuket = owner.get(f"/api/c/{s.cmp}/issue-policy?location_id={s.location}").json()["policy"]

    assert (company["debt"], company["source"]) == ("allow", "company")
    assert (local["effective"]["debt"], local["effective"]["source"]) == ("decide", "location")
    assert local["own"] is True
    assert (phuket["own"], phuket["effective"]["source"]) == (False, "company")


def test_policy_and_exceptions_need_their_rights(owner: Client, s: Scenario) -> None:
    manager, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])
    client_id = add_client(owner, s.cmp, "Anna", "+66810000001")

    assert _policy(manager, s, payment="require").status_code == 403
    booking = post(
        manager,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": s.vehicle,
            "planned_start": (NOW + timedelta(days=5)).isoformat(),
            "planned_end": (NOW + timedelta(days=6)).isoformat(),
            "client_id": client_id,
        },
        key(),
    ).json()["rental"]
    denied = post(
        manager,
        f"/api/c/{s.cmp}/bookings/{booking['id']}/issue-exception",
        {"requirements": ["payment"], "reason": "x", "expected_version": booking["version"]},
        key(),
    )
    assert denied.status_code == 403
    assert not IssueException.objects.exists()
    assert Rental.objects.count() == 1
