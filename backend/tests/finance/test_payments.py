"""Cash receipt and allocation to orders (ТЗ § 6.7.2, § 12.2.4–12.2.5, § 13.6.3, § 13.6.8,
§ 14.3.5, § 17.2.10–17.2.13; acceptance criteria 3 and 4; examples base, E08, E09, E10)."""

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.db import DatabaseError, transaction
from django.test import Client
from django.utils import timezone

from c4rsys.finance.models import ChargeEntry, MoneyMovement, PaymentAllocation
from c4rsys.orders.models import Order
from c4rsys.platform.models import AuditEvent
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from c4rsys.rentals.models import RentalCalculation
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import BKK, NOW, Scenario, add_client, build, issue

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
    body = {"name": "Phuket cash", "type": "cash", "currency": "THB"}
    desk_id: str = post(owner, f"/api/c/{s.cmp}/cash-accounts", body, key()).json()["cash_account"][
        "id"
    ]
    return desk_id


def _issue_and_return(owner: Client, s: Scenario, end: datetime = NOW) -> dict[str, Any]:
    rental: dict[str, Any] = issue(owner, s).json()["rental"]
    post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
        {
            "actual_end": end.isoformat(),
            "return_location_id": s.location,
            "odometer": "10300",
            "fuel": "8",
            "expected_version": rental["version"],
        },
        key(),
    )
    return rental


def _pay(
    client: Client,
    s: Scenario,
    desk: str,
    amount: str,
    allocations: list[dict[str, Any]],
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> Any:
    body = {
        "cash_account_id": desk,
        "client_id": s.client,
        "amount": amount,
        "currency": "THB",
        "allocations": allocations,
        **extra,
    }
    return post(client, f"/api/c/{s.cmp}/payments/cash", body, headers or key())


def _order(owner: Client, s: Scenario, order_id: str) -> dict[str, Any]:
    order: dict[str, Any] = owner.get(f"/api/c/{s.cmp}/orders/{order_id}").json()["order"]
    return order


def _balance(owner: Client, s: Scenario) -> dict[str, str]:
    balance: dict[str, str] = owner.get(f"/api/c/{s.cmp}/clients/{s.client}/balance").json()[
        "balance"
    ]
    return balance


def test_base_example_payment_2000_debt_zero(owner: Client, s: Scenario, desk: str) -> None:
    # Acceptance criterion 3: one charge of 2 000 and one receipt of 2 000, kept separately.
    rental = _issue_and_return(owner, s)

    response = _pay(owner, s, desk, "2000", [{"order_id": rental["order_id"], "amount": "2000"}])

    assert response.status_code == 201, response.content
    payment = response.json()["payment"]
    assert (payment["number"], payment["amount"], payment["state"]) == (1, "2000.00", "posted")
    assert ChargeEntry.objects.count() == 1 and MoneyMovement.objects.count() == 1
    assert _order(owner, s, rental["order_id"])["amounts"]["THB"] == {
        "charged": "2000.00",
        "paid": "2000.00",
        "to_pay": "0.00",
        "overpaid": "0.00",
    }
    assert _balance(owner, s) == {"THB": "0.00"}
    version = RentalCalculation.objects.get()
    assert (version.reason, version.kind, version.base_amount) == (
        "payment",
        "actual",
        Decimal("2000.00"),
    )
    assert AuditEvent.objects.filter(action="payment.received").count() == 1


def test_e08_partial_payment_leaves_debt_and_warns_at_next_issue(
    owner: Client, s: Scenario, desk: str
) -> None:
    rental = _issue_and_return(owner, s)

    _pay(owner, s, desk, "1500", [{"order_id": rental["order_id"], "amount": "1500"}])

    assert _order(owner, s, rental["order_id"])["amounts"]["THB"]["to_pay"] == "500.00"
    assert _balance(owner, s) == {"THB": "-500.00"}
    # § 14.3.5–14.3.6: debt only warns at the next issue.
    second = issue(
        owner,
        s,
        actual_start=datetime(2026, 11, 3, 10, 0, tzinfo=BKK).isoformat(),
        odometer="10300",
    )
    assert second.status_code == 201, second.content
    assert second.json()["warnings"] == [{"code": "CLIENT_HAS_DEBT", "amounts": {"THB": "500.00"}}]


def test_e09_prepayment_then_early_return_gives_overpayment(
    owner: Client, s: Scenario, desk: str
) -> None:
    # Allocation above the debt is allowed (decision 22.10.2026); no automatic payout (§ 13.6.3).
    rental = issue(owner, s).json()["rental"]
    _pay(owner, s, desk, "2000", [{"order_id": rental["order_id"], "amount": "2000"}])
    post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
        {
            "actual_end": datetime(2026, 11, 2, 10, 0, tzinfo=BKK).isoformat(),
            "return_location_id": s.location,
            "odometer": "10300",
            "fuel": "8",
            "expected_version": rental["version"],
        },
        key(),
    )

    amounts = _order(owner, s, rental["order_id"])["amounts"]["THB"]
    assert amounts == {
        "charged": "1000.00",
        "paid": "2000.00",
        "to_pay": "0.00",
        "overpaid": "1000.00",
    }
    assert MoneyMovement.objects.get().amount == Decimal("2000")
    assert _balance(owner, s) == {"THB": "1000.00"}
    assert RentalCalculation.objects.get().kind == "current"  # version at payment, before return


def test_e10_lost_answer_repeat_gives_one_payment(owner: Client, s: Scenario, desk: str) -> None:
    # Acceptance criterion 4: the same key returns the earlier result.
    rental = _issue_and_return(owner, s)
    headers = {"Idempotency-Key": "pay-1"}
    allocation = [{"order_id": rental["order_id"], "amount": "2000"}]

    first = _pay(owner, s, desk, "2000", allocation, headers)
    repeat = _pay(owner, s, desk, "2000", allocation, headers)
    reused = _pay(owner, s, desk, "1000", allocation, headers)

    assert repeat.json() == first.json()
    assert MoneyMovement.objects.count() == 1 and PaymentAllocation.objects.count() == 1
    assert _order(owner, s, rental["order_id"])["amounts"]["THB"]["to_pay"] == "0.00"
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    # A new command with the same amount is not a repeat (E10).
    assert _pay(owner, s, desk, "2000", allocation).json()["payment"]["number"] == 2


def test_unallocated_part_is_an_advance(owner: Client, s: Scenario, desk: str) -> None:
    # § 17.2.10, decision 22.10.2026.
    rental = _issue_and_return(owner, s)

    _pay(owner, s, desk, "3000", [{"order_id": rental["order_id"], "amount": "2000"}])
    _pay(owner, s, desk, "500", [])

    assert _balance(owner, s) == {"THB": "1500.00"}
    assert _order(owner, s, rental["order_id"])["amounts"]["THB"]["paid"] == "2000.00"


@pytest.mark.parametrize(
    ("amount", "allocated", "extra", "code"),
    [
        ("1000", "1500", {}, "ALLOCATION_EXCEEDS_PAYMENT"),
        ("1000", "1000", {"currency": "USD"}, "CURRENCY_MISMATCH"),
        ("1000.001", "1000", {}, "AMOUNT_TOO_PRECISE"),
        ("-5", "1", {}, "AMOUNT_INVALID"),
        ("1000", 1000.5, {}, "AMOUNT_INVALID"),
    ],
)
def test_invalid_payments_post_nothing(
    owner: Client,
    s: Scenario,
    desk: str,
    amount: str,
    allocated: Any,
    extra: dict[str, Any],
    code: str,
) -> None:
    rental = _issue_and_return(owner, s)

    response = _pay(
        owner, s, desk, amount, [{"order_id": rental["order_id"], "amount": allocated}], **extra
    )

    assert response.json()["error"]["code"] == code
    assert not MoneyMovement.objects.exists()


def test_order_of_another_client_or_closed_order_is_refused(
    owner: Client, s: Scenario, desk: str
) -> None:
    rental = _issue_and_return(owner, s)
    other_client = add_client(owner, s.cmp, "Anna", "+66899999999")

    foreign = _pay(
        owner,
        s,
        desk,
        "100",
        [{"order_id": rental["order_id"], "amount": "100"}],
        client_id=other_client,
    )
    Order.objects.update(state="closed", closed_at=timezone.now())
    closed = _pay(owner, s, desk, "100", [{"order_id": rental["order_id"], "amount": "100"}])

    assert foreign.json()["error"]["code"] == "ORDER_CLIENT_MISMATCH"
    assert closed.json()["error"]["code"] == "ORDER_CLOSED"
    assert not MoneyMovement.objects.exists()


def test_archived_account_is_refused(owner: Client, s: Scenario, desk: str) -> None:
    post(owner, f"/api/c/{s.cmp}/cash-accounts/{desk}/archive", {"expected_version": 1}, key())

    assert _pay(owner, s, desk, "100", []).json()["error"]["code"] == "CASH_ACCOUNT_ARCHIVED"


def test_receipt_needs_the_right_on_that_account(owner: Client, s: Scenario, desk: str) -> None:
    # § 23.1.11–23.1.12: «all accounts» of the company or the listed ones, whatever the location.
    other_desk = post(
        owner,
        f"/api/c/{s.cmp}/cash-accounts",
        {"name": "Second cash", "type": "cash", "currency": "THB"},
        key(),
    ).json()["cash_account"]["id"]
    manager, _ = hire(
        owner,
        s.cmp,
        "m@example.com",
        [{"type": "set", "set": "rental_manager", "locations": [s.location]}],
    )
    listed, _ = hire(
        owner,
        s.cmp,
        "l@example.com",
        [
            {
                "type": "set",
                "set": "rental_manager",
                "locations": [s.location],
                "cash_accounts": [desk],
            }
        ],
    )
    driver, _ = hire(owner, s.cmp, "d@example.com", [{"type": "set", "set": "driver"}])

    assert _pay(manager, s, other_desk, "100", []).status_code == 201
    assert _pay(listed, s, desk, "100", []).status_code == 201
    assert _pay(listed, s, other_desk, "100", []).status_code == 404
    assert _pay(driver, s, desk, "100", []).status_code == 404
    assert MoneyMovement.objects.count() == 2


def test_journal_failure_posts_nothing(owner: Client, s: Scenario, desk: str) -> None:
    rental = _issue_and_return(owner, s)
    headers = {"Idempotency-Key": "pay-journal"}
    allocation = [{"order_id": rental["order_id"], "amount": "2000"}]
    with mock.patch(
        "c4rsys.platform.models.AuditEvent.objects.bulk_create",
        side_effect=DatabaseError("journal unavailable"),
    ):
        failed = _pay(owner, s, desk, "2000", allocation, headers)

    assert failed.json()["error"]["code"] == "OPERATION_NOT_POSTED"
    assert not MoneyMovement.objects.exists() and not RentalCalculation.objects.exists()
    retried = _pay(owner, s, desk, "2000", allocation, headers)
    assert retried.json()["payment"]["number"] == 1  # the failed attempt consumed no number


def test_posted_records_are_protected(owner: Client, s: Scenario, desk: str) -> None:
    rental = _issue_and_return(owner, s)
    _pay(owner, s, desk, "2000", [{"order_id": rental["order_id"], "amount": "2000"}])

    for statement in (
        lambda: MoneyMovement.objects.update(amount=1),
        lambda: MoneyMovement.objects.all().delete(),
        lambda: PaymentAllocation.objects.update(amount=1),
        lambda: PaymentAllocation.objects.all().delete(),
        lambda: RentalCalculation.objects.update(base_amount=1),
    ):
        with pytest.raises(DatabaseError), transaction.atomic():
            statement()
    PaymentAllocation.objects.update(state="cancelled")  # the only allowed change
