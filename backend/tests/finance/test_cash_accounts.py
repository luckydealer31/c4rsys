"""Cash desks and bank accounts, and their permission scope (ТЗ § 17.2.5, § 22.1.7,
§ 23.1.11–23.1.12; clarification of 23.10.2026: accounts are not bound to locations)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.finance.models import CashAccount
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()[
        "company"
    ]["public_id"]
    return public_id


def _location(client: Client, cmp: str, name: str) -> str:
    location_id: str = post(
        client, f"/api/c/{cmp}/locations", {"name": name}, headers=key()
    ).json()["location"]["id"]
    return location_id


def _create(client: Client, cmp: str, **data: Any) -> Any:
    body = {"name": "Main cash", "type": "cash", **data}
    return post(client, f"/api/c/{cmp}/cash-accounts", body, headers=key())


def _names(client: Client, cmp: str) -> list[str]:
    return [a["name"] for a in client.get(f"/api/c/{cmp}/cash-accounts").json()["cash_accounts"]]


def test_create_cash_desk_with_company_currency_proposed(owner: Client, cmp: str) -> None:
    # § 22.1.7: name, type and currency are required; the main currency is proposed.
    response = _create(owner, cmp)

    assert response.status_code == 201, response.content
    account = response.json()["cash_account"]
    assert account["currency"] == "THB"
    assert "location_id" not in account
    assert account["state"] == "active"
    assert AuditEvent.objects.filter(action="cash_account.created").count() == 1


def test_currency_required_without_main_currency(owner: Client) -> None:
    cmp = create_company(owner).json()["company"]["public_id"]

    response = _create(owner, cmp)

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "currency"


def test_bank_account_in_other_currency(owner: Client, cmp: str) -> None:
    # § 17.1.1, § 17.2.5.
    account = _create(owner, cmp, name="Bangkok Bank", type="bank", currency="usd").json()[
        "cash_account"
    ]

    assert (account["type"], account["currency"]) == ("bank", "USD")


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"name": ""}, "name"),
        ({"type": "card"}, "type"),
        ({"currency": "XXX"}, "currency"),
    ],
)
def test_invalid_input_is_rejected(
    owner: Client, cmp: str, data: dict[str, Any], field: str
) -> None:
    response = _create(owner, cmp, **data)

    assert response.status_code == 400
    assert response.json()["error"]["field"] == field
    assert not CashAccount.objects.exists()


def test_rename_and_archive(owner: Client, cmp: str) -> None:
    account = _create(owner, cmp).json()["cash_account"]
    path = f"/api/c/{cmp}/cash-accounts/{account['id']}"

    renamed = owner.patch(
        path,
        data={"name": "Front desk", "expected_version": 1},
        content_type="application/json",
        headers=key(),
    ).json()["cash_account"]
    archived = post(owner, f"{path}/archive", {"expected_version": 2}, headers=key())

    assert renamed["name"] == "Front desk" and renamed["currency"] == "THB"
    assert archived.json()["cash_account"]["state"] == "archived"
    again = post(owner, f"{path}/archive", {"expected_version": 3}, headers=key())
    assert again.json()["error"]["code"] == "CASH_ACCOUNT_ARCHIVED"


def test_all_accounts_whatever_the_locations_of_the_assignment(owner: Client, cmp: str) -> None:
    # § 23.1.11: «all cash desks and accounts of the company», independent of locations.
    phuket = _location(owner, cmp, "Phuket")
    _create(owner, cmp, name="Front desk")
    _create(owner, cmp, name="Bank", type="bank")
    manager, _ = hire(
        owner,
        cmp,
        "manager@example.com",
        [{"type": "set", "set": "rental_manager", "locations": [phuket]}],
    )

    assert _names(manager, cmp) == ["Front desk", "Bank"]


def test_listed_accounts_limit_access(owner: Client, cmp: str) -> None:
    # § 23.1.12: selected accounts without access to the other accounts of the company.
    phuket = _location(owner, cmp, "Phuket")
    chosen = _create(owner, cmp, name="Front desk").json()["cash_account"]["id"]
    _create(owner, cmp, name="Bank", type="bank")
    manager, _ = hire(
        owner,
        cmp,
        "manager@example.com",
        [
            {
                "type": "set",
                "set": "rental_manager",
                "locations": [phuket],
                "cash_accounts": [chosen],
            }
        ],
    )

    assert _names(manager, cmp) == ["Front desk"]


def _invite_with_accounts(owner: Client, cmp: str, accounts: list[str]) -> Any:
    body = {
        "target": "x@example.com",
        "position_name": "Staff",
        "rights": [
            {
                "type": "set",
                "set": "accountant",
                "locations": [_location(owner, cmp, "Phuket")],
                "cash_accounts": accounts,
            }
        ],
    }
    return post(owner, f"/api/c/{cmp}/employees/invitations", body, headers=key())


def test_any_account_of_the_company_may_be_listed(owner: Client, cmp: str) -> None:
    account = _create(owner, cmp).json()["cash_account"]["id"]

    assert _invite_with_accounts(owner, cmp, [account]).status_code == 201


def test_unknown_account_in_rights_is_invalid(owner: Client, cmp: str) -> None:
    response = _invite_with_accounts(owner, cmp, ["01a1628f-ca1c-71ab-a48b-80eede8d99b1"])

    assert response.json()["error"]["code"] == "SCOPE_INVALID"


def test_employee_without_permission_cannot_manage_accounts(owner: Client, cmp: str) -> None:
    account = _create(owner, cmp).json()["cash_account"]
    manager, _ = hire(owner, cmp, "manager@example.com", [{"type": "set", "set": "rental_manager"}])
    driver, _ = hire(owner, cmp, "driver@example.com", [{"type": "set", "set": "driver"}])

    assert _create(manager, cmp).status_code == 403
    rename = manager.patch(
        f"/api/c/{cmp}/cash-accounts/{account['id']}",
        data={"name": "X", "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    assert rename.status_code == 403
    assert _names(driver, cmp) == []
    hidden = post(
        driver,
        f"/api/c/{cmp}/cash-accounts/{account['id']}/archive",
        {"expected_version": 1},
        headers=key(),
    )
    assert hidden.status_code == 404


def test_location_limited_manager_creates_accounts_of_the_company(owner: Client, cmp: str) -> None:
    # Accounts are not bound to locations: «all accounts» of the grant covers the new one.
    phuket = _location(owner, cmp, "Phuket")
    manager, _ = hire(
        owner,
        cmp,
        "fin@example.com",
        [{"type": "grant", "permission": "finance.cash_account.manage", "locations": [phuket]}],
    )

    assert _create(manager, cmp, name="Front desk").status_code == 201
    assert CashAccount.objects.count() == 1


def test_account_of_another_company_is_not_found(owner: Client, cmp: str) -> None:
    account = _create(owner, cmp).json()["cash_account"]
    other = create_company(owner, key="c-2", display_name="Other").json()["company"]["public_id"]

    response = post(
        owner,
        f"/api/c/{other}/cash-accounts/{account['id']}/archive",
        {"expected_version": 1},
        headers=key(),
    )

    assert response.status_code == 404
    assert _names(owner, other) == []
