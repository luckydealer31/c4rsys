"""Verification of links and disputed records by the system staff (ТЗ § 24.1.5, § 24.1.7,
§ 8.3.12, § 8.3.16)."""

from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.accounts.models import LoginContact, UserAccount, UserSession
from c4rsys.backoffice.models import SystemJournalEntry
from c4rsys.fleet.models import VehicleOwnerRequest, VehicleOwnership, VehicleOwnershipDecision
from c4rsys.parties.models import ClientCard, ProfileLink
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.backoffice.helpers import Member, err, member, sp
from tests.companies.helpers import create_company, key
from tests.fleet.test_owner_requests import answer, ask, person, somchai_id, vehicle
from tests.parties.test_profile_links import ask as ask_link
from tests.parties.test_profile_links import person as client_card

pytestmark = pytest.mark.django_db


@pytest.fixture
def verifier() -> Member:
    return member("verify@c4rsys.test", ["verification"], unit="Verification")


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok").json()["company"]["public_id"]
    return public_id


@pytest.fixture
def dispute(owner: Client, cmp: str) -> dict[str, Any]:
    created = post(owner, f"/api/c/{cmp}/locations", {"name": "Phuket"}, key())
    location = created.json()["location"]["id"]
    claimant, usr = person("claimant@example.com")
    _, first_usr = person("first@example.com")
    card = vehicle(owner, cmp, location, relation="managed", owner_kind="profile", owner_lookup=usr)
    request = ask(owner, cmp, card["id"]).json()["request"]
    stored = VehicleOwnerRequest.objects.get(pk=request["id"])
    VehicleOwnership.objects.create(
        vehicle_id=stored.vehicle_id,
        owner_user_id=somchai_id(first_usr),
        started_at=timezone.now(),
    )
    assert answer(claimant, request["id"], ownership=True).status_code == 200
    return {"request": request["id"], "claimant": usr, "first": first_usr}


def test_the_queue_needs_the_verification_permission(admin: Member, support: Member) -> None:
    assert support.client.get("/api/staff/verification").status_code == 403
    queue = admin.client.get("/api/staff/verification").json()
    # Conflicts of identifiers come with block 12.
    assert queue["identifier_conflicts"] is None


def test_an_ownership_dispute_is_decided_by_the_verification(
    verifier: Member, dispute: dict[str, Any]
) -> None:
    queue = verifier.client.get("/api/staff/verification").json()
    shown = queue["ownership_disputes"]
    assert [d["request_id"] for d in shown] == [dispute["request"]]
    assert shown[0]["claimant"]["public_id"] == dispute["claimant"]
    assert shown[0]["current_owner"]["public_id"] == dispute["first"]

    response = sp(
        verifier,
        f"verification/ownership/{dispute['request']}",
        {"kind": "transfer", "reason": "Sale contract checked"},
    )
    assert response.status_code == 200, response.content
    # A new version of the history; the earlier period stays with its end.
    periods = list(VehicleOwnership.objects.order_by("started_at"))
    assert len(periods) == 2
    assert periods[0].owner_user_id == somchai_id(dispute["first"])
    assert periods[0].ended_at is not None
    assert periods[1].owner_user_id == somchai_id(dispute["claimant"])
    assert periods[1].ended_at is None
    decision = VehicleOwnershipDecision.objects.get()
    assert decision.reason == "Sale contract checked"
    assert SystemJournalEntry.objects.filter(action="verification.ownership_decided").exists()
    assert verifier.client.get("/api/staff/verification").json()["ownership_disputes"] == []
    again = sp(
        verifier, f"verification/ownership/{dispute['request']}", {"kind": "keep", "reason": "x"}
    )
    assert err(again) == "DISPUTE_NOT_OPEN"


def test_keeping_the_current_owner(verifier: Member, dispute: dict[str, Any]) -> None:
    response = sp(
        verifier,
        f"verification/ownership/{dispute['request']}",
        {"kind": "keep", "reason": "No evidence of the sale"},
    )
    assert response.status_code == 200, response.content
    assert VehicleOwnership.objects.get().owner_user_id == somchai_id(dispute["first"])
    assert response.json()["dispute"]["decision"]["kind"] == "keep"


def test_a_disputed_profile_link_is_ended_after_the_check(
    verifier: Member, owner: Client, cmp: str
) -> None:
    card_id = client_card(owner, cmp)
    somchai, usr = person("somchai@example.com")
    link = ask_link(owner, cmp, card_id, usr).json()["link"]
    accepted = post(
        somchai, f"/api/profile-links/{link['id']}/accept", {"fields": ["first_name"]}, key()
    )
    assert accepted.status_code == 200, accepted.content
    shown = verifier.client.get(f"/api/staff/verification/profile-links/{link['id']}").json()
    assert shown["link"]["state"] == "accepted"
    ended = sp(
        verifier,
        f"verification/profile-links/{link['id']}/end",
        {"reason": "Accepted by another person"},
    )
    assert ended.status_code == 200, ended.content
    stored = ProfileLink.objects.get(pk=link["id"])
    assert stored.state == "broken" and stored.broken_initiator == "platform"
    assert ClientCard.objects.get(pk=card_id).linked_user_id is None
    # The company sees the correction in its journal.
    event = AuditEvent.objects.get(action="profile_link.broken")
    assert event.company_id == stored.company_id and event.reason == "Accepted by another person"
    # A company cannot pretend to be the platform.
    refused = post(
        owner, f"/api/c/{cmp}/profile-links/{link['id']}/break", {"initiator": "platform"}, key()
    )
    assert refused.status_code in (400, 422)


def test_unlinking_a_disputed_contact(verifier: Member, owner: Client) -> None:
    usr = owner.get("/api/me").json()["user"]["public_id"]
    user = UserAccount.objects.get(public_id=usr)
    phone = LoginContact.objects.create(user=user, kind="phone", value="+66812345678")
    contacts = verifier.client.get(f"/api/staff/verification/users/{usr}/contacts").json()
    assert {c["value"] for c in contacts["contacts"]} == {"o***@example.com", "+***78"}
    email = LoginContact.objects.get(user=user, kind="email")
    last = sp(
        verifier, f"verification/users/{usr}/contacts/{email.id}/unlink", {"reason": "Dispute"}
    )
    # § 2.1.2: the profile keeps a confirmed email.
    assert err(last) == "CONTACT_LAST_EMAIL"
    done = sp(
        verifier,
        f"verification/users/{usr}/contacts/{phone.id}/unlink",
        {"reason": "The number was given to another person"},
    )
    assert done.status_code == 200, done.content
    assert not LoginContact.objects.filter(pk=phone.id).exists()
    assert not UserSession.objects.filter(user=user, ended_at__isnull=True).exists()
    entry = SystemJournalEntry.objects.get(action="verification.contact_unlinked")
    assert entry.before == {"contact_kind": "phone", "contact": "+66812345678"}
