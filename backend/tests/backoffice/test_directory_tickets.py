"""Search with masked contacts, the system journal and support tickets (ТЗ § 24.1.2–24.1.3,
§ 24.1.7, § 24.4.3, § 24.4.5)."""

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from c4rsys.backoffice.models import SystemJournalEntry
from c4rsys.platform.models import AuditEvent
from tests.backoffice.helpers import Member, custom_role, err, member, sp
from tests.rentals.scenario import Scenario

pytestmark = pytest.mark.django_db


def test_search_masks_contacts_and_a_reveal_needs_a_reason(
    support: Member, owner: Client, s: Scenario
) -> None:
    found = support.client.get("/api/staff/users?q=owner@example.com").json()["users"]
    assert len(found) == 1
    assert found[0]["email_masked"] == "o***@example.com"
    usr = found[0]["public_id"]
    card = support.client.get(f"/api/staff/users/{usr}").json()["user"]
    assert card["contacts_masked"]["login_emails"] == ["o***@example.com"]
    assert "owner@example.com" not in str(card)
    assert card["companies"][0]["public_id"] == s.cmp
    # The full contacts only with a reason; the reveal is journaled with it.
    assert err(sp(support, f"users/{usr}/contacts", {})) == "FIELD_REQUIRED"
    revealed = sp(support, f"users/{usr}/contacts", {"reason": "Ticket 7: verify the owner"})
    assert revealed.status_code == 200, revealed.content
    assert revealed.json()["contacts"]["login_emails"] == ["owner@example.com"]
    entry = SystemJournalEntry.objects.get(action="directory.user_contacts_revealed")
    assert entry.reason == "Ticket 7: verify the owner" and entry.roles == ["support"]

    companies = support.client.get("/api/staff/companies?q=Test Rent").json()["companies"]
    assert [c["public_id"] for c in companies] == [s.cmp]
    assert support.client.get(f"/api/staff/companies/{s.cmp}").json()["company"]["employees"] == 1


def test_a_reveal_needs_its_own_permission() -> None:
    custom_role("finder", ["directory.search"])
    finder = member("finder@c4rsys.test", ["finder"])
    assert finder.client.get("/api/staff/users?q=finder@c4rsys.test").status_code == 200
    usr = finder.public_id
    assert err(sp(finder, f"users/{usr}/contacts", {"reason": "x"})) == "PERMISSION_DENIED"


def test_the_system_journal_is_separate_and_cannot_be_changed(
    admin: Member, owner: Client, s: Scenario
) -> None:
    before = AuditEvent.objects.filter(company_id__isnull=False).count()
    admin.client.get("/api/staff/companies?q=Test")
    admin.client.get(f"/api/staff/companies/{s.cmp}")
    # Staff actions go to the system journal, not to the company's.
    assert AuditEvent.objects.filter(company_id__isnull=False).count() == before
    assert SystemJournalEntry.objects.filter(action="directory.company_viewed").exists()
    entry = SystemJournalEntry.objects.first()
    assert entry is not None
    with pytest.raises(DatabaseError), transaction.atomic():
        SystemJournalEntry.objects.filter(pk=entry.pk).update(action="edited")
    with pytest.raises(DatabaseError), transaction.atomic():
        SystemJournalEntry.objects.filter(pk=entry.pk).delete()
    journal = admin.client.get("/api/staff/journal?action=directory.").json()["entries"]
    assert {e["action"] for e in journal} >= {"directory.company_viewed"}
    exported = admin.client.get("/api/staff/journal/export")
    assert exported.status_code == 200 and exported["Content-Type"].startswith("text/csv")
    assert b"directory.company_viewed" in exported.content
    assert SystemJournalEntry.objects.filter(action="journal.exported").exists()


def test_ticket_lifecycle(support: Member, admin: Member, owner: Client, s: Scenario) -> None:
    body = {
        "received_on": "2026-10-30",
        "applicant_name": "Ivan",
        "applicant_contact": "ivan@example.com",
        "subject": "Booking cancelled",
        "description": "The client says the booking was cancelled without a reason.",
    }
    created = sp(support, "tickets", body)
    assert created.status_code == 201, created.content
    t = created.json()["ticket"]
    assert (t["number"], t["state"], t["channel"]) == (1, "new", "email")
    assert sp(support, "tickets", {**body, "subject": "Second"}).json()["ticket"]["number"] == 2
    tid = t["id"]
    # Links to a company, a user and a disputed record.
    usr = owner.get("/api/me").json()["user"]["public_id"]
    assert sp(
        support, f"tickets/{tid}/links", {"kind": "company", "target": s.cmp}
    ).status_code == (200)
    assert sp(support, f"tickets/{tid}/links", {"kind": "user", "target": usr}).status_code == 200
    record = {"kind": "record", "target": "VIN WVWZZZ1JZXW000001", "record_type": "vin"}
    assert sp(support, f"tickets/{tid}/links", record).status_code == 200
    assigned = sp(support, f"tickets/{tid}/assign", {"member_id": admin.member_id})
    assert assigned.json()["ticket"]["assignee"]["id"] == admin.member_id
    sp(support, f"tickets/{tid}/comments", {"text": "Asked the company for the log."})
    sp(support, f"tickets/{tid}/comments", {"kind": "confirmation", "text": "Owner confirmed."})
    # States: new → in progress → waiting → closed with a result.
    current = support.client.get(f"/api/staff/tickets/{tid}").json()["ticket"]
    moved = sp(
        support, f"tickets/{tid}/state", {"state": "in_progress", "version": current["version"]}
    )
    assert moved.status_code == 200, moved.content
    stale = sp(support, f"tickets/{tid}/state", {"state": "waiting", "version": current["version"]})
    assert err(stale) == "VERSION_CONFLICT"
    assert sp(support, f"tickets/{tid}/state", {"state": "waiting"}).status_code == 200
    assert err(sp(support, f"tickets/{tid}/state", {"state": "new"})) == "TICKET_STATE_INVALID"
    assert err(sp(support, f"tickets/{tid}/state", {"state": "closed"})) == "TICKET_RESULT_REQUIRED"
    closed = sp(support, f"tickets/{tid}/state", {"state": "closed", "result": "Explained."})
    assert closed.status_code == 200
    detail = closed.json()["ticket"]
    assert detail["state"] == "closed" and detail["result"] == "Explained."
    assert [c["kind"] for c in detail["comments"]] == ["comment", "confirmation"]
    assert {x["kind"] for x in detail["links"]} == {"company", "user", "record"}
    assert support.client.get("/api/staff/tickets?state=closed").json()["tickets"][0]["id"] == tid
    actions = set(SystemJournalEntry.objects.filter(ticket_id=tid).values_list("action", flat=True))
    assert {"ticket.created", "ticket.linked", "ticket.assigned", "ticket.state_changed"} <= actions
    assert {"ticket.comment_added", "ticket.confirmation_added"} <= actions
