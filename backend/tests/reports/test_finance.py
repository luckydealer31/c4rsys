"""Reports, part О4: «Finance» (ТЗ § 21.5.1, § 21.1.4, § 21.1.1)."""

from typing import Any

import pytest
from django.test import Client

from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.finance.test_settlements import _account, _article
from tests.orders.test_close import _finished_rental, _pay, frozen_clock  # noqa: F401
from tests.rentals.scenario import Scenario

pytestmark = pytest.mark.django_db


def _blocks(client: Client, s: Scenario, query: str = "") -> dict[str, dict[str, Any]]:
    response = client.get(f"/api/c/{s.cmp}/reports/finance?{query}")
    assert response.status_code == 200, response.content
    return {b["code"]: b for b in response.json()["blocks"]}


def _money(block: dict[str, Any]) -> dict[str, str]:
    return {r["currency"]: r["amount"] for r in block["rows"]}


def test_receipts_expenses_transfers_and_result(owner: Client, s: Scenario) -> None:
    desk = _account(owner, s.cmp, "Desk")
    safe = _account(owner, s.cmp, "Safe")
    rental = _finished_rental(owner, s)
    assert _pay(owner, s, desk, rental["order_id"], "1500").status_code == 201
    expense = post(
        owner,
        f"/api/c/{s.cmp}/money-records",
        {
            "cash_account_id": desk,
            "direction": "out",
            "amount": "300",
            "currency": "THB",
            "article_id": _article(owner, s.cmp, "staff"),
        },
        key(),
    ).json()["record"]
    post(
        owner,
        f"/api/c/{s.cmp}/transfers",
        {"from_account_id": desk, "to_account_id": safe, "amount": "400"},
        key(),
    )
    blocks = _blocks(owner, s)
    assert _money(blocks["finance.receipts"]) == {"THB": "1500.00"}
    assert _money(blocks["finance.expenses"]) == {"THB": "300.00"}
    # A transfer is apart and is neither receipt nor expense (§ 21.5.1).
    assert _money(blocks["finance.transfers"]) == {"THB": "400.00"}
    assert _money(blocks["finance.result"]) == {"THB": "1200.00"}
    assert blocks["finance.result"]["value"]["amount"] == "1200.00"
    balances = {r["account"]: r for r in blocks["finance.account_balances"]["rows"]}
    assert balances["Desk"]["opening"] == "0.00" and balances["Desk"]["closing"] == "800.00"
    assert balances["Safe"]["closing"] == "400.00"
    staff = [r for r in blocks["finance.by_article"]["rows"] if r["expenses"] != "0.00"]
    assert [r["expenses"] for r in staff] == ["300.00"]
    assert _money(blocks["finance.orders_paid"]) == {"THB": "1500.00"}
    assert _money(blocks["finance.receivable"]) == {"THB": "500.00"}

    # § 21.1.4: a reversal goes to its own period and takes back its record.
    post(owner, f"/api/c/{s.cmp}/money-records/{expense['id']}/reverse", {"reason": "Twice"}, key())
    blocks = _blocks(owner, s)
    assert _money(blocks["finance.expenses"]) == {}
    assert blocks["finance.expenses"]["value"]["amount"] == "0.00"


def test_partial_access_names_the_accounts(owner: Client, s: Scenario) -> None:
    desk = _account(owner, s.cmp, "Desk")
    _account(owner, s.cmp, "Safe")
    clerk, _ = hire(
        owner,
        s.cmp,
        "c@example.com",
        [
            {"type": "grant", "permission": "reports.view"},
            {"type": "grant", "permission": "finance.account.view", "cash_accounts": [desk]},
        ],
    )
    blocks = _blocks(clerk, s)
    assert blocks["finance.receipts"]["accounts"] == ["Desk"]
    # Debts need every account: hidden with partial access (§ 21.1.1).
    assert "finance.receivable" not in blocks
    nobody, _ = hire(
        owner, s.cmp, "n@example.com", [{"type": "grant", "permission": "reports.view"}]
    )
    assert _blocks(nobody, s) == {}
