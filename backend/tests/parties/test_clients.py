"""Client cards and the search for matches (ТЗ § 6.1.9–6.1.11, § 6.2.2, § 6.2.7–6.2.8)."""

from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.test import Client

from c4rsys.companies.models import Company
from c4rsys.parties.models import ClientCard, ClientContact
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db

PERSON = {
    "kind": "person",
    "first_name": "Ivan",
    "last_name": "Petrov",
    "birth_date": "1990-05-17",
    "contacts": [{"kind": "phone", "value": "+66 81-234-5678"}],
}
COMPANY = {
    "kind": "company",
    "company_name": "Andaman Tours",
    "contacts": [{"kind": "email", "value": "Office@Andaman.example"}],
}


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok").json()["company"]["public_id"]
    return public_id


def _create(client: Client, cmp: str, body: dict[str, Any]) -> Any:
    return post(client, f"/api/c/{cmp}/clients", body, headers=key())


def _matches(client: Client, cmp: str, body: dict[str, Any]) -> Any:
    return post(client, f"/api/c/{cmp}/clients/matches", body)


def test_person_card_without_account(owner: Client, cmp: str) -> None:
    # § 6.2.4, § 6.2.7, § 6.2.10: name, surname and one contact; no account needed.
    response = _create(owner, cmp, PERSON)

    assert response.status_code == 201, response.content
    client = response.json()["client"]
    assert (client["kind"], client["first_name"], client["last_name"]) == (
        "person",
        "Ivan",
        "Petrov",
    )
    assert client["birth_date"] == "1990-05-17"
    assert client["contacts"] == [{"kind": "phone", "value": "+66 81-234-5678"}]
    assert response.json()["warnings"] == []
    assert ClientCard.objects.get().linked_user_id is None
    assert ClientContact.objects.get().value_normalized == "+66812345678"
    assert AuditEvent.objects.filter(action="client.created").count() == 1


def test_company_card(owner: Client, cmp: str) -> None:
    # § 6.2.8.
    client = _create(owner, cmp, COMPANY).json()["client"]

    assert client["company_name"] == "Andaman Tours"
    assert ClientContact.objects.get().value_normalized == "office@andaman.example"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({**PERSON, "first_name": ""}, "first_name"),
        ({**PERSON, "last_name": ""}, "last_name"),
        ({**PERSON, "contacts": []}, "contacts"),
        ({**PERSON, "contacts": [{"kind": "telegram", "value": "@ivan"}]}, "contacts[0]"),
        ({**PERSON, "contacts": [{"kind": "phone", "value": "12ab"}]}, "contacts[0]"),
        ({**PERSON, "contacts": [{"kind": "email", "value": "not-mail"}]}, "contacts[0]"),
        ({**PERSON, "birth_date": "2990-01-01"}, "birth_date"),
        ({**PERSON, "birth_date": "17.05.1990"}, "birth_date"),
        ({**COMPANY, "company_name": ""}, "company_name"),
        ({**COMPANY, "first_name": "Ivan"}, "first_name"),
        ({**PERSON, "kind": "robot"}, "kind"),
    ],
)
def test_minimum_of_6_2_7_and_6_2_8(
    owner: Client, cmp: str, body: dict[str, Any], field: str
) -> None:
    response = _create(owner, cmp, body)

    assert response.status_code == 400
    assert response.json()["error"]["field"] == field
    assert not ClientCard.objects.exists()


def test_matches_by_any_field_contacts_first(owner: Client, cmp: str) -> None:
    # § 6.1.11, decision 22.10.2026.
    by_phone = _create(
        owner,
        cmp,
        {
            "kind": "person",
            "first_name": "Anna",
            "last_name": "Smirnova",
            "contacts": [{"kind": "phone", "value": "+66812345678"}],
        },
    ).json()["client"]["id"]
    by_name = _create(
        owner,
        cmp,
        {**PERSON, "first_name": "IVAN", "contacts": [{"kind": "email", "value": "i@x.example"}]},
    ).json()["client"]["id"]
    by_birth = _create(
        owner,
        cmp,
        {
            "kind": "person",
            "first_name": "Oleg",
            "last_name": "petrov",
            "birth_date": "1990-05-17",
            "contacts": [{"kind": "email", "value": "o@x.example"}],
        },
    ).json()["client"]["id"]
    _create(
        owner,
        cmp,
        {
            "kind": "person",
            "first_name": "Other",
            "last_name": "Person",
            "contacts": [{"kind": "phone", "value": "+66 99 999 9999"}],
        },
    )

    found = _matches(
        owner,
        cmp,
        {
            "first_name": "Ivan",
            "last_name": "Petrov",
            "birth_date": "1990-05-17",
            "phone": "+66 (81) 234 5678",
        },
    ).json()["matches"]

    assert [m["client"]["id"] for m in found] == [by_phone, by_name, by_birth]
    assert [m["matched"] for m in found] == [["phone"], ["birth_date", "name"], ["birth_date"]]


def test_company_match_by_name_or_email(owner: Client, cmp: str) -> None:
    created = _create(owner, cmp, COMPANY).json()["client"]["id"]

    by_name = _matches(owner, cmp, {"company_name": "andaman tours"}).json()["matches"]
    by_email = _matches(owner, cmp, {"email": "office@andaman.example"}).json()["matches"]

    assert [m["client"]["id"] for m in by_name] == [created]
    assert by_email[0]["matched"] == ["email"]


def test_duplicate_is_allowed_with_warning(owner: Client, cmp: str) -> None:
    # The manager compares and decides (§ 6.1.11); creation is not blocked.
    first = _create(owner, cmp, PERSON).json()["client"]["id"]

    second = _create(owner, cmp, PERSON)

    assert second.status_code == 201
    assert second.json()["warnings"] == [{"code": "POSSIBLE_DUPLICATES", "clients": [first]}]


def test_change_keeps_minimum(owner: Client, cmp: str) -> None:
    client = _create(owner, cmp, PERSON).json()["client"]
    path = f"/api/c/{cmp}/clients/{client['id']}"

    def patch(data: dict[str, Any]) -> Any:
        return owner.patch(path, data=data, content_type="application/json", headers=key())

    changed = patch(
        {
            "middle_name": "Sergeevich",
            "contacts": [{"kind": "email", "value": "ivan@example.com"}],
            "expected_version": 1,
        }
    )
    assert changed.status_code == 200, changed.content
    assert changed.json()["client"]["contacts"] == [{"kind": "email", "value": "ivan@example.com"}]
    assert patch({"contacts": [], "expected_version": 2}).json()["error"]["field"] == "contacts"
    assert patch({"last_name": "", "expected_version": 2}).json()["error"]["field"] == "last_name"
    assert patch({"kind": "company", "expected_version": 2}).json()["error"]["code"] == (
        "CLIENT_KIND_IMMUTABLE"
    )
    assert patch({"first_name": "Iv", "expected_version": 1}).json()["error"]["code"] == (
        "VERSION_CONFLICT"
    )


def test_list_search_by_part_of_name(owner: Client, cmp: str) -> None:
    # § 2.8.3: search by a part of the name.
    _create(owner, cmp, PERSON)
    _create(owner, cmp, COMPANY)

    names = [
        c["last_name"] or c["company_name"]
        for c in owner.get(f"/api/c/{cmp}/clients?q=petr").json()["clients"]
    ]
    everyone = owner.get(f"/api/c/{cmp}/clients").json()["clients"]

    assert names == ["Petrov"]
    assert len(everyone) == 2


def test_find_and_list_are_separate_rights(owner: Client, cmp: str) -> None:
    # § 6.2.2: finding a client being served does not open lists.
    created = _create(owner, cmp, PERSON).json()["client"]["id"]
    accountant, _ = hire(owner, cmp, "acc@example.com", [{"type": "set", "set": "accountant"}])
    manager, _ = hire(owner, cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])
    driver, _ = hire(owner, cmp, "d@example.com", [{"type": "set", "set": "driver"}])

    assert _matches(accountant, cmp, {"phone": "+66812345678"}).status_code == 200
    assert accountant.get(f"/api/c/{cmp}/clients/{created}").status_code == 200
    assert accountant.get(f"/api/c/{cmp}/clients").status_code == 403
    assert _create(accountant, cmp, COMPANY).status_code == 403
    assert manager.get(f"/api/c/{cmp}/clients").status_code == 200
    assert _create(manager, cmp, COMPANY).status_code == 201
    assert _matches(driver, cmp, {"phone": "+66812345678"}).status_code == 403
    assert driver.get(f"/api/c/{cmp}/clients/{created}").status_code == 403


def test_clients_of_another_company_are_invisible(owner: Client, cmp: str) -> None:
    # § 6.1.9 and § 2.8.1: the base belongs to the company.
    created = _create(owner, cmp, PERSON).json()["client"]["id"]
    other = create_company(owner, key="c-2", display_name="Other").json()["company"]["public_id"]

    assert owner.get(f"/api/c/{other}/clients/{created}").status_code == 404
    assert _matches(owner, other, {"phone": "+66812345678"}).json()["matches"] == []
    assert owner.get(f"/api/c/{other}/clients").json()["clients"] == []


def test_contact_must_belong_to_card_of_same_company(owner: Client, cmp: str) -> None:
    # A05: the database forbids cross-company links.
    _create(owner, cmp, PERSON)
    other = Company.objects.get(
        public_id=create_company(owner, key="c-2", display_name="Other").json()["company"][
            "public_id"
        ]
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        ClientContact.objects.create(
            company=other,
            client=ClientCard.objects.get(),
            kind="email",
            value="x@example.com",
            value_normalized="x@example.com",
        )
