"""Remaining finance pieces independent of other modules: correcting the client of a payment
(§ 17.2.12), another currency in supplier payments and in moving a payment (§ 17.1.4), reversing
an initial balance (§ 22.1.7, § 23.2.7), the common balance in a chosen currency (§ 17.1.2)."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client

from c4rsys.finance import rates
from c4rsys.finance.models import PaymentClientChange
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.finance.test_settlements import _purchase_and_receipt
from tests.rentals.scenario import NOW, Scenario, add_client, build, issue
from tests.warehouse.test_receipts import supplier

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
def usd_rate() -> None:
    rates.store(
        {
            "result": "success",
            "base_code": "USD",
            "time_last_update_unix": 1792886400,
            "conversion_rates": {"USD": 1, "THB": 36.5},
        }
    )


def _account(client: Client, cmp: str, name: str = "Cash", currency: str = "THB") -> str:
    body = {"name": name, "type": "cash", "currency": currency}
    account_id: str = post(client, f"/api/c/{cmp}/cash-accounts", body, key()).json()[
        "cash_account"
    ]["id"]
    return account_id


def _pay(client: Client, s: Scenario, account: str, amount: str, **extra: Any) -> Any:
    body = {
        "cash_account_id": account,
        "client_id": s.client,
        "amount": amount,
        "currency": "THB",
        "allocations": [],
        **extra,
    }
    return post(client, f"/api/c/{s.cmp}/payments/cash", body, key())


def _available(client: Client, cmp: str, client_id: str) -> dict[str, str]:
    data: dict[str, str] = client.get(f"/api/c/{cmp}/clients/{client_id}/balance").json()[
        "available"
    ]
    return data


# --- § 17.2.12 ------------------------------------------------------------------------------


def test_client_of_a_payment_is_corrected_with_history(owner: Client, s: Scenario) -> None:
    other = add_client(owner, s.cmp, first="Anna", phone="+66899999999")
    account = _account(owner, s.cmp)
    payment = _pay(owner, s, account, "1000").json()["payment"]
    url = f"/api/c/{s.cmp}/money-records/{payment['id']}/client"

    without_reason = post(owner, url, {"client_id": other}, key())
    changed = post(owner, url, {"client_id": other, "reason": "Wrong card"}, key())

    assert without_reason.status_code == 400
    assert changed.status_code == 200, changed.content
    record = changed.json()["record"]
    assert (record["client_id"], record["client_changed"]) == (other, True)
    assert _available(owner, s.cmp, s.client) == {}
    assert _available(owner, s.cmp, other) == {"THB": "1000.00"}
    change = PaymentClientChange.objects.get()
    assert (str(change.previous_client_id), change.reason) == (s.client, "Wrong card")


def test_client_change_needs_an_unlinked_unspent_payment(owner: Client, s: Scenario) -> None:
    other = add_client(owner, s.cmp, first="Anna", phone="+66899999999")
    account = _account(owner, s.cmp)

    def change(payment: dict[str, Any]) -> Any:
        url = f"/api/c/{s.cmp}/money-records/{payment['id']}/client"
        return post(owner, url, {"client_id": other, "reason": "x"}, key())

    # The money already paid back is not the client's to move (§ 17.2.12).
    spent = _pay(owner, s, account, "300").json()["payment"]
    post(
        owner,
        f"/api/c/{s.cmp}/refunds",
        {
            "cash_account_id": account,
            "client_id": s.client,
            "funds_amount": "300",
            "funds_currency": "THB",
        },
        key(),
    )
    assert change(spent).json()["error"]["code"] == "CLIENT_FUNDS_USED"

    # A payment linked to an order is unlinked first.
    rental = issue(owner, s).json()["rental"]
    linked = _pay(
        owner, s, account, "500", allocations=[{"order_id": rental["order_id"], "amount": "500"}]
    ).json()["payment"]
    assert change(linked).json()["error"]["code"] == "RECORD_ALLOCATED"


# --- § 17.1.4 -------------------------------------------------------------------------------


def test_supplier_paid_in_another_currency(owner: Client, s: Scenario, usd_rate: None) -> None:
    seller = supplier(owner, s.cmp)
    _, receipt = _purchase_and_receipt(owner, s.cmp, seller)  # 4 × 200 = 800 THB
    usd = _account(owner, s.cmp, "Bank USD", "USD")
    articles = owner.get(f"/api/c/{s.cmp}/finance-articles").json()["articles"]
    goods = next(a["id"] for a in articles if a["code"] == "goods_materials")

    paid = post(
        owner,
        f"/api/c/{s.cmp}/supplier-payments",
        {
            "cash_account_id": usd,
            "counterparty_id": seller,
            "direction": "out",
            "amount": "20",
            "currency": "USD",
            "article_id": goods,
            "links": [{"receipt_id": receipt["id"], "amount": "20"}],
        },
        key(),
    )

    assert paid.status_code == 201, paid.content
    statement = owner.get(f"/api/c/{s.cmp}/supplier-statement?receipt_id={receipt['id']}").json()
    assert (statement["paid"], statement["to_pay"]) == ("730.00", "70.00")
    card = owner.get(f"/api/c/{s.cmp}/counterparties/{seller}/settlement").json()
    assert card["payable"] == {"THB": "70.00", "USD": "0.00"}


def test_payment_in_another_currency_is_moved_off_the_order(
    owner: Client, s: Scenario, usd_rate: None
) -> None:
    rental = issue(owner, s).json()["rental"]
    post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
        {
            "actual_end": NOW.isoformat(),
            "return_location_id": s.location,
            "odometer": "10300",
            "fuel": "8",
            "expected_version": rental["version"],
        },
        key(),
    )
    usd = _account(owner, s.cmp, "Bank USD", "USD")
    body = {
        "cash_account_id": usd,
        "client_id": s.client,
        "amount": "50",
        "currency": "USD",
        "allocations": [{"order_id": rental["order_id"], "amount": "50"}],
    }
    payment = post(owner, f"/api/c/{s.cmp}/payments/cash", body, key()).json()["payment"]

    moved = post(
        owner,
        f"/api/c/{s.cmp}/payment-allocations/{payment['allocations'][0]['id']}/relink",
        {"order_id": None, "reason": "Paid for another order"},
        key(),
    )

    assert moved.status_code == 200, moved.content
    assert _available(owner, s.cmp, s.client) == {"THB": "0.00", "USD": "50.00"}


# --- § 22.1.7 and § 17.1.2 ------------------------------------------------------------------


def test_reversed_initial_balance_is_entered_again(owner: Client, s: Scenario) -> None:
    account = _account(owner, s.cmp)
    url = f"/api/c/{s.cmp}/cash-accounts/{account}/opening"
    first = post(owner, url, {"amount": "1000", "currency": "THB"}, key()).json()["record"]

    again = post(owner, url, {"amount": "1200", "currency": "THB"}, key())
    reversed_ = post(
        owner, f"/api/c/{s.cmp}/money-records/{first['id']}/reverse", {"reason": "Typo"}, key()
    )
    corrected = post(owner, url, {"amount": "1200", "currency": "THB"}, key())

    assert again.json()["error"]["code"] == "OPENING_BALANCE_EXISTS"
    assert reversed_.status_code == 201, reversed_.content
    assert corrected.status_code == 201, corrected.content
    balance = owner.get(f"/api/c/{s.cmp}/cash-accounts/{account}/records").json()
    assert balance["cash_account"]["balance"] == "1200.00"


def test_common_balance_in_a_chosen_currency(owner: Client, s: Scenario, usd_rate: None) -> None:
    account = _account(owner, s.cmp)
    post(
        owner,
        f"/api/c/{s.cmp}/cash-accounts/{account}/opening",
        {"amount": "3650", "currency": "THB"},
        key(),
    )

    total = owner.get(f"/api/c/{s.cmp}/cash-accounts?total_currency=usd").json()["total"]
    wrong = owner.get(f"/api/c/{s.cmp}/cash-accounts?total_currency=XXX")

    assert (total["amount"], total["currency"]) == ("100.00", "USD")
    assert wrong.status_code == 400
