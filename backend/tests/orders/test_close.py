"""Closing an order (ТЗ § 12.1.9, § 12.2.3–12.2.4, § 13.6.8, § 23.6.5; acceptance criteria 2
and 7; examples base and E08). Includes the whole base scenario through the API."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.db import DatabaseError
from django.test import Client
from django.utils import timezone

from c4rsys.finance.models import ChargeEntry, MoneyMovement
from c4rsys.orders.models import Order
from c4rsys.platform.models import AuditEvent
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from c4rsys.rentals.models import Rental, RentalCalculation
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
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
def desk(owner: Client, s: Scenario) -> str:
    body = {"name": "Phuket cash", "type": "cash", "currency": "THB", "location_id": s.location}
    desk_id: str = post(owner, f"/api/c/{s.cmp}/cash-accounts", body, key()).json()["cash_account"][
        "id"
    ]
    return desk_id


def _finished_rental(client: Client, s: Scenario) -> dict[str, Any]:
    """Issue 1 Nov 10:00 (10 000 km, fuel 8) → return 3 Nov 10:00 (10 300 km, fuel 8) →
    formalization completed."""
    rental: dict[str, Any] = issue(client, s).json()["rental"]
    returned = post(
        client,
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
    done = post(
        client,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/complete-formalization",
        {"expected_version": returned["version"]},
        key(),
    )
    assert done.status_code == 200, done.content
    return rental


def _pay(client: Client, s: Scenario, desk: str, order_id: str, amount: str) -> Any:
    return post(
        client,
        f"/api/c/{s.cmp}/payments/cash",
        {
            "cash_account_id": desk,
            "client_id": s.client,
            "amount": amount,
            "currency": "THB",
            "allocations": [{"order_id": order_id, "amount": amount}],
        },
        key(),
    )


def _order(client: Client, s: Scenario, order_id: str) -> dict[str, Any]:
    order: dict[str, Any] = client.get(f"/api/c/{s.cmp}/orders/{order_id}").json()["order"]
    return order


def _close(
    client: Client, s: Scenario, order_id: str, headers: dict[str, str] | None = None
) -> Any:
    version = Order.objects.get(pk=order_id).version
    return post(
        client,
        f"/api/c/{s.cmp}/orders/{order_id}/close",
        {"expected_version": version},
        headers or key(),
    )


def test_base_scenario_end_to_end(owner: Client, s: Scenario, desk: str) -> None:
    # Base scenario: 2 days, 2 000 THB, one charge and one receipt, debt 0, order closed,
    # history with authors (docs/stage1/STAGE1_ACCEPTANCE_TESTS.md).
    rental = _finished_rental(owner, s)
    assert _order(owner, s, rental["order_id"])["state"] == "ready_to_close"
    _pay(owner, s, desk, rental["order_id"], "2000")

    response = _close(owner, s, rental["order_id"])

    assert response.status_code == 200, response.content
    order = response.json()["order"]
    assert order["state"] == "closed" and order["closed_at"]
    assert order["amounts"]["THB"] == {
        "charged": "2000.00",
        "paid": "2000.00",
        "to_pay": "0.00",
        "overpaid": "0.00",
    }
    assert ChargeEntry.objects.count() == 1 and MoneyMovement.objects.count() == 1
    detail = owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").json()["rental"]
    assert detail["calculation"]["billable_minutes"] == 2880
    assert detail["calculation"]["base_amount"] == "2000.00"
    # § 13.6.8: control versions at payment and at closing.
    assert list(RentalCalculation.objects.order_by("seq").values_list("reason", flat=True)) == [
        "payment",
        "closing",
    ]
    # Acceptance criterion 7 / base scenario: the history keeps the actions with their authors.
    for action in ("rental.issued", "rental.returned", "payment.received", "order.closed"):
        event = AuditEvent.objects.get(action=action)
        assert event.actor_user_id is not None and event.actor_employment_id is not None


def test_close_before_formalization_is_refused(owner: Client, s: Scenario, desk: str) -> None:
    # Acceptance criterion 2: closing before the formalization is complete.
    rental = issue(owner, s).json()["rental"]

    response = _close(owner, s, rental["order_id"])

    assert response.json()["error"]["code"] == "ORDER_NOT_READY_TO_CLOSE"
    assert response.json()["error"]["params"]["conditions"] == [
        {"operation": "rental", "id": rental["id"], "condition": "not_finished"}
    ]
    assert Order.objects.get().state == "open"


def test_e08_debt_needs_close_with_debt(owner: Client, s: Scenario, desk: str) -> None:
    # E08, § 23.6.5: «Rental manager» has order.close but not order.close_with_debt.
    manager, _ = hire(owner, s.cmp, "a@example.com", [{"type": "set", "set": "rental_manager"}])
    rental = _finished_rental(manager, s)
    _pay(manager, s, desk, rental["order_id"], "1500")

    refused = _close(manager, s, rental["order_id"])
    assert refused.json()["error"]["code"] == "ORDER_HAS_DEBT"
    assert refused.json()["error"]["params"]["debt"] == {"THB": "500.0000"}

    closed = _close(owner, s, rental["order_id"])  # the director may do everything
    assert closed.json()["order"]["state"] == "closed"
    assert closed.json()["order"]["amounts"]["THB"]["to_pay"] == "500.00"
    # § 6.7.2: the debt does not disappear after closing.
    balance = owner.get(f"/api/c/{s.cmp}/clients/{s.client}/balance").json()["balance"]
    assert balance == {"THB": "-500.00"}


def test_close_with_debt_by_granted_right(owner: Client, s: Scenario, desk: str) -> None:
    manager, _ = hire(
        owner,
        s.cmp,
        "a@example.com",
        [
            {"type": "set", "set": "rental_manager"},
            {"type": "grant", "permission": "order.close_with_debt"},
        ],
    )
    rental = _finished_rental(manager, s)

    assert _close(manager, s, rental["order_id"]).json()["order"]["state"] == "closed"


def test_close_needs_order_close_and_the_rental_section_right(owner: Client, s: Scenario) -> None:
    # § 23.6.5; the rental section right is rental.complete_formalization (decision 22.10.2026).
    rental = _finished_rental(owner, s)
    only_close, _ = hire(
        owner,
        s.cmp,
        "c@example.com",
        [
            {"type": "grant", "permission": "order.view"},
            {"type": "grant", "permission": "order.close"},
            {"type": "grant", "permission": "order.close_with_debt"},
        ],
    )
    accountant, _ = hire(owner, s.cmp, "b@example.com", [{"type": "set", "set": "accountant"}])

    missing = _close(only_close, s, rental["order_id"])
    denied = _close(accountant, s, rental["order_id"])

    assert missing.json()["error"]["code"] == "ORDER_SECTION_PERMISSION_MISSING"
    assert missing.json()["error"]["params"]["permission"] == "rental.complete_formalization"
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    assert Order.objects.get().state == "open"


def test_closed_order_does_not_change(owner: Client, s: Scenario, desk: str) -> None:
    # Acceptance criterion 7, § 12.2.3: payments, operations and a second closing are refused;
    # the rental and the order stay readable.
    rental = _finished_rental(owner, s)
    _pay(owner, s, desk, rental["order_id"], "2000")
    _close(owner, s, rental["order_id"])

    assert _pay(owner, s, desk, rental["order_id"], "10").json()["error"]["code"] == "ORDER_CLOSED"
    assert _close(owner, s, rental["order_id"]).json()["error"]["code"] == "ORDER_CLOSED"
    added = issue(
        owner, s, order_id=rental["order_id"], odometer="10300", actual_start=NOW.isoformat()
    )
    assert added.json()["error"]["code"] == "ORDER_CLOSED"
    assert owner.get(f"/api/c/{s.cmp}/rentals/{rental['id']}").status_code == 200
    assert _order(owner, s, rental["order_id"])["state"] == "closed"


def test_rental_of_a_closed_order_cannot_be_changed(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    Order.objects.update(state="closed", closed_at=timezone.now())

    response = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
        {"actual_end": NOW.isoformat(), "return_location_id": s.location, "expected_version": 1},
        key(),
    )

    assert response.json()["error"]["code"] == "ORDER_CLOSED"
    assert Rental.objects.get().state == "active"


def test_repeat_with_same_key_closes_once(owner: Client, s: Scenario, desk: str) -> None:
    # Acceptance criterion 4.
    rental = _finished_rental(owner, s)
    _pay(owner, s, desk, rental["order_id"], "2000")
    headers = {"Idempotency-Key": "close-1"}

    first = _close(owner, s, rental["order_id"], headers)
    again = post(
        owner,
        f"/api/c/{s.cmp}/orders/{rental['order_id']}/close",
        {"expected_version": first.json()["order"]["version"] - 1},
        headers,
    )

    assert again.json() == first.json()
    assert RentalCalculation.objects.filter(reason="closing").count() == 1


def test_journal_failure_leaves_the_order_open(owner: Client, s: Scenario, desk: str) -> None:
    rental = _finished_rental(owner, s)
    _pay(owner, s, desk, rental["order_id"], "2000")
    with mock.patch(
        "c4rsys.platform.models.AuditEvent.objects.bulk_create",
        side_effect=DatabaseError("journal unavailable"),
    ):
        failed = _close(owner, s, rental["order_id"])

    assert failed.json()["error"]["code"] == "OPERATION_NOT_POSTED"
    assert Order.objects.get().state == "open"
    assert not RentalCalculation.objects.filter(reason="closing").exists()
