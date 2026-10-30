"""The request to the owner of a managed vehicle (ТЗ § 8.3.1, § 8.3.3, § 8.3.12–8.3.14, § 7.1.2,
§ 20.3.4; part К5)."""

from datetime import timedelta
from typing import Any, cast

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.fleet import owner_notices, owner_requests
from c4rsys.fleet.imports import ADAPTER
from c4rsys.fleet.models import (
    Vehicle,
    VehicleCard,
    VehicleOwnerClaim,
    VehicleOwnerRequest,
    VehicleOwnership,
)
from c4rsys.notifications.models import Notification
from c4rsys.parties.models import ClientCard
from c4rsys.platform.errors import ApiError
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, key, named_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok").json()["company"]["public_id"]
    return public_id


@pytest.fixture
def location(owner: Client, cmp: str) -> str:
    created = post(owner, f"/api/c/{cmp}/locations", {"name": "Phuket"}, key())
    location_id: str = created.json()["location"]["id"]
    return location_id


def vehicle(owner: Client, cmp: str, location: str, **data: Any) -> dict[str, Any]:
    body = {
        "location_id": location,
        "odometer_unit": "km",
        "name": "Yaris",
        "vin": "JTDBT923771234567",
        **data,
    }
    created = post(owner, f"/api/c/{cmp}/vehicles", body, key())
    assert created.status_code == 201, created.content
    card: dict[str, Any] = created.json()["vehicle"]
    return card


def person(email: str) -> tuple[Client, str]:
    client = Client()
    named_user(client, email)
    public_id: str = client.get("/api/me").json()["user"]["public_id"]
    return client, public_id


def ask(owner: Client, cmp: str, card_id: str) -> Any:
    return post(owner, f"/api/c/{cmp}/vehicles/{card_id}/owner-requests", {}, key())


def answer(client: Client, request_id: str, **given: bool) -> Any:
    return post(client, f"/api/owner-requests/{request_id}/answer", {"answers": given}, key())


def test_three_answers_one_by_one(owner: Client, cmp: str, location: str) -> None:
    somchai, usr = person("somchai@example.com")
    card = vehicle(owner, cmp, location, relation="managed", owner_kind="profile", owner_lookup=usr)
    url = f"/api/c/{cmp}/vehicles/{card['id']}"

    sent = ask(owner, cmp, card["id"])
    assert sent.status_code == 201, sent.content
    request = sent.json()["request"]
    assert request["state"] == "open" and request["answers"] == []
    assert ask(owner, cmp, card["id"]).json()["error"]["code"] == "OWNER_REQUEST_PENDING"

    mine = somchai.get("/api/owner-requests").json()["requests"]
    assert mine[0]["vehicle"]["vin"] == "JTDBT923771234567"
    assert mine[0]["company"]["display_name"] == "Sunrise Rent"

    # § 8.3.1: «it is mine» puts the vehicle into the personal garage by itself.
    first = answer(somchai, request["id"], ownership=True)
    assert first.status_code == 200, first.content
    assert list(owner_requests.garage_of_user(somchai_id(usr))) != []
    shown = owner.get(f"{url}/owner-requests").json()["requests"][0]
    assert shown["state"] == "awaiting_rest"
    assert [(a["question"], a["answer"], a["by"]) for a in shown["answers"]] == [
        ("ownership", True, "Anna Petrova")
    ]
    status = owner.get(url).json()["vehicle"]["owner"]
    assert status["ownership_confirmed"] and not status["confirmed"]

    # § 8.3.14: an answer is never changed.
    again = answer(somchai, request["id"], ownership=False)
    assert again.json()["error"]["code"] == "OWNER_ANSWER_GIVEN"
    assert not ClientCard.objects.exists()
    assert answer(somchai, request["id"], link=True, management=True).status_code == 200
    # «Yes» to the link: the owner becomes a client — a card linked to the profile.
    client = ClientCard.objects.get()
    assert client.linked_user_id == somchai_id(usr)
    assert (client.first_name, client.last_name) == ("Anna", "Petrova")
    assert [c.value for c in client.contacts.all()] == ["somchai@example.com"]
    shown = owner.get(f"/api/c/{cmp}/profile-links?card_type=client&card_id={client.id}").json()
    assert shown["links"][0]["state"] == "accepted"
    status = owner.get(url).json()["vehicle"]["owner"]
    assert status["confirmed"] and not status["conflict"]
    assert owner.get(f"{url}/owner-requests").json()["requests"][0]["state"] == "complete"
    assert ask(owner, cmp, card["id"]).json()["error"]["code"] == "OWNER_ALREADY_CONFIRMED"

    # A confirmed ownership stops the cancel of an import of this card.
    stored = VehicleOwnerRequest.objects.get(pk=request["id"])
    with pytest.raises(ApiError) as blocked:
        ADAPTER.remove(cast(Any, None), stored.company_id, [stored.vehicle_card_id])
    assert blocked.value.params["blockers"][0]["reason"] == "owner_confirmed"

    entry = AuditEvent.objects.filter(action="vehicle.owner_answered").latest("occurred_at")
    assert owner_notices.on_answer(entry) == 1
    assert Notification.objects.filter(event="vehicle.owner_answered").count() == 1


def somchai_id(usr: str) -> Any:
    from c4rsys.accounts.models import UserAccount

    return UserAccount.objects.get(public_id=usr).id


def test_refusal_is_a_conflict_and_a_new_request_keeps_answers(
    owner: Client, cmp: str, location: str
) -> None:
    somchai, usr = person("somchai@example.com")
    card = vehicle(owner, cmp, location, relation="managed", owner_kind="profile", owner_lookup=usr)
    url = f"/api/c/{cmp}/vehicles/{card['id']}"
    first = ask(owner, cmp, card["id"]).json()["request"]
    no = answer(somchai, first["id"], link=False, ownership=False, management=False)
    assert no.status_code == 200, no.content
    status = owner.get(url).json()["vehicle"]["owner"]
    # § 8.3.3: «no / no / no» is a conflict; the card works on and nothing goes to the garage.
    assert status["conflict"] and not status["ownership_confirmed"]
    assert not VehicleOwnership.objects.exists()

    assert not ClientCard.objects.exists()
    second = ask(owner, cmp, card["id"])
    assert second.status_code == 201, second.content
    history = owner.get(f"{url}/owner-requests").json()["requests"]
    assert [r["state"] for r in history] == ["open", "complete"]
    assert len(history[1]["answers"]) == 3

    # An incomplete answer: the next request supersedes it and keeps its answer.
    answer(somchai, second.json()["request"]["id"], link=True)
    assert ClientCard.objects.count() == 1
    third = ask(owner, cmp, card["id"])
    assert third.status_code == 201
    states = [r["state"] for r in owner.get(f"{url}/owner-requests").json()["requests"]]
    assert states == ["open", "superseded", "complete"]

    cancelled = post(owner, f"{url}/owner-requests/cancel", {}, key())
    assert cancelled.json()["request"]["state"] == "cancelled"
    late = answer(somchai, third.json()["request"]["id"], link=True)
    assert late.json()["error"]["code"] == "OWNER_REQUEST_NOT_OPEN"


def test_who_may_be_asked(owner: Client, cmp: str, location: str) -> None:
    own = vehicle(owner, cmp, location)
    assert ask(owner, cmp, own["id"]).json()["error"]["code"] == "OWNER_REQUEST_MANAGED_ONLY"
    manual = vehicle(
        owner,
        cmp,
        location,
        relation="managed",
        owner_kind="person",
        owner_name="Somchai",
        vin="JTDBT923771234568",
    )
    assert ask(owner, cmp, manual["id"]).json()["error"]["code"] == "OWNER_PROFILE_REQUIRED"

    by_email = vehicle(
        owner,
        cmp,
        location,
        relation="managed",
        owner_kind="profile",
        owner_lookup="Later@Example.com",
        vin="JTDBT923771234569",
    )
    request = ask(owner, cmp, by_email["id"]).json()["request"]
    VehicleOwnerRequest.objects.filter(pk=request["id"]).update(
        expires_at=timezone.now() - timedelta(1)
    )
    # The owner registers later with the address and sees the request; it has expired.
    later, _ = person("later@example.com")
    found = later.get("/api/owner-requests").json()["requests"]
    assert found[0]["state"] == "expired"
    assert answer(later, request["id"], link=True).json()["error"]["code"] == (
        "OWNER_REQUEST_EXPIRED"
    )
    assert ask(owner, cmp, by_email["id"]).status_code == 201


def test_company_owner_and_a_dispute(owner: Client, cmp: str, location: str) -> None:
    lessor = Client()
    named_user(lessor, "boss@example.com")
    other = create_company(lessor, display_name="Fleet Owner").json()["company"]["public_id"]
    card = vehicle(
        owner, cmp, location, relation="managed", owner_kind="profile", owner_lookup=other.lower()
    )
    request = ask(owner, cmp, card["id"]).json()["request"]
    assert owner_notices.requests(timezone.now()) == 1
    assert Notification.objects.get(event="vehicle.owner_requested").company.public_id == other

    incoming = lessor.get(f"/api/c/{other}/incoming-owner-requests").json()["requests"]
    assert incoming[0]["id"] == request["id"]
    given = post(
        lessor,
        f"/api/c/{other}/incoming-owner-requests/{request['id']}/answer",
        {"answers": {"link": True, "ownership": True, "management": True}},
        key(),
    )
    assert given.status_code == 200, given.content
    ownership = VehicleOwnership.objects.get()
    client = ClientCard.objects.get()
    assert client.kind == "company" and client.company_name == "Fleet Owner"
    assert client.linked_company_id == ownership.owner_company_id
    assert ownership.owner_company_id is not None and ownership.ended_at is None
    owner_notices.requests(timezone.now())
    assert Notification.objects.get(event="vehicle.owner_requested").state == "done"


def test_ownership_dispute_is_not_resolved_automatically(
    owner: Client, cmp: str, location: str
) -> None:
    claimant, usr = person("claimant@example.com")
    _, first_usr = person("first@example.com")
    card = vehicle(owner, cmp, location, relation="managed", owner_kind="profile", owner_lookup=usr)
    request = ask(owner, cmp, card["id"]).json()["request"]
    stored = VehicleOwnerRequest.objects.get(pk=request["id"])
    VehicleOwnership.objects.create(
        vehicle_id=stored.vehicle_id, owner_user_id=somchai_id(first_usr), started_at=timezone.now()
    )
    assert answer(claimant, request["id"], ownership=True).status_code == 200
    # § 8.3.12: the confirmed owner stays; the claim waits for the support.
    url = f"/api/c/{cmp}/vehicles/{card['id']}/owner-requests"
    assert owner.get(url).json()["requests"][0]["ownership_dispute"] is True
    assert VehicleOwnership.objects.get().owner_user_id == somchai_id(first_usr)
    assert list(owner_requests.garage_of_user(somchai_id(usr))) == []


def put_owner(owner: Client, cmp: str, card: dict[str, Any], lookup: str) -> None:
    current = owner.get(f"/api/c/{cmp}/vehicles/{card['id']}").json()["vehicle"]
    body = {
        "expected_version": current["version"],
        "name": "Yaris",
        "vin": "JTDBT923771234567",
        "owner_kind": "profile",
        "owner_lookup": lookup,
    }
    changed = owner.put(
        f"/api/c/{cmp}/vehicles/{card['id']}/details",
        data=body,
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content


def test_a_new_owner_closes_the_open_request(owner: Client, cmp: str, location: str) -> None:
    previous, usr = person("previous@example.com")
    newcomer, new_usr = person("newcomer@example.com")
    card = vehicle(owner, cmp, location, relation="managed", owner_kind="profile", owner_lookup=usr)
    old = ask(owner, cmp, card["id"]).json()["request"]

    put_owner(owner, cmp, card, new_usr)
    # The request to the previous owner is cancelled with the reason and is not answered.
    history = owner.get(f"/api/c/{cmp}/vehicles/{card['id']}/owner-requests").json()["requests"]
    assert [(r["state"], r["closed_reason"]) for r in history] == [("cancelled", "owner_changed")]
    entry = AuditEvent.objects.get(action="vehicle.owner_request_cancelled")
    assert entry.after == {"request": old["id"], "reason": "owner_changed"}
    late = answer(previous, old["id"], ownership=True, link=True)
    assert late.json()["error"]["code"] == "OWNER_REQUEST_NOT_OPEN"
    assert not VehicleOwnership.objects.exists() and not ClientCard.objects.exists()

    # The new owner is asked at once.
    sent = ask(owner, cmp, card["id"])
    assert sent.status_code == 201, sent.content
    assert answer(newcomer, sent.json()["request"]["id"], ownership=True).status_code == 200


def test_an_answer_is_checked_against_the_current_card(
    owner: Client, cmp: str, location: str
) -> None:
    previous, usr = person("previous@example.com")
    card = vehicle(owner, cmp, location, relation="managed", owner_kind="profile", owner_lookup=usr)
    request = ask(owner, cmp, card["id"]).json()["request"]
    stored = VehicleOwnerRequest.objects.get(pk=request["id"])

    # A declaration that did not close the request (data of before the fix): the answer is
    # refused, and sending a new request closes the old one.
    VehicleOwnerClaim.objects.create(
        company_id=stored.company_id,
        vehicle_card_id=stored.vehicle_card_id,
        kind=VehicleOwnerClaim.Kind.PROFILE,
        lookup="other@example.com",
        created_by_user_id=stored.created_by_user_id,
    )
    refused = answer(previous, request["id"], ownership=True)
    assert refused.json()["error"]["code"] == "OWNER_REQUEST_NOT_OPEN"
    assert ask(owner, cmp, card["id"]).status_code == 201
    stored.refresh_from_db()
    assert (stored.state, stored.closed_reason) == ("cancelled", "owner_changed")

    # A card archived after the request is not answered either.
    archived = vehicle(
        owner,
        cmp,
        location,
        relation="managed",
        owner_kind="profile",
        owner_lookup=usr,
        vin="JTDBT923771234570",
    )
    second = ask(owner, cmp, archived["id"]).json()["request"]
    VehicleCard.objects.filter(pk=archived["id"]).update(state=VehicleCard.State.ARCHIVED)
    late = answer(previous, second["id"], ownership=True)
    assert late.json()["error"]["code"] == "OWNER_REQUEST_NOT_OPEN"
    assert not VehicleOwnership.objects.exists()


def test_ownership_confirmed_earlier_by_the_same_owner_is_no_dispute(
    owner: Client, cmp: str, location: str
) -> None:
    somchai, usr = person("somchai@example.com")
    card = vehicle(owner, cmp, location, relation="managed", owner_kind="profile", owner_lookup=usr)
    url = f"/api/c/{cmp}/vehicles/{card['id']}/owner-requests"
    first = ask(owner, cmp, card["id"]).json()["request"]
    assert answer(somchai, first["id"], ownership=True).status_code == 200
    # Incomplete: a new request; «mine» again finds the ownership of the same owner.
    second = ask(owner, cmp, card["id"]).json()["request"]
    assert answer(somchai, second["id"], ownership=True).status_code == 200
    entry = AuditEvent.objects.filter(action="vehicle.owner_answered").latest("occurred_at")
    assert (entry.after or {})["ownership"] == "already"
    shown = owner.get(url).json()["requests"]
    assert [r["ownership_dispute"] for r in shown] == [False, False]
    assert VehicleOwnership.objects.count() == 1


def test_owner_yes_links_the_card_of_a_broken_link_again(
    owner: Client, cmp: str, location: str
) -> None:
    somchai, usr = person("somchai@example.com")
    body = {
        "kind": "person",
        "first_name": "Somchai",
        "last_name": "K",
        "contacts": [{"kind": "phone", "value": "+66 81 234 5678"}],
    }
    client_id = post(owner, f"/api/c/{cmp}/clients", body, key()).json()["client"]["id"]
    link_body = {"card_type": "client", "card_id": client_id, "target": usr}
    link = post(owner, f"/api/c/{cmp}/profile-links", link_body, key()).json()["link"]
    accepted = post(
        somchai, f"/api/profile-links/{link['id']}/accept", {"fields": ["first_name"]}, key()
    )
    assert accepted.status_code == 200, accepted.content
    broken = post(
        owner, f"/api/c/{cmp}/profile-links/{link['id']}/break", {"initiator": "company"}, key()
    )
    assert broken.status_code == 200, broken.content

    card = vehicle(owner, cmp, location, relation="managed", owner_kind="profile", owner_lookup=usr)
    request = ask(owner, cmp, card["id"]).json()["request"]
    assert answer(somchai, request["id"], link=True).status_code == 200
    # § 6.6.6: after a new request is accepted the previous card is linked; no new card.
    linked = ClientCard.objects.get()
    assert str(linked.id) == client_id and linked.linked_user_id == somchai_id(usr)
    url = f"/api/c/{cmp}/profile-links?card_type=client&card_id={client_id}"
    assert [x["state"] for x in owner.get(url).json()["links"]] == ["accepted", "broken"]
    relinked = AuditEvent.objects.filter(action="profile_link.accepted").latest("occurred_at")
    assert (relinked.after or {})["source"] == "vehicle_owner"

    # A second «yes» keeps the linked card.
    other = vehicle(
        owner,
        cmp,
        location,
        relation="managed",
        owner_kind="profile",
        owner_lookup=usr,
        vin="JTDBT923771234571",
    )
    again = ask(owner, cmp, other["id"]).json()["request"]
    assert answer(somchai, again["id"], link=True).status_code == 200
    assert ClientCard.objects.count() == 1


def test_the_vehicle_is_locked_before_the_ownership_is_recorded(
    owner: Client, cmp: str, location: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two first owners answering at once are serialized on the global vehicle: the later one
    finds the earlier ownership and becomes a dispute instead of a unique violation."""
    somchai, usr = person("somchai@example.com")
    card = vehicle(owner, cmp, location, relation="managed", owner_kind="profile", owner_lookup=usr)
    request = ask(owner, cmp, card["id"]).json()["request"]
    locked: list[str] = []
    original = Vehicle.objects.select_for_update

    def spy(*args: Any, **kwargs: Any) -> Any:
        locked.append("vehicle")
        return original(*args, **kwargs)

    monkeypatch.setattr(Vehicle.objects, "select_for_update", spy)
    assert answer(somchai, request["id"], ownership=True).status_code == 200
    assert locked == ["vehicle"]
