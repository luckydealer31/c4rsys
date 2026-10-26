"""The deposit of a rental (ТЗ § 13.1.6, § 14.3.3, § 14.7, § 6.7.2, § 17.2.10)."""

from collections.abc import Iterator
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import Client

from c4rsys.finance import deposits
from c4rsys.finance.models import DepositEntry
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import returns as returns_module
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


def _with_deposit(owner: Client, s: Scenario, amount: str = "5000", days: int | None = 3) -> None:
    deposit: dict[str, Any] = {"required": True, "amount": amount}
    if days is not None:
        deposit["hold_days"] = days
    response = owner.put(
        f"/api/c/{s.cmp}/tariffs/{s.tariff}/vehicles/{s.vehicle}",
        data={"mileage": {"mode": "unlimited"}, "deposit": deposit},
        content_type="application/json",
        headers=key(),
    )
    assert response.status_code == 200, response.content


@pytest.fixture
def s(owner: Client) -> Scenario:
    scenario = build(owner)
    _with_deposit(owner, scenario)
    return scenario


def _account(client: Client, s: Scenario) -> str:
    body = {"name": "Cash", "type": "cash", "currency": "THB"}
    account_id: str = post(client, f"/api/c/{s.cmp}/cash-accounts", body, key()).json()[
        "cash_account"
    ]["id"]
    return account_id


def _receive(client: Client, s: Scenario, account: str, amount: str, **extra: Any) -> Any:
    body = {
        "cash_account_id": account,
        "client_id": s.client,
        "amount": amount,
        "currency": "THB",
        "allocations": [],
        **extra,
    }
    return post(client, f"/api/c/{s.cmp}/payments/cash", body, key())


def _deposit(client: Client, s: Scenario, rental_id: str) -> dict[str, Any]:
    data: dict[str, Any] = client.get(f"/api/c/{s.cmp}/rentals/{rental_id}/deposit").json()[
        "deposit"
    ]
    return data


def _balance(client: Client, s: Scenario) -> dict[str, Any]:
    data: dict[str, Any] = client.get(f"/api/c/{s.cmp}/clients/{s.client}/balance").json()
    return data


def _return(owner: Client, s: Scenario, rental: dict[str, Any]) -> None:
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


def test_issue_without_full_deposit_creates_a_conflict(owner: Client, s: Scenario) -> None:
    # § 14.3.3: the issue is allowed; the missing deposit is shown apart from the debt.
    rental = issue(owner, s).json()["rental"]
    assert rental["deposit"]["required"] == "5000.00"
    account = _account(owner, s)

    response = _receive(
        owner, s, account, "3000", deposits=[{"rental_id": rental["id"], "amount": "2000"}]
    )

    assert response.status_code == 201, response.content
    deposit = _deposit(owner, s, rental["id"])
    assert (deposit["received"], deposit["held"], deposit["missing"], deposit["conflict"]) == (
        "2000.00",
        "2000.00",
        "3000.00",
        True,
    )
    # The deposit is not the client's funds: only 1 000 of the receipt is available.
    balance = _balance(owner, s)
    assert balance["available"] == {"THB": "1000.00"}
    assert balance["deposit_held"] == {"THB": "2000.00"}

    waived = post(
        owner, f"/api/c/{s.cmp}/rentals/{rental['id']}/deposit/waive", {"reason": "Regular"}, key()
    )
    assert waived.status_code == 200, waived.content
    assert waived.json()["deposit"]["conflict"] is False
    assert waived.json()["deposit"]["held"] == "2000.00"


def test_term_moves_the_free_part_to_the_balance(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    account = _account(owner, s)
    _receive(owner, s, account, "5000", deposits=[{"rental_id": rental["id"], "amount": "5000"}])
    _return(owner, s, rental)
    hold = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/deposit/hold",
        {"amount": "1500", "reason": "Scratch on the door"},
        key(),
    )
    assert hold.status_code == 200, hold.content

    assert deposits.release_expired(NOW + timedelta(days=2)) == 0
    out = StringIO()
    later = NOW + timedelta(days=3, minutes=1)
    with mock.patch("django.utils.timezone.now", return_value=later):
        call_command("release_deposits", stdout=out)

    assert "1" in out.getvalue()
    deposit = _deposit(owner, s, rental["id"])
    assert (deposit["to_balance"], deposit["held"], deposit["claimed"]) == (
        "3500.00",
        "1500.00",
        "1500.00",
    )
    assert DepositEntry.objects.get(kind="to_balance").automatic is True
    assert _balance(owner, s)["available"] == {"THB": "3500.00"}
    # Running again moves nothing more: the claimed part stays.
    assert deposits.release_expired(NOW + timedelta(days=10)) == 0


def test_empty_term_means_only_by_hand(owner: Client, s: Scenario) -> None:
    # Customer's decision of 26.10.2026.
    _with_deposit(owner, s, days=None)
    rental = issue(owner, s).json()["rental"]
    _receive(
        owner,
        s,
        _account(owner, s),
        "5000",
        deposits=[{"rental_id": rental["id"], "amount": "5000"}],
    )
    _return(owner, s, rental)

    assert deposits.release_expired(NOW + timedelta(days=365)) == 0
    moved = post(
        owner,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/deposit/to-balance",
        {"amount": "5000"},
        key(),
    )
    assert moved.json()["deposit"]["held"] == "0.00"


def test_offset_and_refund_of_the_deposit(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    account = _account(owner, s)
    _receive(owner, s, account, "5000", deposits=[{"rental_id": rental["id"], "amount": "5000"}])
    base = f"/api/c/{s.cmp}/rentals/{rental['id']}/deposit"

    early = post(owner, f"{base}/refund", {"cash_account_id": account, "amount": "100"}, key())
    assert early.json()["error"]["code"] == "DEPOSIT_VEHICLE_NOT_RETURNED"
    _return(owner, s, rental)

    offset = post(
        owner, f"{base}/offset", {"order_id": rental["order_id"], "amount": "2000"}, key()
    )
    assert offset.status_code == 200, offset.content
    order = owner.get(f"/api/c/{s.cmp}/orders/{rental['order_id']}").json()["order"]
    assert order["amounts"]["THB"]["paid"] == "2000.00"
    too_much = post(owner, f"{base}/refund", {"cash_account_id": account, "amount": "3500"}, key())
    assert too_much.json()["error"]["code"] == "DEPOSIT_EXCEEDS_HELD"
    refunded = post(owner, f"{base}/refund", {"cash_account_id": account, "amount": "3000"}, key())
    assert refunded.status_code == 200, refunded.content
    deposit = refunded.json()["deposit"]
    assert (deposit["offset"], deposit["refunded"], deposit["held"]) == (
        "2000.00",
        "3000.00",
        "0.00",
    )
    # The deposit paid the rental; the refund did not touch the client's funds.
    balance = _balance(owner, s)
    assert balance["balance"] == {"THB": "0.00"}
    assert balance["deposit_held"] == {}
    account_data = owner.get(f"/api/c/{s.cmp}/cash-accounts/{account}/records").json()
    assert account_data["cash_account"]["balance"] == "2000.00"


def test_deposit_actions_need_their_right(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    manager, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])
    account = _account(owner, s)

    # The rental manager receives the deposit but does not move it (§ 23.2.2).
    received = _receive(
        manager, s, account, "1000", deposits=[{"rental_id": rental["id"], "amount": "1000"}]
    )
    moved = post(
        manager,
        f"/api/c/{s.cmp}/rentals/{rental['id']}/deposit/to-balance",
        {"amount": "100"},
        key(),
    )

    assert received.status_code == 201, received.content
    assert moved.status_code == 403


def test_deposit_entries_cannot_be_changed(owner: Client, s: Scenario) -> None:
    rental = issue(owner, s).json()["rental"]
    _receive(
        owner, s, _account(owner, s), "500", deposits=[{"rental_id": rental["id"], "amount": "500"}]
    )

    with pytest.raises(DatabaseError), transaction.atomic():
        DepositEntry.objects.update(reason="x")
