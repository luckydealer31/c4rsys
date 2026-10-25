"""Counterparties, the minimal list (ТЗ § 6.1.4–6.1.9, § 6.2.7–6.2.8, § 6.6.4, § 23.8.2)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.parties.models import ClientCard
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner).json()["company"]["public_id"]
    return public_id


def create(owner: Client, cmp: str, **data: Any) -> Any:
    body = {"kind": "company", "company_name": "Auto Parts Co", **data}
    body.setdefault("contacts", [{"kind": "phone", "value": "+66 81 234 5678"}])
    return post(owner, f"/api/c/{cmp}/counterparties", body, key())


def test_minimum_by_kind_and_a_separate_list(owner: Client, cmp: str) -> None:
    created = create(owner, cmp)
    assert created.status_code == 201, created.content
    card = created.json()["counterparty"]
    assert card["name"] == "Auto Parts Co" and card["state"] == "active"
    # § 6.1.7: a counterparty is not a client.
    assert not ClientCard.objects.exists()

    no_contact = create(owner, cmp, contacts=[])
    assert no_contact.json()["error"]["code"] == "CONTACT_REQUIRED"
    person = create(owner, cmp, kind="person", company_name="", first_name="Somchai")
    assert person.json()["error"]["field"] == "last_name"
    full = create(owner, cmp, kind="person", company_name="", first_name="Somchai", last_name="K")
    assert full.status_code == 201

    found = owner.get(f"/api/c/{cmp}/counterparties?q=parts").json()["counterparties"]
    assert [c["id"] for c in found] == [card["id"]]


def test_change_archive_and_restore(owner: Client, cmp: str) -> None:
    card = create(owner, cmp).json()["counterparty"]
    url = f"/api/c/{cmp}/counterparties/{card['id']}"
    changed = owner.patch(
        url,
        data={"company_name": "Parts Ltd", "comment": "Oils", "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    assert changed.json()["counterparty"]["name"] == "Parts Ltd"
    archived = post(owner, f"{url}/archive", {"expected_version": 2}, key())
    assert archived.json()["counterparty"]["state"] == "archived"
    assert owner.get(f"/api/c/{cmp}/counterparties").json()["counterparties"] == []
    in_archive = owner.get(f"/api/c/{cmp}/counterparties?state=archived").json()
    assert len(in_archive["counterparties"]) == 1
    restored = post(owner, f"{url}/restore", {"expected_version": 3}, key())
    assert restored.json()["counterparty"]["state"] == "active"


def test_rights_are_separate(owner: Client, cmp: str) -> None:
    rights = [
        {
            "type": "grant",
            "permission": "counterparty.view",
            "locations": "all",
            "contexts": "all",
            "cash_accounts": "all",
        }
    ]
    viewer, _ = hire(owner, cmp, "viewer@example.com", rights)
    assert viewer.get(f"/api/c/{cmp}/counterparties").status_code == 200
    assert create(viewer, cmp).status_code == 403
