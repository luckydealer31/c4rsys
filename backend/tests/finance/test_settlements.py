"""Settlements with parties, set-offs, refunds and corrections (ТЗ § 11.2.4, § 11.4.2,
§ 17.2.3, § 17.2.10–17.2.17, § 17.3.3, § 23.2.7)."""

from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock
from uuid import UUID

import pytest
from django.db import DatabaseError, transaction
from django.test import Client
from django.utils import timezone

from c4rsys.finance import rates
from c4rsys.finance.models import Offset, SettlementEntry
from c4rsys.orders.models import Order
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from c4rsys.warehouse.models import Asset
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import NOW, Scenario, build, issue
from tests.warehouse.test_operations import item as new_item
from tests.warehouse.test_operations import location, warehouse_of
from tests.warehouse.test_receipts import supplier
from tests.warehouse.test_supplier_returns import give_back

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


def _account(client: Client, cmp: str, name: str = "Cash", currency: str = "THB") -> str:
    body = {"name": name, "type": "cash", "currency": currency}
    account_id: str = post(client, f"/api/c/{cmp}/cash-accounts", body, key()).json()[
        "cash_account"
    ]["id"]
    return account_id


def _article(client: Client, cmp: str, code: str) -> str:
    items = client.get(f"/api/c/{cmp}/finance-articles").json()["articles"]
    article_id: str = next(a["id"] for a in items if a["code"] == code)
    return article_id


def _pay_client(client: Client, s: Scenario, account: str, amount: str, **extra: Any) -> Any:
    body = {
        "cash_account_id": account,
        "client_id": s.client,
        "amount": amount,
        "currency": "THB",
        "allocations": [],
        **extra,
    }
    return post(client, f"/api/c/{s.cmp}/payments/cash", body, key())


def _settlement(client: Client, s: Scenario) -> dict[str, Any]:
    data: dict[str, Any] = client.get(f"/api/c/{s.cmp}/clients/{s.client}/settlement").json()
    return data


def _returned_order(owner: Client, s: Scenario) -> str:
    rental: dict[str, Any] = issue(owner, s).json()["rental"]
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
    order_id: str = rental["order_id"]
    return order_id


def _order_amounts(owner: Client, s: Scenario, order_id: str) -> dict[str, str]:
    amounts: dict[str, str] = owner.get(f"/api/c/{s.cmp}/orders/{order_id}").json()["order"][
        "amounts"
    ]["THB"]
    return amounts


# --- Suppliers (§ 11.2.4, § 11.4.2) ---------------------------------------------------------


def _purchase_and_receipt(
    owner: Client, cmp: str, seller: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    ctx = warehouse_of(location(owner, cmp, "Phuket"))
    pads = new_item(owner, cmp, "Pads", "piece")
    purchase: dict[str, Any] = post(
        owner,
        f"/api/c/{cmp}/warehouse/purchases",
        {
            "supplier_id": seller,
            "lines": [{"item_id": pads["id"], "quantity": "10", "unit_cost": "200"}],
        },
        key(),
    ).json()["purchase"]
    draft = post(
        owner,
        f"/api/c/{cmp}/warehouse/receipts",
        {
            "source": "purchase",
            "context_id": ctx,
            "purchase_id": purchase["id"],
            "lines": [
                {
                    "item_id": pads["id"],
                    "quantity": "4",
                    "unit_cost": "200",
                    "purchase_line_id": purchase["lines"][0]["id"],
                }
            ],
        },
        key(),
    ).json()["receipt"]
    receipt: dict[str, Any] = post(
        owner,
        f"/api/c/{cmp}/warehouse/receipts/{draft['id']}/post",
        {"expected_version": draft["version"]},
        key(),
    ).json()["receipt"]
    return purchase, receipt


def _supplier_money(
    owner: Client, cmp: str, account: str, seller: str, amount: str, **data: Any
) -> Any:
    body = {
        "cash_account_id": account,
        "counterparty_id": seller,
        "direction": "out",
        "amount": amount,
        "currency": "THB",
        "article_id": _article(owner, cmp, "goods_materials"),
        **data,
    }
    return post(owner, f"/api/c/{cmp}/supplier-payments", body, key())


def test_prepayment_receipt_payment_and_return(owner: Client, s: Scenario) -> None:
    cmp = s.cmp
    seller = supplier(owner, cmp)
    account = _account(owner, cmp)
    purchase, receipt = _purchase_and_receipt(owner, cmp, seller)

    # Prepayment before the goods come; the payment does not change the stock.
    prepaid = _supplier_money(
        owner, cmp, account, seller, "500", links=[{"purchase_id": purchase["id"], "amount": "500"}]
    )
    assert prepaid.status_code == 201, prepaid.content
    statement = owner.get(f"/api/c/{cmp}/supplier-statement?purchase_id={purchase['id']}").json()
    assert (statement["received"], statement["paid"], statement["to_pay"]) == (
        "800.00",
        "500.00",
        "300.00",
    )
    card = owner.get(f"/api/c/{cmp}/counterparties/{seller}/settlement").json()
    assert card["payable"] == {"THB": "300.00"}

    _supplier_money(
        owner, cmp, account, seller, "300", links=[{"receipt_id": receipt["id"], "amount": "300"}]
    )
    assert owner.get(f"/api/c/{cmp}/counterparties/{seller}/settlement").json()["payable"] == {
        "THB": "0.00"
    }

    # A return lowers the debt only when its result is settled; money back is a receipt.
    line = receipt["lines"][0]
    asset = owner.get(f"/api/c/{cmp}/warehouse/receipts/{receipt['id']}").json()["receipt"]
    returned = give_back(
        owner,
        cmp,
        receipt["id"],
        [{"receipt_line_id": line["id"], "asset_ids": [_first_asset(line["id"])]}],
    ).json()["operation"]
    assert asset["number"] == receipt["number"]
    assert owner.get(f"/api/c/{cmp}/counterparties/{seller}/settlement").json()["payable"] == {
        "THB": "0.00"
    }
    settled = post(
        owner,
        f"/api/c/{cmp}/supplier-returns/{returned['id']}/settle",
        {"result": "money_back"},
        key(),
    )
    assert settled.status_code == 201, settled.content
    assert owner.get(f"/api/c/{cmp}/counterparties/{seller}/settlement").json()["payable"] == {
        "THB": "-200.00"
    }
    again = post(
        owner,
        f"/api/c/{cmp}/supplier-returns/{returned['id']}/settle",
        {"result": "money_back"},
        key(),
    )
    assert again.json()["error"]["code"] == "RETURN_SETTLED"
    back = _supplier_money(owner, cmp, account, seller, "200", direction="in")
    assert back.status_code == 201, back.content
    lists = owner.get(f"/api/c/{cmp}/settlements").json()
    assert [c["payable"] for c in lists["counterparties"]] in ([], [{"THB": "0.00"}])


def _first_asset(receipt_line_id: str) -> str:
    asset = Asset.objects.filter(receipt_line_id=UUID(receipt_line_id)).first()
    assert asset is not None
    return str(asset.id)


def test_supplier_link_must_be_of_this_supplier(owner: Client, s: Scenario) -> None:
    seller = supplier(owner, s.cmp)
    other = post(
        owner,
        f"/api/c/{s.cmp}/counterparties",
        {
            "kind": "company",
            "company_name": "Other",
            "contacts": [{"kind": "email", "value": "o@x.example"}],
        },
        key(),
    ).json()["counterparty"]["id"]
    account = _account(owner, s.cmp)
    purchase, _ = _purchase_and_receipt(owner, s.cmp, seller)

    response = _supplier_money(
        owner,
        s.cmp,
        account,
        other,
        "100",
        links=[{"purchase_id": purchase["id"], "amount": "100"}],
    )

    assert response.json()["error"]["code"] == "SUPPLIER_MISMATCH"


# --- Initial obligations (§ 17.2.14) --------------------------------------------------------


def test_initial_debt_of_client_and_its_set_off(owner: Client, s: Scenario) -> None:
    account = _account(owner, s.cmp)
    opening = post(
        owner,
        f"/api/c/{s.cmp}/settlement-entries",
        {
            "client_id": s.client,
            "debtor": "party",
            "amount": "1000",
            "currency": "THB",
            "as_of": "2026-10-01",
        },
        key(),
    )
    assert opening.status_code == 201, opening.content
    assert _settlement(owner, s)["balance"] == {"THB": "-1000.00"}

    _pay_client(owner, s, account, "1500")
    data = _settlement(owner, s)
    assert (data["available"], data["unpaid"], data["balance"]) == (
        {"THB": "1500.00"},
        {"THB": "1000.00"},
        {"THB": "500.00"},
    )

    entry = opening.json()["entry"]["id"]
    offset = post(
        owner,
        f"/api/c/{s.cmp}/offsets",
        {"client_id": s.client, "opening_id": entry, "amount": "1000", "currency": "THB"},
        key(),
    )
    assert offset.status_code == 201, offset.content
    data = _settlement(owner, s)
    # § 17.2.13: set-off lowers both by the same amount; the balance stays.
    assert (data["available"], data["unpaid"], data["balance"]) == (
        {"THB": "500.00"},
        {},
        {"THB": "500.00"},
    )
    cancel = post(owner, f"/api/c/{s.cmp}/settlement-entries/{entry}/cancel", {"note": "x"}, key())
    assert cancel.json()["error"]["code"] == "ENTRY_HAS_OFFSETS"


def test_initial_debt_to_supplier_and_cancellation(owner: Client, s: Scenario) -> None:
    seller = supplier(owner, s.cmp)
    entry = post(
        owner,
        f"/api/c/{s.cmp}/settlement-entries",
        {
            "counterparty_id": seller,
            "debtor": "company",
            "amount": "300",
            "currency": "THB",
            "as_of": "2026-10-01",
        },
        key(),
    ).json()["entry"]
    url = f"/api/c/{s.cmp}/counterparties/{seller}/settlement"
    assert owner.get(url).json()["payable"] == {"THB": "300.00"}

    post(owner, f"/api/c/{s.cmp}/settlement-entries/{entry['id']}/cancel", {"note": "Typo"}, key())

    assert owner.get(url).json()["payable"] == {"THB": "0.00"}
    with pytest.raises(DatabaseError), transaction.atomic():
        SettlementEntry.objects.filter(pk=entry["id"]).update(amount=Decimal(1))


# --- Set-off of available funds (§ 17.2.10, § 17.2.15–17.2.16) ------------------------------


def test_set_off_to_order_and_cancel_of_closed_order(owner: Client, s: Scenario) -> None:
    order_id = _returned_order(owner, s)
    account = _account(owner, s.cmp)
    _pay_client(owner, s, account, "3000")

    too_much = post(
        owner,
        f"/api/c/{s.cmp}/offsets",
        {"client_id": s.client, "order_id": order_id, "amount": "2500", "currency": "THB"},
        key(),
    )
    assert too_much.json()["error"]["code"] == "OFFSET_EXCEEDS_DEBT"
    offset = post(
        owner,
        f"/api/c/{s.cmp}/offsets",
        {"client_id": s.client, "order_id": order_id, "amount": "2000", "currency": "THB"},
        key(),
    ).json()["offset"]
    assert _order_amounts(owner, s, order_id)["paid"] == "2000.00"
    assert _settlement(owner, s)["balance"] == {"THB": "1000.00"}

    Order.objects.filter(pk=order_id).update(state="closed", closed_at=timezone.now())
    url = f"/api/c/{s.cmp}/offsets/{offset['id']}/cancel"
    asked = post(owner, url, {}, key())
    assert asked.json()["error"]["code"] == "DEBT_WILL_APPEAR"
    assert asked.json()["error"]["params"]["debt"] == "2000.00"
    confirmed = post(owner, url, {"confirmed_debt": "2000.00"}, key())
    assert confirmed.status_code == 200, confirmed.content
    assert _order_amounts(owner, s, order_id)["paid"] == "0.00"
    # The money received earlier stays with the client.
    assert _settlement(owner, s)["available"] == {"THB": "3000.00"}


def test_correct_set_off_keeps_history(owner: Client, s: Scenario) -> None:
    order_id = _returned_order(owner, s)
    account = _account(owner, s.cmp)
    _pay_client(owner, s, account, "3000")
    offset = post(
        owner,
        f"/api/c/{s.cmp}/offsets",
        {"client_id": s.client, "order_id": order_id, "amount": "2000", "currency": "THB"},
        key(),
    ).json()["offset"]

    corrected = post(
        owner, f"/api/c/{s.cmp}/offsets/{offset['id']}/correct", {"amount": "1500"}, key()
    )

    assert corrected.status_code == 201, corrected.content
    assert corrected.json()["offset"]["replaces_id"] == offset["id"]
    assert Offset.objects.get(pk=offset["id"]).state == "cancelled"
    assert _order_amounts(owner, s, order_id)["paid"] == "1500.00"


def test_set_off_needs_its_right(owner: Client, s: Scenario) -> None:
    order_id = _returned_order(owner, s)
    manager, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])

    response = post(
        manager,
        f"/api/c/{s.cmp}/offsets",
        {"client_id": s.client, "order_id": order_id, "amount": "10", "currency": "THB"},
        key(),
    )

    assert response.status_code == 403


# --- Refunds (§ 17.2.17) --------------------------------------------------------------------


def test_refund_only_available_and_in_another_currency(owner: Client, s: Scenario) -> None:
    rates.store(
        {
            "result": "success",
            "base_code": "USD",
            "time_last_update_unix": 1792886400,
            "conversion_rates": {"USD": 1, "THB": 36.5},
        }
    )
    thb = _account(owner, s.cmp)
    usd = _account(owner, s.cmp, "Bank USD", "USD")
    _pay_client(owner, s, thb, "1000")
    _account_opening = post(
        owner,
        f"/api/c/{s.cmp}/cash-accounts/{usd}/opening",
        {"amount": "100", "currency": "USD"},
        key(),
    )
    assert _account_opening.status_code == 201

    def refund(account: str, amount: str) -> Any:
        body = {
            "cash_account_id": account,
            "client_id": s.client,
            "funds_amount": amount,
            "funds_currency": "THB",
        }
        return post(owner, f"/api/c/{s.cmp}/refunds", body, key())

    assert refund(thb, "1200").json()["error"]["code"] == "REFUND_EXCEEDS_AVAILABLE"
    assert refund(thb, "400").status_code == 201
    in_usd = refund(usd, "365")
    assert in_usd.status_code == 201, in_usd.content
    record = in_usd.json()["record"]
    assert (record["amount"], record["currency"], record["funds_amount"]) == (
        "10.00",
        "USD",
        "365.00",
    )
    assert _settlement(owner, s)["available"] == {"THB": "235.00"}


# --- Corrections (§ 17.2.11, § 23.2.7) and split of an expense (§ 17.3.3) -------------------


def test_reversal_of_a_manual_expense(owner: Client, s: Scenario) -> None:
    account = _account(owner, s.cmp)
    post(
        owner,
        f"/api/c/{s.cmp}/cash-accounts/{account}/opening",
        {"amount": "1000", "currency": "THB"},
        key(),
    )
    expense = post(
        owner,
        f"/api/c/{s.cmp}/money-records",
        {
            "cash_account_id": account,
            "direction": "out",
            "amount": "300",
            "currency": "THB",
            "article_id": _article(owner, s.cmp, "staff"),
        },
        key(),
    ).json()["record"]
    url = f"/api/c/{s.cmp}/money-records/{expense['id']}/reverse"

    without_reason = post(owner, url, {}, key())
    reversed_ = post(owner, url, {"reason": "Entered twice"}, key())

    assert without_reason.status_code == 400
    assert reversed_.status_code == 201, reversed_.content
    assert reversed_.json()["records"][0]["reverses_id"] == expense["id"]
    balance = owner.get(f"/api/c/{s.cmp}/cash-accounts/{account}/records").json()["cash_account"][
        "balance"
    ]
    assert balance == "1000.00"
    assert post(owner, url, {"reason": "again"}, key()).json()["error"]["code"] == "RECORD_REVERSED"


def test_reversal_of_a_transfer_and_of_an_allocated_payment(owner: Client, s: Scenario) -> None:
    desk = _account(owner, s.cmp, "Desk")
    safe = _account(owner, s.cmp, "Safe")
    post(
        owner,
        f"/api/c/{s.cmp}/cash-accounts/{desk}/opening",
        {"amount": "1000", "currency": "THB"},
        key(),
    )
    moved = post(
        owner,
        f"/api/c/{s.cmp}/transfers",
        {"from_account_id": desk, "to_account_id": safe, "amount": "400"},
        key(),
    ).json()["out"]
    order_id = _returned_order(owner, s)
    paid = _pay_client(
        owner, s, desk, "500", allocations=[{"order_id": order_id, "amount": "500"}]
    ).json()["payment"]

    back = post(
        owner,
        f"/api/c/{s.cmp}/money-records/{moved['id']}/reverse",
        {"reason": "Wrong safe"},
        key(),
    )
    allocated = post(
        owner, f"/api/c/{s.cmp}/money-records/{paid['id']}/reverse", {"reason": "x"}, key()
    )

    assert len(back.json()["records"]) == 2
    balances = {
        a["name"]: a["balance"]
        for a in owner.get(f"/api/c/{s.cmp}/cash-accounts").json()["cash_accounts"]
    }
    assert balances == {"Desk": "1500.00", "Safe": "0.00"}
    assert allocated.json()["error"]["code"] == "RECORD_ALLOCATED"


def test_relink_payment_from_order(owner: Client, s: Scenario) -> None:
    order_id = _returned_order(owner, s)
    account = _account(owner, s.cmp)
    payment = _pay_client(
        owner, s, account, "2000", allocations=[{"order_id": order_id, "amount": "2000"}]
    ).json()["payment"]
    allocation = payment["allocations"][0]["id"]

    response = post(
        owner,
        f"/api/c/{s.cmp}/payment-allocations/{allocation}/relink",
        {"order_id": None, "reason": "Paid for another order"},
        key(),
    )

    assert response.status_code == 200, response.content
    assert _order_amounts(owner, s, order_id)["paid"] == "0.00"
    # § 17.2.11: the money stays with the client; only the link changed.
    data = _settlement(owner, s)
    assert (data["available"], data["unpaid"]) == ({"THB": "2000.00"}, {"THB": "2000.00"})


def test_split_of_an_expense(owner: Client, s: Scenario) -> None:
    order_id = _returned_order(owner, s)
    account = _account(owner, s.cmp)
    expense = post(
        owner,
        f"/api/c/{s.cmp}/money-records",
        {
            "cash_account_id": account,
            "direction": "out",
            "amount": "300",
            "currency": "THB",
            "article_id": _article(owner, s.cmp, "vehicle_upkeep"),
        },
        key(),
    ).json()["record"]
    url = f"/api/c/{s.cmp}/money-records/{expense['id']}/shares"

    wrong = post(owner, url, {"shares": [{"vehicle_id": s.vehicle, "amount": "100"}]}, key())
    split = post(
        owner,
        url,
        {
            "shares": [
                {"vehicle_id": s.vehicle, "amount": "100"},
                {"order_id": order_id, "amount": "200"},
            ]
        },
        key(),
    )

    assert wrong.json()["error"]["code"] == "SHARES_SUM_MISMATCH"
    assert split.status_code == 200, split.content
    assert [x["amount"] for x in split.json()["shares"]] == ["100.00", "200.00"]
