"""Cash desks and bank accounts, and their permission scope (ТЗ § 17.2.5, § 22.1.7,
§ 23.1.11–23.1.12)."""

from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.test import Client

from c4rsys.companies.models import Company, Location
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
    assert account["location_id"] is None
    assert account["state"] == "active"
    assert AuditEvent.objects.filter(action="cash_account.created").count() == 1


def test_currency_required_without_main_currency(owner: Client) -> None:
    cmp = create_company(owner).json()["company"]["public_id"]

    response = _create(owner, cmp)

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "currency"


def test_bank_account_bound_to_location_in_other_currency(owner: Client, cmp: str) -> None:
    # § 17.1.1, § 17.2.5.
    location_id = _location(owner, cmp, "Phuket")

    account = _create(
        owner, cmp, name="Bangkok Bank", type="bank", currency="usd", location_id=location_id
    ).json()["cash_account"]

    assert (account["type"], account["currency"], account["location_id"]) == (
        "bank",
        "USD",
        location_id,
    )


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"name": ""}, "name"),
        ({"type": "card"}, "type"),
        ({"currency": "XXX"}, "currency"),
        ({"location_id": "01a1628f-ca1c-71ab-a48b-80eede8d99b1"}, "location_id"),
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


def test_scope_by_location_hides_other_and_shared_accounts(owner: Client, cmp: str) -> None:
    # Decision 22.10.2026: «all» within locations covers their accounts, not shared ones.
    phuket = _location(owner, cmp, "Phuket")
    krabi = _location(owner, cmp, "Krabi")
    _create(owner, cmp, name="Shared")
    _create(owner, cmp, name="Phuket cash", location_id=phuket)
    _create(owner, cmp, name="Krabi cash", location_id=krabi)
    manager, _ = hire(
        owner,
        cmp,
        "manager@example.com",
        [{"type": "set", "set": "rental_manager", "locations": [phuket]}],
    )

    assert _names(manager, cmp) == ["Phuket cash"]
    assert _names(owner, cmp) == ["Shared", "Phuket cash", "Krabi cash"]


def test_whole_company_scope_includes_shared_accounts(owner: Client, cmp: str) -> None:
    phuket = _location(owner, cmp, "Phuket")
    _create(owner, cmp, name="Shared")
    _create(owner, cmp, name="Phuket cash", location_id=phuket)
    accountant, _ = hire(owner, cmp, "acc@example.com", [{"type": "set", "set": "accountant"}])

    assert _names(accountant, cmp) == ["Shared", "Phuket cash"]


def test_shared_account_listed_for_location_employee(owner: Client, cmp: str) -> None:
    # § 23.1.12: a selected shared account without access to other locations or accounts.
    phuket = _location(owner, cmp, "Phuket")
    krabi = _location(owner, cmp, "Krabi")
    shared = _create(owner, cmp, name="Shared").json()["cash_account"]["id"]
    _create(owner, cmp, name="Other shared")
    _create(owner, cmp, name="Krabi cash", location_id=krabi)
    manager, _ = hire(
        owner,
        cmp,
        "manager@example.com",
        [
            {
                "type": "set",
                "set": "rental_manager",
                "locations": [phuket],
                "cash_accounts": [shared],
            }
        ],
    )

    assert _names(manager, cmp) == ["Shared"]


def test_listed_account_of_another_location_is_invalid(owner: Client, cmp: str) -> None:
    phuket = _location(owner, cmp, "Phuket")
    krabi = _location(owner, cmp, "Krabi")
    krabi_cash = _create(owner, cmp, location_id=krabi).json()["cash_account"]["id"]
    body = {
        "target": "x@example.com",
        "position_name": "Staff",
        "rights": [
            {
                "type": "set",
                "set": "accountant",
                "locations": [phuket],
                "cash_accounts": [krabi_cash],
            }
        ],
    }

    response = post(owner, f"/api/c/{cmp}/employees/invitations", body, headers=key())

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


def test_location_limited_manager_cannot_create_shared_account(owner: Client, cmp: str) -> None:
    phuket = _location(owner, cmp, "Phuket")
    manager, _ = hire(
        owner,
        cmp,
        "fin@example.com",
        [{"type": "grant", "permission": "finance.cash_account.manage", "locations": [phuket]}],
    )

    assert _create(manager, cmp, location_id=phuket).status_code == 201
    assert _create(manager, cmp, name="Shared").status_code == 403
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


def test_location_of_account_must_belong_to_same_company(owner: Client, cmp: str) -> None:
    # A05: the database forbids cross-company links.
    _location(owner, cmp, "Phuket")
    other = Company.objects.get(
        public_id=create_company(owner, key="c-2", display_name="Other").json()["company"][
            "public_id"
        ]
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        CashAccount.objects.create(
            company=other,
            name="X",
            type="cash",
            currency_id="THB",
            location=Location.objects.get(),
            created_by_user_id=other.created_by_id,
        )
