"""One person or company behind the cards of its roles (customer's decisions of 28.11.2026)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.parties.models import ClientCard, Counterparty
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db

PHONE = [{"kind": "phone", "value": "+66 81 234 5678"}]
PERSON = {"kind": "person", "first_name": "Timofey", "last_name": "Shchukin", "contacts": PHONE}


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner).json()["company"]["public_id"]
    return public_id


def roles(client: Client, cmp: str, card_type: str, card_id: str) -> Any:
    return client.get(f"/api/c/{cmp}/persons?card_type={card_type}&card_id={card_id}").json()


def act(client: Client, cmp: str, action: str, **body: Any) -> Any:
    return post(client, f"/api/c/{cmp}/persons/{action}", body, key())


def patch(client: Client, url: str, **body: Any) -> Any:
    return client.patch(url, data=body, content_type="application/json", headers=key())


def test_a_role_is_added_and_the_data_stay_the_same(owner: Client, cmp: str) -> None:
    investor = post(
        owner, f"/api/c/{cmp}/investors", {**PERSON, "comment": "Gave two bikes"}, key()
    )
    investor = investor.json()["counterparty"]
    alone = roles(owner, cmp, "investor", investor["id"])
    assert [(r["role"], r["current"]) for r in alone["roles"]] == [("investor", True)]
    assert alone["can_add"] == ["client", "counterparty"] and alone["employee"] is None

    made = act(owner, cmp, "add-role", card_type="investor", card_id=investor["id"], role="client")

    assert made.status_code == 201, made.content
    client_id = made.json()["id"]
    client = ClientCard.objects.get(pk=client_id)
    assert (client.first_name, client.last_name, client.comment) == ("Timofey", "Shchukin", "")
    assert [c.value for c in client.contacts.all()] == ["+66 81 234 5678"]
    both = roles(owner, cmp, "client", client_id)
    assert [(r["role"], r["current"]) for r in both["roles"]] == [
        ("client", True),
        ("investor", False),
    ]
    # The comment is of the role and is told with its name.
    assert [r["comment"] for r in both["roles"]] == ["", "Gave two bikes"]
    assert both["can_add"] == ["counterparty"]
    # A role is not taken twice.
    again = act(owner, cmp, "add-role", card_type="client", card_id=client_id, role="investor")
    assert again.json()["error"]["code"] == "PARTY_ROLE_TAKEN"

    # The data are one: changed in a role, they are changed in the other; the comment is not.
    changed = patch(
        owner,
        f"/api/c/{cmp}/clients/{client_id}",
        last_name="Shchukin-Ivanov",
        birth_date="1990-02-03",
        comment="Pays late",
        contacts=[{"kind": "phone", "value": "+66 80 000 1111"}],
        id_document={"type": "passport", "number": "75 1234567"},
        expected_version=client.version,
    )
    assert changed.status_code == 200, changed.content
    same = Counterparty.objects.get(pk=investor["id"])
    assert (same.last_name, str(same.birth_date), same.comment) == (
        "Shchukin-Ivanov",
        "1990-02-03",
        "Gave two bikes",
    )
    assert same.id_document is not None and same.id_document["number"] == "75 1234567"
    assert [c.value for c in same.contacts.all()] == ["+66 80 000 1111"]
    # And back, from the card of the investor.
    back = patch(
        owner,
        f"/api/c/{cmp}/investors/{investor['id']}",
        first_name="Tim",
        expected_version=same.version,
    )
    assert back.status_code == 200, back.content
    assert ClientCard.objects.get(pk=client_id).first_name == "Tim"

    # A tag is of the being: put on a role, it stands on the other; taken off — off both.
    tag = post(
        owner, f"/api/c/{cmp}/tags", {"name": "VIP", "color": "red", "personal": False}, key()
    )
    tag_id = tag.json()["tag"]["id"]
    put = owner.put(
        f"/api/c/{cmp}/tag-links",
        data={"type": "client", "id": client_id, "tag_ids": [tag_id]},
        content_type="application/json",
        headers=key(),
    )
    assert put.status_code == 200, put.content
    links = owner.get(f"/api/c/{cmp}/tag-links?type=investor").json()["links"]
    assert links == {investor["id"]: [tag_id]}
    owner.put(
        f"/api/c/{cmp}/tag-links",
        data={"type": "investor", "id": investor["id"], "tag_ids": []},
        content_type="application/json",
        headers=key(),
    )
    assert owner.get(f"/api/c/{cmp}/tag-links?type=client").json()["links"] == {}
    # The archive is of the role: the investor is archived, the client is not.
    archived = post(
        owner,
        f"/api/c/{cmp}/investors/{investor['id']}/archive",
        {"expected_version": Counterparty.objects.get(pk=investor["id"]).version},
        key(),
    )
    assert archived.status_code == 200, archived.content
    assert ClientCard.objects.get(pk=client_id).state == "active"


def test_cards_are_joined_by_hand_with_a_right(owner: Client, cmp: str) -> None:
    client = post(owner, f"/api/c/{cmp}/clients", PERSON, key()).json()["client"]
    investor = post(
        owner,
        f"/api/c/{cmp}/investors",
        {**PERSON, "first_name": "Timofei", "contacts": PHONE},
        key(),
    ).json()["counterparty"]
    company = post(
        owner,
        f"/api/c/{cmp}/counterparties",
        {"kind": "company", "company_name": "Parts Co", "contacts": PHONE},
        key(),
    ).json()["counterparty"]
    # The cards alike are offered to whoever may join.
    seen = roles(owner, cmp, "client", client["id"])
    assert seen["may_join"] is True
    assert [(o["role"], o["id"]) for o in seen["offered"]] == [("investor", investor["id"])]
    assert "phone" in seen["offered"][0]["matched"]
    # Without the right the cards are not joined, and nothing is offered.
    clerk, _ = hire(
        owner,
        cmp,
        "clerk@example.com",
        [
            {"type": "grant", "permission": p, "locations": "all"}
            for p in ("client.list", "investor.view")
        ],
    )
    limited = roles(clerk, cmp, "client", client["id"])
    assert (limited["may_join"], limited["offered"]) == (False, [])
    body = {
        "card_type": "client",
        "card_id": client["id"],
        "other_type": "investor",
        "other_id": investor["id"],
    }
    assert act(clerk, cmp, "join", **body).status_code == 403
    # A person and a company are not one being.
    wrong = act(
        owner, cmp, "join", **{**body, "other_type": "counterparty", "other_id": company["id"]}
    )
    assert wrong.json()["error"]["code"] == "PARTY_KIND_DIFFERS"

    joined = act(owner, cmp, "join", **body)

    assert joined.status_code == 200, joined.content
    assert [r["role"] for r in joined.json()["roles"]] == ["client", "investor"]
    # The data of the card the join was made from stay.
    assert Counterparty.objects.get(pk=investor["id"]).first_name == "Timofey"
    assert act(owner, cmp, "join", **body).json()["error"]["code"] == "PARTY_ALREADY_JOINED"
    # Whoever sees one role only is told that there is more, not what.
    viewer, _ = hire(
        owner,
        cmp,
        "viewer@example.com",
        [{"type": "grant", "permission": "client.list", "locations": "all"}],
    )
    partly = roles(viewer, cmp, "client", client["id"])
    assert ([r["role"] for r in partly["roles"]], partly["hidden"]) == (["client"], 1)
    assert roles(viewer, cmp, "investor", investor["id"])["error"]["code"] == "PERMISSION_DENIED"

    left = act(owner, cmp, "leave", card_type="investor", card_id=investor["id"])
    assert left.status_code == 200, left.content
    assert [r["role"] for r in roles(owner, cmp, "client", client["id"])["roles"]] == ["client"]
    assert (
        act(owner, cmp, "leave", card_type="client", card_id=client["id"]).json()["error"]["code"]
        == "PARTY_ALONE"
    )
