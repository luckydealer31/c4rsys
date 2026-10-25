"""Exchange rates, transfers between accounts and payments in another currency (ТЗ § 17.1,
§ 23.2.7, § 23.7.6)."""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError, transaction
from django.test import Client
from django.utils import timezone

from c4rsys.finance import rates
from c4rsys.finance.models import ExchangeRate, MoneyMovement, RateFetch, Transfer
from c4rsys.platform.models import AuditEvent
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import NOW, Scenario, build, issue

pytestmark = pytest.mark.django_db

# 1 USD = 36.5 THB = 0.92 EUR.
ANSWER = {
    "result": "success",
    "time_last_update_unix": 1792886400,
    "base_code": "USD",
    "conversion_rates": {"USD": 1, "THB": 36.5, "EUR": 0.92, "ZZZ": 3},
}


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
def stored() -> RateFetch:
    return rates.store(ANSWER)


def _account(client: Client, cmp: str, name: str, currency: str) -> str:
    body = {"name": name, "type": "cash", "currency": currency}
    account_id: str = post(client, f"/api/c/{cmp}/cash-accounts", body, key()).json()[
        "cash_account"
    ]["id"]
    return account_id


def _opening(client: Client, cmp: str, account: str, amount: str, currency: str) -> None:
    post(
        client,
        f"/api/c/{cmp}/cash-accounts/{account}/opening",
        {"amount": amount, "currency": currency},
        key(),
    )


def _transfer(client: Client, cmp: str, source: str, target: str, amount: str, **data: Any) -> Any:
    body = {"from_account_id": source, "to_account_id": target, "amount": amount, **data}
    return post(client, f"/api/c/{cmp}/transfers", body, key())


def _balances(client: Client, cmp: str) -> dict[str, Any]:
    data: dict[str, Any] = client.get(f"/api/c/{cmp}/cash-accounts").json()
    return data


# --- Rates ----------------------------------------------------------------------------------


def test_store_keeps_source_pair_and_time(stored: RateFetch) -> None:
    # § 17.1.6: each rate keeps the source, the pair and the time of receipt.
    assert stored.source == "exchangerate-api" and stored.base_id == "USD"
    assert set(ExchangeRate.objects.values_list("quote_id", flat=True)) == {"USD", "THB", "EUR"}


def test_cross_rate_through_base(stored: RateFetch) -> None:
    suggestion = rates.suggest("THB", "EUR")

    assert suggestion is not None
    assert suggestion.rate == Decimal("0.025205479452")
    assert suggestion.stale is False
    assert rates.suggest("THB", "JPY") is None


def test_outdated_rate_is_marked(stored: RateFetch) -> None:
    later = timezone.now() + timedelta(hours=37)

    suggestion = rates.suggest("USD", "THB", at=later)

    assert suggestion is not None and suggestion.stale is True


def test_rates_cannot_be_changed(stored: RateFetch) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        ExchangeRate.objects.filter(fetch=stored).update(rate=Decimal(1))


def test_command_downloads_with_key() -> None:
    out = StringIO()
    with (
        mock.patch.dict("os.environ", {"C4R_EXCHANGE_RATE_API_KEY": "k"}),
        mock.patch.object(rates, "download", return_value=ANSWER) as download,
    ):
        call_command("fetch_exchange_rates", stdout=out)

    download.assert_called_once_with("k")
    assert RateFetch.objects.count() == 1 and "stored" in out.getvalue()


def test_command_keeps_old_rates_when_source_fails() -> None:
    with (
        mock.patch.dict("os.environ", {"C4R_EXCHANGE_RATE_API_KEY": "k"}),
        mock.patch.object(rates, "download", side_effect=rates.FetchError("quota-reached")),
        pytest.raises(CommandError, match="quota-reached"),
    ):
        call_command("fetch_exchange_rates")


def test_suggestion_endpoint(owner: Client, s: Scenario, stored: RateFetch) -> None:
    _account(owner, s.cmp, "Cash", "THB")

    data = owner.get(f"/api/c/{s.cmp}/exchange-rates/suggestion?from=usd&to=THB").json()
    missing = owner.get(f"/api/c/{s.cmp}/exchange-rates/suggestion?from=USD&to=JPY").json()

    assert (data["rate"], data["stale"], data["may_override"]) == ("36.500000000000", False, True)
    assert missing["rate"] is None and missing["latest_fetch_at"] is not None


# --- Transfers (§ 17.1.6) -------------------------------------------------------------------


def test_transfer_in_one_currency(owner: Client, s: Scenario) -> None:
    desk = _account(owner, s.cmp, "Desk", "THB")
    safe = _account(owner, s.cmp, "Safe", "THB")
    _opening(owner, s.cmp, desk, "1000", "THB")

    response = _transfer(owner, s.cmp, desk, safe, "400", note="Evening")

    assert response.status_code == 201, response.content
    assert response.json()["in"]["transfer"]["other_account_id"] == desk
    balances = {a["name"]: a["balance"] for a in _balances(owner, s.cmp)["cash_accounts"]}
    assert balances == {"Desk": "600.00", "Safe": "400.00"}
    assert AuditEvent.objects.filter(action="money.transferred").count() == 1


def test_exchange_transfer_keeps_both_amounts_and_rate(
    owner: Client, s: Scenario, stored: RateFetch
) -> None:
    usd = _account(owner, s.cmp, "Bank USD", "USD")
    thb = _account(owner, s.cmp, "Desk", "THB")
    _opening(owner, s.cmp, usd, "100", "USD")

    response = _transfer(owner, s.cmp, usd, thb, "10.01")

    assert response.status_code == 201, response.content
    side = response.json()["out"]
    assert (side["amount"], side["currency"]) == ("10.01", "USD")
    assert side["transfer"]["other_amount"] == "365.37"  # 10.01 × 36.5 = 365.365 → half up
    transfer = Transfer.objects.get()
    assert (transfer.applied_rate, transfer.rate_manual) == (Decimal("36.5"), False)
    assert transfer.rate_fetch == stored
    # A transfer is neither income nor expense.
    assert not MoneyMovement.objects.filter(kind="transfer", article__isnull=False).exists()


def test_manual_rate_needs_its_permission(owner: Client, s: Scenario, stored: RateFetch) -> None:
    usd = _account(owner, s.cmp, "Bank USD", "USD")
    thb = _account(owner, s.cmp, "Desk", "THB")
    _opening(owner, s.cmp, usd, "100", "USD")
    accountant, _ = hire(owner, s.cmp, "a@example.com", [{"type": "set", "set": "accountant"}])

    refused = _transfer(accountant, s.cmp, usd, thb, "10", rate="35")
    suggested = _transfer(accountant, s.cmp, usd, thb, "10")
    manual = _transfer(owner, s.cmp, usd, thb, "10", rate="35.2")

    assert refused.status_code == 403
    assert suggested.status_code == 201
    assert manual.json()["in"]["amount"] == "352.00"
    kept = Transfer.objects.get(rate_manual=True)
    # § 17.1.8: both the suggested and the applied rate are kept.
    assert (kept.suggested_rate, kept.applied_rate) == (Decimal("36.5"), Decimal("35.2"))


def test_without_any_rate_only_manual(owner: Client, s: Scenario) -> None:
    usd = _account(owner, s.cmp, "Bank USD", "USD")
    thb = _account(owner, s.cmp, "Desk", "THB")
    _opening(owner, s.cmp, usd, "100", "USD")

    refused = _transfer(owner, s.cmp, usd, thb, "10")
    manual = _transfer(owner, s.cmp, usd, thb, "10", rate="36")

    assert refused.json()["error"]["code"] == "RATE_UNAVAILABLE"
    assert manual.status_code == 201
    assert Transfer.objects.get().suggested_rate is None


def test_transfer_to_the_same_account_is_refused(owner: Client, s: Scenario) -> None:
    desk = _account(owner, s.cmp, "Desk", "THB")

    response = _transfer(owner, s.cmp, desk, desk, "10")

    assert response.json()["error"]["code"] == "TRANSFER_SAME_ACCOUNT"


def test_transfer_needs_both_accounts_in_scope(owner: Client, s: Scenario) -> None:
    desk = _account(owner, s.cmp, "Desk", "THB")
    safe = _account(owner, s.cmp, "Safe", "THB")
    accountant, _ = hire(
        owner,
        s.cmp,
        "a@example.com",
        [{"type": "set", "set": "accountant", "cash_accounts": [desk]}],
    )

    assert _transfer(accountant, s.cmp, desk, safe, "10").status_code == 404


def test_common_balance_in_main_currency(owner: Client, s: Scenario, stored: RateFetch) -> None:
    # § 17.1.2, § 17.1.5: the accounts keep their currencies; the total uses the current rate.
    usd = _account(owner, s.cmp, "Bank USD", "USD")
    thb = _account(owner, s.cmp, "Desk", "THB")
    _opening(owner, s.cmp, usd, "100", "USD")
    _opening(owner, s.cmp, thb, "500", "THB")

    total = _balances(owner, s.cmp)["total"]

    assert (total["amount"], total["currency"], total["missing"]) == ("4150.00", "THB", [])


# --- Payment in another currency (§ 17.1.4) -------------------------------------------------


def _issue_and_return(owner: Client, s: Scenario) -> dict[str, Any]:
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
    return rental


def test_order_paid_in_another_currency(owner: Client, s: Scenario, stored: RateFetch) -> None:
    # The receipt stays in USD on the USD account; the THB order is settled by the rate.
    rental = _issue_and_return(owner, s)
    usd = _account(owner, s.cmp, "Bank USD", "USD")
    body = {
        "cash_account_id": usd,
        "client_id": s.client,
        "amount": "60",
        "currency": "USD",
        "allocations": [{"order_id": rental["order_id"], "amount": "50"}],
    }

    response = post(owner, f"/api/c/{s.cmp}/payments/cash", body, key())

    assert response.status_code == 201, response.content
    allocation = response.json()["payment"]["allocations"][0]
    assert (allocation["amount"], allocation["settled_currency"]) == ("50.00", "THB")
    assert Decimal(allocation["settled_amount"]) == Decimal("1825")
    order = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    assert order["amounts"]["THB"]["charged"] == "2000.00"
    assert order["amounts"]["THB"]["paid"] == "1825.00"
    assert "USD" not in order["amounts"]
    balance = owner.get(f"/api/c/{s.cmp}/clients/{s.client}/balance").json()["balance"]
    # 10 USD stay available; 175 THB are still owed.
    assert (balance["USD"], balance["THB"]) == ("10.00", "-175.00")
