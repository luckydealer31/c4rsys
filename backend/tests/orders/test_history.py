"""History of an order and a rental, journal of an employee (ТЗ § 23.10, § 24.4.5;
acceptance criterion 7: the history keeps the actions with their authors)."""

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key
from tests.rentals.scenario import NOW, Scenario, build, issue

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
def manager(owner: Client, s: Scenario) -> tuple[Client, str]:
    return hire(owner, s.cmp, "a@example.com", [{"type": "set", "set": "rental_manager"}])


@pytest.fixture
def closed(owner: Client, s: Scenario, manager: tuple[Client, str]) -> dict[str, Any]:
    """The base scenario done by employee A (rental manager)."""
    a, _ = manager
    desk = post(
        owner,
        f"/api/c/{s.cmp}/cash-accounts",
        {"name": "Phuket cash", "type": "cash", "currency": "THB", "location_id": s.location},
        key(),
    ).json()["cash_account"]["id"]
    rental: dict[str, Any] = issue(a, s).json()["rental"]
    returned = post(
        a,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
        {
            "actual_end": NOW.isoformat(),
            "return_location_id": s.location,
            "odometer": "10300",
            "fuel": "8",
            "expected_version": rental["version"],
        },
        key(),
    ).json()["rental"]
    post(
        a,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/complete-formalization",
        {"expected_version": returned["version"]},
        key(),
    )
    post(
        a,
        f"/api/c/{s.cmp}/payments/cash",
        {
            "cash_account_id": desk,
            "client_id": s.client,
            "amount": "2000",
            "currency": "THB",
            "allocations": [{"order_id": rental["order_id"], "amount": "2000"}],
        },
        key(),
    )
    order = a.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    closing = post(
        a,
        f"/api/c/{s.cmp}/orders/{rental['order_id']}/close",
        {"expected_version": order["version"]},
        key(),
    )
    assert closing.status_code == 200, closing.content
    return rental


def test_order_history_shows_the_whole_scenario_with_authors(
    s: Scenario, manager: tuple[Client, str], closed: dict[str, Any]
) -> None:
    a, employment = manager
    history = a.get(f"/api/c/{s.cmp}/orders/{closed['order_id']}/history").json()["history"]

    actions = [h["action"] for h in history]
    for expected in (
        "order.created",
        "rental.issued",
        "inspection.saved",
        "rental.returned",
        "charge.recorded",
        "rental.formalization_completed",
        "payment.received",
        "rental.control_version",
        "order.closed",
    ):
        assert expected in actions, expected
    assert actions.index("rental.issued") < actions.index("rental.returned")
    assert actions.index("payment.received") < actions.index("order.closed")
    closing = next(h for h in history if h["action"] == "order.closed")
    assert closing["actor"] == {
        "user_id": closing["actor"]["user_id"],
        "name": "Anna Petrova",
        "employment_id": employment,
        "system_process": None,
    }
    assert closing["before"] == {"state": "open"} and closing["after"]["state"] == "closed"
    assert closing["occurred_at"].endswith("+00:00")


def test_rental_history_is_the_rental_part(
    s: Scenario, manager: tuple[Client, str], closed: dict[str, Any]
) -> None:
    a, _ = manager
    history = a.get(f"/api/c/{s.cmp}/rentals/{closed['id']}/history").json()["history"]

    actions = {h["action"] for h in history}
    assert {"rental.issued", "rental.returned", "inspection.saved", "charge.recorded"} <= actions
    assert "payment.received" not in actions and "order.closed" not in actions


def test_employee_journal_with_filters(
    owner: Client, s: Scenario, manager: tuple[Client, str], closed: dict[str, Any]
) -> None:
    # § 23.10.1–23.10.2.
    _, employment = manager
    path = f"/api/c/{s.cmp}/employees/{employment}/journal"

    everything = owner.get(path).json()["journal"]
    rental_section = owner.get(f"{path}?section=rental").json()["journal"]
    payments = owner.get(f"{path}?action=payment.received").json()["journal"]
    future = (NOW + timedelta(days=3650)).isoformat().replace("+", "%2B")
    nothing = owner.get(f"{path}?from={future}").json()["journal"]

    assert {e["actor"]["employment_id"] for e in everything} == {employment}
    assert everything[0]["occurred_at"] >= everything[-1]["occurred_at"]  # newest first
    assert rental_section and all(e["action"].startswith("rental.") for e in rental_section)
    assert [e["action"] for e in payments] == ["payment.received"]
    assert payments[0]["after"]["amount"] == "2000"
    assert nothing == []


def test_journal_needs_its_own_permission_and_hides_money_without_finance_right(
    owner: Client, s: Scenario, manager: tuple[Client, str], closed: dict[str, Any]
) -> None:
    # § 23.10.3, decision 22.10.2026.
    a, employment = manager
    auditor, _ = hire(
        owner,
        s.cmp,
        "audit@example.com",
        [{"type": "grant", "permission": "employee.journal.view"}],
    )
    path = f"/api/c/{s.cmp}/employees/{employment}/journal"

    assert a.get(path).status_code == 403  # the manager has no journal permission
    journal = auditor.get(path).json()["journal"]
    payment = next(e for e in journal if e["action"] == "payment.received")
    issued = next(e for e in journal if e["action"] == "rental.issued")
    assert payment["details_hidden"] is True and payment["after"] is None
    assert issued["details_hidden"] is False and issued["after"] is not None


def test_journal_stays_after_access_is_blocked(
    owner: Client, s: Scenario, manager: tuple[Client, str], closed: dict[str, Any]
) -> None:
    # § 23.10.1.
    _, employment = manager
    post(owner, f"/api/c/{s.cmp}/employees/{employment}/block", {}, key())

    journal = owner.get(f"/api/c/{s.cmp}/employees/{employment}/journal").json()["journal"]

    assert any(e["action"] == "order.closed" for e in journal)


def test_other_company_sees_no_history(
    owner: Client, s: Scenario, manager: tuple[Client, str], closed: dict[str, Any]
) -> None:
    _, employment = manager
    other = create_company(owner, key="c-2", display_name="Other").json()["company"]["public_id"]

    assert owner.get(f"/api/c/{other}/orders/{closed['order_id']}/history").status_code == 404
    assert owner.get(f"/api/c/{other}/rentals/{closed['id']}/history").status_code == 404
    assert owner.get(f"/api/c/{other}/employees/{employment}/journal").status_code == 404
