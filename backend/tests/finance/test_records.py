"""Initial balances and manual receipts and payouts (ТЗ § 17.2.1–17.2.2, § 17.2.7–17.2.9,
§ 22.1.7, § 23.7.1–23.7.2)."""

from typing import Any

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from c4rsys.finance.models import MoneyMovement
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import Scenario, add_location, build

pytestmark = pytest.mark.django_db


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


def _opening(client: Client, cmp: str, account: str, amount: str = "1000") -> Any:
    return post(
        client,
        f"/api/c/{cmp}/cash-accounts/{account}/opening",
        {"amount": amount, "currency": "THB"},
        key(),
    )


def _record(client: Client, cmp: str, account: str, article: str, **data: Any) -> Any:
    body = {
        "cash_account_id": account,
        "direction": "out",
        "amount": "300",
        "currency": "THB",
        "method": "cash",
        "article_id": article,
        **data,
    }
    return post(client, f"/api/c/{cmp}/money-records", body, key())


def _records(client: Client, cmp: str, account: str) -> Any:
    return client.get(f"/api/c/{cmp}/cash-accounts/{account}/records").json()


def test_opening_balance_is_a_separate_record(owner: Client, s: Scenario) -> None:
    # § 22.1.7: optional; a separate initial record that is not a receipt of money.
    account = _account(owner, s.cmp)

    response = _opening(owner, s.cmp, account)

    assert response.status_code == 201, response.content
    assert response.json()["record"]["kind"] == "opening"
    assert _records(owner, s.cmp, account)["cash_account"]["balance"] == "1000.00"
    assert _opening(owner, s.cmp, account).json()["error"]["code"] == "OPENING_BALANCE_EXISTS"


def test_payout_and_receipt_change_the_balance(owner: Client, s: Scenario) -> None:
    account = _account(owner, s.cmp)
    _opening(owner, s.cmp, account)

    payout = _record(
        owner, s.cmp, account, _article(owner, s.cmp, "vehicle_upkeep"), vehicle_id=s.vehicle
    )
    receipt = _record(
        owner,
        s.cmp,
        account,
        _article(owner, s.cmp, "other_income"),
        direction="in",
        amount="50.5",
        method="noncash",
        location_id=s.location,
        note="Scrap sold",
    )

    assert payout.status_code == 201, payout.content
    assert receipt.status_code == 201, receipt.content
    assert payout.json()["record"]["vehicle_id"] == s.vehicle
    data = _records(owner, s.cmp, account)
    assert data["cash_account"]["balance"] == "750.50"
    assert [r["kind"] for r in data["records"]] == ["manual", "manual", "opening"]
    assert {"money.paid_out", "money.received"} <= set(
        AuditEvent.objects.values_list("action", flat=True)
    )


def test_article_must_match_direction_and_be_active(owner: Client, s: Scenario) -> None:
    account = _account(owner, s.cmp)
    income = _article(owner, s.cmp, "rental")

    wrong_kind = _record(owner, s.cmp, account, income)
    post(owner, f"/api/c/{s.cmp}/finance-articles/{income}/archive", {"expected_version": 1}, key())
    archived = _record(owner, s.cmp, account, income, direction="in")

    assert wrong_kind.json()["error"]["code"] == "ARTICLE_KIND_MISMATCH"
    assert archived.json()["error"]["code"] == "ARTICLE_ARCHIVED"
    assert not MoneyMovement.objects.exists()


def test_record_in_account_currency_only(owner: Client, s: Scenario) -> None:
    # Exchange comes with § 17.1.3; until then the record is in the account's currency.
    account = _account(owner, s.cmp)

    response = _record(owner, s.cmp, account, _article(owner, s.cmp, "staff"), currency="USD")

    assert response.json()["error"]["code"] == "CURRENCY_MISMATCH"


def test_context_must_belong_to_location(owner: Client, s: Scenario) -> None:
    account = _account(owner, s.cmp)
    other = add_location(owner, s.cmp, "Krabi")
    contexts = owner.get(f"/api/c/{s.cmp}/locations/{other}").json()["location"]["contexts"]

    response = _record(
        owner,
        s.cmp,
        account,
        _article(owner, s.cmp, "staff"),
        location_id=s.location,
        context_id=contexts[0]["id"],
    )

    assert response.json()["error"]["code"] == "CONTEXT_NOT_IN_LOCATION"


def test_permissions_of_receipts_payouts_and_balances(owner: Client, s: Scenario) -> None:
    # § 23.7.1: receiving a rental payment does not give payouts or the money of the company.
    account = _account(owner, s.cmp)
    article = _article(owner, s.cmp, "staff")
    manager, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])
    accountant, _ = hire(owner, s.cmp, "a@example.com", [{"type": "set", "set": "accountant"}])

    assert _record(manager, s.cmp, account, article).status_code == 403
    assert manager.get(f"/api/c/{s.cmp}/cash-accounts/{account}/records").status_code == 403
    listed = manager.get(f"/api/c/{s.cmp}/cash-accounts").json()["cash_accounts"]
    assert "balance" not in listed[0]
    assert _opening(accountant, s.cmp, account).status_code == 403
    assert _record(accountant, s.cmp, account, article).status_code == 201


def test_listed_accounts_limit_payouts(owner: Client, s: Scenario) -> None:
    # § 23.7.2: finance actions limited to the chosen accounts.
    chosen = _account(owner, s.cmp, "Front desk")
    other = _account(owner, s.cmp, "Bank")
    accountant, _ = hire(
        owner,
        s.cmp,
        "a@example.com",
        [{"type": "set", "set": "accountant", "cash_accounts": [chosen]}],
    )
    article = _article(owner, s.cmp, "staff")

    assert _record(accountant, s.cmp, chosen, article).status_code == 201
    assert _record(accountant, s.cmp, other, article).status_code == 404


def test_posted_record_cannot_be_changed(owner: Client, s: Scenario) -> None:
    # § 17.2.11: a correction is a separate record.
    account = _account(owner, s.cmp)
    _record(owner, s.cmp, account, _article(owner, s.cmp, "staff"))
    movement = MoneyMovement.objects.get()

    with pytest.raises(DatabaseError), transaction.atomic():
        MoneyMovement.objects.filter(pk=movement.pk).update(note="changed")
