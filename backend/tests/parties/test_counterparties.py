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


PHONE = [{"kind": "phone", "value": "+66 81 234 5678"}]


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


def test_a_category_is_a_tag_and_the_lists_are_filtered_by_tags(owner: Client, cmp: str) -> None:
    """Customer's decisions of 28.11.2026: the category of a counterparty is a tag — what is
    named as a category is put on the card as a tag of the company; the lists of the clients,
    the investors and the counterparties are filtered by tags; an investor carries tags too."""
    parts = create(owner, cmp, company_name="Parts Co", category="Parts").json()["counterparty"]
    create(owner, cmp, company_name="Wash Co", category="Washing")
    other = create(owner, cmp, company_name="Other Parts", category="parts").json()["counterparty"]
    create(owner, cmp, company_name="No Category")
    assert parts["category"] == ""
    assert owner.get(f"/api/c/{cmp}/counterparties/categories").json()["categories"] == []
    tags = {t["name"]: t for t in owner.get(f"/api/c/{cmp}/tags").json()["tags"]}
    assert sorted(tags) == ["Parts", "Washing"] and tags["Parts"]["count"] == 2
    url = f"/api/c/{cmp}/counterparties"
    found = owner.get(f"{url}?tags={tags['Parts']['id']}").json()["counterparties"]
    assert sorted(c["name"] for c in found) == ["Other Parts", "Parts Co"]
    # Every chosen tag must be on the card.
    both = f"{tags['Parts']['id']},{tags['Washing']['id']}"
    assert owner.get(f"{url}?tags={both}").json()["counterparties"] == []
    assert owner.get(f"{url}?tags=nonsense").status_code == 400
    # A category given in a change of the card becomes a tag as well.
    changed = owner.patch(
        f"{url}/{other['id']}",
        data={"category": "Washing", "expected_version": other["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    assert len(owner.get(f"{url}?tags={both}").json()["counterparties"]) == 1

    # An investor and a client carry tags; their lists are filtered the same way.
    investor = post(
        owner,
        f"/api/c/{cmp}/investors",
        {"kind": "company", "company_name": "Capital", "contacts": PHONE},
        key(),
    ).json()["counterparty"]
    post(
        owner,
        f"/api/c/{cmp}/investors",
        {"kind": "company", "company_name": "Plain", "contacts": PHONE},
        key(),
    )
    put = owner.put(
        f"/api/c/{cmp}/tag-links",
        data={"type": "investor", "id": investor["id"], "tag_ids": [tags["Parts"]["id"]]},
        content_type="application/json",
        headers=key(),
    )
    assert put.status_code == 200, put.content
    links = owner.get(f"/api/c/{cmp}/tag-links?type=investor").json()["links"]
    assert links == {investor["id"]: [tags["Parts"]["id"]]}
    listed = owner.get(f"/api/c/{cmp}/investors?tags={tags['Parts']['id']}").json()
    assert [c["name"] for c in listed["counterparties"]] == ["Capital"]
    # The tag of the investor is not a tag of a counterparty with the same id.
    assert (
        investor["id"] not in owner.get(f"/api/c/{cmp}/tag-links?type=counterparty").json()["links"]
    )
    client = post(
        owner,
        f"/api/c/{cmp}/clients",
        {"kind": "company", "company_name": "Client Co", "contacts": PHONE},
        key(),
    ).json()["client"]
    post(
        owner,
        f"/api/c/{cmp}/clients",
        {"kind": "company", "company_name": "Else", "contacts": PHONE},
        key(),
    )
    owner.put(
        f"/api/c/{cmp}/tag-links",
        data={"type": "client", "id": client["id"], "tag_ids": [tags["Washing"]["id"]]},
        content_type="application/json",
        headers=key(),
    )
    listed = owner.get(f"/api/c/{cmp}/clients?tags={tags['Washing']['id']}").json()["clients"]
    assert [c["name"] for c in listed] == ["Client Co"]


def test_a_person_counterparty_has_a_birth_date(owner: Client, cmp: str) -> None:
    """Customer's remark of 25.11.2026: a person counterparty has a birth date (§ 6.2.12)."""
    person = {"kind": "person", "company_name": "", "first_name": "Timofey", "last_name": "S"}
    card = create(owner, cmp, **person, birth_date="1990-05-04").json()["counterparty"]
    assert card["birth_date"] == "1990-05-04"
    url = f"/api/c/{cmp}/counterparties/{card['id']}"
    changed = owner.patch(
        url,
        data={"birth_date": "1991-06-07", "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    assert changed.json()["counterparty"]["birth_date"] == "1991-06-07"
    company = create(owner, cmp, birth_date="1990-05-04")
    assert company.json()["error"]["field"] == "birth_date"


def test_the_card_tells_with_whom_it_may_be_a_duplicate(owner: Client, cmp: str) -> None:
    """Customer's remark of 25.11.2026: the mark «Possible duplicate» shows with whom and by
    what the card matches."""
    from c4rsys.parties.models import Counterparty

    email = [{"kind": "email", "value": "tima@example.com"}]
    first = create(owner, cmp, contacts=email).json()["counterparty"]
    person = {"kind": "person", "company_name": "", "first_name": "Timofey", "last_name": "S"}
    second = create(owner, cmp, **person, contacts=email).json()["counterparty"]
    url = f"/api/c/{cmp}/counterparties/{second['id']}"
    assert owner.get(url).json()["counterparty"]["duplicates"] == []
    Counterparty.objects.filter(pk=second["id"]).update(possible_duplicate=True)
    assert owner.get(url).json()["counterparty"]["duplicates"] == [
        {"id": first["id"], "name": "Auto Parts Co", "kind": "company", "matched": ["email"]}
    ]


def test_the_mark_of_a_duplicate_follows_the_current_data(owner: Client, cmp: str) -> None:
    """Customer's remark of 25.11.2026: once the matching data is corrected, the mark goes away."""
    from c4rsys.parties.models import Counterparty

    email = [{"kind": "email", "value": "tima@example.com"}]
    first = create(owner, cmp, contacts=email).json()["counterparty"]
    person = {"kind": "person", "company_name": "", "first_name": "Timofey", "last_name": "S"}
    second = create(owner, cmp, **person, contacts=email).json()["counterparty"]
    Counterparty.objects.filter(pk=second["id"]).update(possible_duplicate=True)
    # The other card changes its email: the marked card matches nothing any more.
    changed = owner.patch(
        f"/api/c/{cmp}/counterparties/{first['id']}",
        data={"contacts": [{"kind": "email", "value": "parts@example.com"}], "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    url = f"/api/c/{cmp}/counterparties/{second['id']}"
    card = owner.get(url).json()["counterparty"]
    assert card["possible_duplicate"] is False and card["duplicates"] == []
    # The card itself takes the email back: it is marked again.
    owner.patch(
        url,
        data={"contacts": [{"kind": "email", "value": "parts@example.com"}], "expected_version": 1},
        content_type="application/json",
        headers=key(),
    )
    assert owner.get(url).json()["counterparty"]["possible_duplicate"] is True
