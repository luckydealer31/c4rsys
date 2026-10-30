"""Linking cards to c4rsys profiles (ТЗ § 6.2.5–6.3, § 6.4.2, § 6.6.2–6.6.6, § 20.3.4; part К4)."""

from datetime import timedelta
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from c4rsys.notifications.models import Notification
from c4rsys.parties import links, notices
from c4rsys.parties.models import ClientCard, Counterparty, ProfileLink
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key, named_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner).json()["company"]["public_id"]
    return public_id


def person(owner: Client, cmp: str, phone: str = "+66 81 234 5678") -> str:
    body = {
        "kind": "person",
        "first_name": "Somchai",
        "last_name": "K",
        "contacts": [{"kind": "phone", "value": phone}],
    }
    created = post(owner, f"/api/c/{cmp}/clients", body, key())
    assert created.status_code == 201, created.content
    card_id: str = created.json()["client"]["id"]
    return card_id


def ask(owner: Client, cmp: str, card_id: str, target: str, card_type: str = "client") -> Any:
    body = {"card_type": card_type, "card_id": card_id, "target": target}
    return post(owner, f"/api/c/{cmp}/profile-links", body, key())


def links_of(owner: Client, cmp: str, card_id: str, card_type: str = "client") -> list[Any]:
    url = f"/api/c/{cmp}/profile-links?card_type={card_type}&card_id={card_id}"
    found: list[Any] = owner.get(url).json()["links"]
    return found


def grant(permission: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "grant",
            "permission": permission,
            "locations": "all",
            "contexts": "all",
            "cash_accounts": "all",
        }
    ]


def user_client(email: str) -> tuple[Client, str]:
    client = Client()
    named_user(client, email)
    public_id: str = client.get("/api/me").json()["user"]["public_id"]
    return client, public_id


def test_request_by_id_accept_apply_and_break(owner: Client, cmp: str) -> None:
    card_id = person(owner, cmp)
    somchai, usr = user_client("somchai@example.com")

    sent = ask(owner, cmp, card_id, usr.lower())
    assert sent.status_code == 201, sent.content
    link = sent.json()["link"]
    assert link["state"] == "pending" and link["target"] == usr
    # § 6.3.1: nothing of the profile before the answer.
    assert link["linked_profile"] is None and link["shared"] == {}
    assert ask(owner, cmp, card_id, usr).json()["error"]["code"] == "LINK_REQUEST_PENDING"

    mine = somchai.get("/api/profile-links").json()["links"]
    assert [m["id"] for m in mine] == [link["id"]]
    assert mine[0]["company"]["display_name"] == "Sunrise Rent"
    assert mine[0]["available"]["email"] == "somchai@example.com"
    accepted = post(
        somchai,
        f"/api/profile-links/{link['id']}/accept",
        {"fields": ["first_name", "last_name", "email"]},
        key(),
    )
    assert accepted.status_code == 200, accepted.content
    card = ClientCard.objects.get(pk=card_id)
    assert card.linked_user_id is not None
    # § 6.4.2: the working fields of the card do not change by themselves.
    assert card.last_name == "K"

    shown = links_of(owner, cmp, card_id)[0]
    assert shown["state"] == "accepted" and shown["linked_profile"]["public_id"] == usr
    assert shown["shared"] == {
        "first_name": "Anna",
        "last_name": "Petrova",
        "email": "somchai@example.com",
    }

    version = owner.get(f"/api/c/{cmp}/clients/{card_id}").json()["client"]["version"]
    for field in ("email", "last_name"):
        applied = post(
            owner,
            f"/api/c/{cmp}/profile-links/{link['id']}/apply",
            {"field": field, "expected_version": version},
            key(),
        )
        assert applied.status_code == 200, applied.content
        version = applied.json()["version"]
    client = owner.get(f"/api/c/{cmp}/clients/{card_id}").json()["client"]
    assert client["last_name"] == "Petrova"
    assert {c["value"] for c in client["contacts"]} == {"+66 81 234 5678", "somchai@example.com"}
    assert AuditEvent.objects.filter(action="profile_link.value_applied").count() == 2

    broken = post(
        owner, f"/api/c/{cmp}/profile-links/{link['id']}/break", {"initiator": "client"}, key()
    )
    assert broken.json()["link"]["state"] == "broken"
    assert broken.json()["link"]["broken_initiator"] == "client"
    assert ClientCard.objects.get(pk=card_id).linked_user_id is None
    # § 6.6.3: the person still sees the link of the history given before.
    assert somchai.get("/api/profile-links").json()["links"][0]["state"] == "broken"
    # § 6.6.6: a new request links the same card again.
    assert ask(owner, cmp, card_id, usr).status_code == 201


def test_invitation_by_email_decline_and_resend(owner: Client, cmp: str) -> None:
    card_id = person(owner, cmp)
    sent = ask(owner, cmp, card_id, "New.Client@Example.com")
    assert sent.status_code == 201, sent.content
    link = sent.json()["link"]
    assert link["target"] == "new.client@example.com"

    # § 6.2.6: after registering with the address the request of this card is there.
    newcomer, _usr = user_client("new.client@example.com")
    mine = newcomer.get("/api/profile-links").json()["links"]
    assert [m["id"] for m in mine] == [link["id"]]
    declined = post(newcomer, f"/api/profile-links/{link['id']}/decline", {}, key())
    assert declined.json()["link"]["state"] == "declined"
    assert ClientCard.objects.get(pk=card_id).linked_user_id is None
    assert newcomer.get("/api/profile-links").json()["links"] == []
    # Decision Б-5: a new request by hand after a decline.
    assert ask(owner, cmp, card_id, "new.client@example.com").status_code == 201


def test_target_rules_cancel_and_expiry(owner: Client, cmp: str) -> None:
    card_id = person(owner, cmp)
    missing = ask(owner, cmp, card_id, "USR-ZZZZZZZZ")
    assert missing.json()["error"]["code"] == "LINK_TARGET_NOT_FOUND"
    wrong_kind = ask(owner, cmp, card_id, cmp)
    assert wrong_kind.json()["error"]["code"] == "LINK_TARGET_NOT_FOUND"
    phone = ask(owner, cmp, card_id, "+66 (81) 999-0000")
    assert phone.json()["link"]["target"] == "+66819990000"
    assert phone.json()["link"]["by_phone"] is True

    link_id = phone.json()["link"]["id"]
    cancelled = post(owner, f"/api/c/{cmp}/profile-links/{link_id}/cancel", {}, key())
    assert cancelled.json()["link"]["state"] == "cancelled"
    again = post(owner, f"/api/c/{cmp}/profile-links/{link_id}/cancel", {}, key())
    assert again.json()["error"]["code"] == "LINK_NOT_PENDING"

    somchai, usr = user_client("somchai@example.com")
    link = ask(owner, cmp, card_id, usr).json()["link"]
    ProfileLink.objects.filter(pk=link["id"]).update(expires_at=timezone.now() - timedelta(1))
    late = post(somchai, f"/api/profile-links/{link['id']}/accept", {"fields": []}, key())
    assert late.json()["error"]["code"] == "LINK_EXPIRED"
    assert links_of(owner, cmp, card_id)[0]["state"] == "expired"
    accepted_id = ask(owner, cmp, card_id, usr).json()["link"]["id"]
    assert post(somchai, f"/api/profile-links/{accepted_id}/accept", {}, key()).status_code == 200

    # One card of the list per profile in a company.
    other = person(owner, cmp, phone="+66 81 000 1111")
    assert ask(owner, cmp, other, usr).json()["error"]["code"] == "LINK_PROFILE_TAKEN"
    assert ask(owner, cmp, card_id, usr).json()["error"]["code"] == "LINK_ALREADY_EXISTS"


def test_rights_of_the_request(owner: Client, cmp: str) -> None:
    card_id = person(owner, cmp)
    reader, _ = hire(owner, cmp, "reader@example.com", grant("client.find"))
    denied = ask(reader, cmp, card_id, "someone@example.com")
    assert denied.status_code == 403
    assert (
        reader.get(f"/api/c/{cmp}/profile-links?card_type=client&card_id={card_id}").status_code
        == 200
    )


def test_company_profile_answer_and_notifications(owner: Client, cmp: str) -> None:
    body = {
        "kind": "company",
        "company_name": "Parts",
        "contacts": [{"kind": "phone", "value": "+66 81 234 5678"}],
    }
    card_id = post(owner, f"/api/c/{cmp}/counterparties", body, key()).json()["counterparty"]["id"]
    supplier = Client()
    named_user(supplier, "boss@example.com")
    other = create_company(supplier, display_name="Parts Supplier").json()["company"]["public_id"]

    by_email = ask(owner, cmp, card_id, "boss@example.com", "counterparty")
    assert by_email.json()["error"]["code"] == "LINK_COMPANY_BY_ID"
    assert ask(owner, cmp, card_id, cmp, "counterparty").json()["error"]["code"] == "LINK_SELF"
    link = ask(owner, cmp, card_id, other, "counterparty").json()["link"]

    # § 6.3.8: only who may answer sees it; the notification lasts until the answer.
    clerk, _ = hire(supplier, other, "clerk@example.com", grant("client.find"))
    assert clerk.get(f"/api/c/{other}/incoming-links").status_code == 403
    now = timezone.now()
    assert notices.requests(now) == 1
    requested = Notification.objects.get(event="link.requested")
    assert requested.state == Notification.State.ACTIVE
    assert requested.recipients.count() == 1

    incoming = supplier.get(f"/api/c/{other}/incoming-links").json()["links"]
    assert incoming[0]["available"]["display_name"] == "Parts Supplier"
    accepted = post(
        supplier,
        f"/api/c/{other}/incoming-links/{link['id']}/accept",
        {"fields": ["display_name"]},
        key(),
    )
    assert accepted.status_code == 200, accepted.content
    card = Counterparty.objects.get(pk=card_id)
    assert card.linked_company_id is not None and card.linked_user_id is None
    shown = links_of(owner, cmp, card_id, "counterparty")[0]
    assert shown["linked_profile"]["public_id"] == other
    assert shown["shared"] == {"display_name": "Parts Supplier"}

    notices.requests(timezone.now())
    requested.refresh_from_db()
    assert requested.state == Notification.State.DONE
    entry = AuditEvent.objects.get(action="profile_link.accepted")
    assert notices.on_answer(entry) == 1
    answered = Notification.objects.get(event="link.answered")
    assert answered.params["accepted"] is True and answered.object_type == "counterparty"
    # The answer is journaled in the answering company too.
    assert AuditEvent.objects.filter(action="profile_link.answered").count() == 1


def accept(client: Client, link_id: str) -> Any:
    return post(client, f"/api/profile-links/{link_id}/accept", {"fields": ["email"]}, key())


def test_accept_rechecks_that_the_card_is_active(owner: Client, cmp: str) -> None:
    card_id = person(owner, cmp)
    somchai, usr = user_client("somchai@example.com")
    link = ask(owner, cmp, card_id, usr).json()["link"]
    ClientCard.objects.filter(pk=card_id).update(state=ClientCard.State.ARCHIVED)
    refused = accept(somchai, link["id"])
    assert refused.json()["error"]["code"] == "CARD_ARCHIVED"
    assert ClientCard.objects.get(pk=card_id).linked_user_id is None
    assert links_of(owner, cmp, card_id)[0]["state"] == "pending"


def test_one_card_of_a_list_per_profile_in_the_database(
    owner: Client, cmp: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = person(owner, cmp)
    second = person(owner, cmp, phone="+66 81 234 5679")
    somchai, usr = user_client("somchai@example.com")
    one = ask(owner, cmp, first, usr).json()["link"]
    two = ask(owner, cmp, second, usr).json()["link"]
    assert accept(somchai, one["id"]).status_code == 200
    # A concurrent accept passes the read check; the constraint stops it with the same error.
    monkeypatch.setattr(links, "_taken", lambda *args: False)
    refused = accept(somchai, two["id"])
    assert refused.json()["error"]["code"] == "LINK_PROFILE_TAKEN"
    assert ClientCard.objects.filter(linked_user_id__isnull=False).count() == 1
    assert links_of(owner, cmp, second)[0]["state"] == "pending"

    linked = ClientCard.objects.get(pk=first)
    with pytest.raises(IntegrityError), transaction.atomic():
        ClientCard.objects.filter(pk=second).update(linked_user_id=linked.linked_user_id)
    # The other list of the company may be linked to the same profile.
    Counterparty.objects.create(
        company_id=linked.company_id,
        kind="person",
        first_name="Somchai",
        last_name="K",
        linked_user_id=linked.linked_user_id,
        created_by_user_id=linked.created_by_user_id,
    )


def test_an_answer_for_a_company_needs_the_right_for_the_whole_company(
    owner: Client, cmp: str
) -> None:
    supplier = Client()
    named_user(supplier, "boss@example.com")
    other = create_company(supplier, display_name="Parts Supplier").json()["company"]["public_id"]
    created = post(supplier, f"/api/c/{other}/locations", {"name": "Krabi"}, key())
    krabi = created.json()["location"]["id"]
    limited = [
        {
            "type": "grant",
            "permission": "company.link.answer",
            "locations": [krabi],
            "contexts": "all",
            "cash_accounts": "all",
        }
    ]
    local, _ = hire(supplier, other, "local@example.com", limited)
    # Notified are only those with the right for the whole company; the answer is the same.
    assert local.get(f"/api/c/{other}/incoming-links").status_code == 403
    assert local.get(f"/api/c/{other}/incoming-owner-requests").status_code == 403
    whole, _ = hire(supplier, other, "whole@example.com", grant("company.link.answer"))
    assert whole.get(f"/api/c/{other}/incoming-links").status_code == 200
    assert whole.get(f"/api/c/{other}/incoming-owner-requests").status_code == 200
