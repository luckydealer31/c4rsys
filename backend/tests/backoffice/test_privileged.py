"""Privileged sessions (ТЗ § 24.3.1–24.3.2, § 24.1.4; decision В-11.3)."""

from datetime import datetime, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.backoffice import privileged
from c4rsys.backoffice.models import PrivilegedAccess, PrivilegedSession, SystemJournalEntry
from c4rsys.notifications.models import Notification
from c4rsys.platform.models import AuditEvent
from tests.backoffice.helpers import Member, custom_role, err, grant, member, open_session, sp
from tests.companies.helpers import hire
from tests.documents.test_registry import PDF, _create, _ok
from tests.rentals.scenario import Scenario, issue

pytestmark = pytest.mark.django_db


def test_opening_needs_a_reason_and_permitted_scopes(support: Member, s: Scenario) -> None:
    assert err(open_session(support, s.cmp, reason="")) == "FIELD_REQUIRED"
    assert err(open_session(support, s.cmp, reason_category="curiosity")) == "VALIDATION_ERROR"
    assert err(open_session(support, s.cmp, ticket_number=99)) == "TICKET_UNKNOWN"
    # Finances, documents and files are separate system permissions (§ 24.3.1).
    for scope in ("finance", "documents"):
        assert err(open_session(support, s.cmp, [scope])) == "PERMISSION_DENIED"
    opened = open_session(support, s.cmp)
    assert opened.status_code == 201, opened.content
    session = opened.json()["session"]
    assert session["scopes"] == ["view"] and session["active"]
    started = datetime.fromisoformat(session["started_at"])
    expires = datetime.fromisoformat(session["expires_at"])
    assert expires - started == timedelta(hours=1)  # В-11.3
    # One open session at a time; the staff interface knows it is open.
    assert err(open_session(support, s.cmp)) == "PRIVILEGED_SESSION_OPEN"
    assert support.client.get("/api/staff/me").json()["privileged_session"]["id"] == session["id"]
    entry = SystemJournalEntry.objects.get(action="privileged.opened")
    assert entry.reason and entry.roles == ["support"] and entry.target_company_id is not None


def test_a_ticket_number_is_kept(support: Member, s: Scenario) -> None:
    ticket = sp(support, "tickets", {"subject": "Help", "description": "..."}).json()["ticket"]
    opened = open_session(support, s.cmp, ticket_number=ticket["number"])
    assert opened.json()["session"]["ticket_number"] == ticket["number"]
    assert SystemJournalEntry.objects.get(action="privileged.opened").ticket_id is not None


def test_the_session_only_shows_and_records_every_section(
    support: Member, owner: Client, s: Scenario
) -> None:
    rented = issue(owner, s, actual_start=(timezone.now() - timedelta(hours=1)).isoformat())
    assert rented.status_code == 201, rented.content
    rental_id = rented.json()["rental"]["id"]
    sid = open_session(support, s.cmp).json()["session"]["id"]
    base = f"/api/staff/privileged/{sid}"
    assert support.client.get(f"{base}/company").json()["company"]["public_id"] == s.cmp
    assert len(support.client.get(f"{base}/employees").json()["employees"]) == 1
    assert support.client.get(f"{base}/vehicles").json()["vehicles"][0]["id"] == s.vehicle
    assert support.client.get(f"{base}/clients").json()["clients"][0]["id"] == s.client
    orders = support.client.get(f"{base}/orders").json()["orders"]
    assert len(orders) == 1 and "amounts" not in orders[0]  # no money without «finance»
    rental = support.client.get(f"{base}/rentals/{rental_id}").json()["rental"]
    assert rental["state"] == "active" and "calculation" not in rental
    # Documents and finances are outside the default view.
    assert err(support.client.get(f"{base}/documents")) == "PRIVILEGED_SCOPE_DENIED"
    assert err(support.client.get(f"{base}/finance")) == "PRIVILEGED_SCOPE_DENIED"
    # Nothing can be changed through it (§ 24.1.4): the sections only read, and the API of the
    # company does not answer on the staff host.
    assert support.client.post(f"{base}/orders", {}, "application/json").status_code == 405
    assert support.client.get(f"/api/c/{s.cmp}/orders").status_code == 404
    cancel = support.client.post(
        f"/api/c/{s.cmp}/rentals/{rental_id}/return", {}, "application/json"
    )
    assert cancel.status_code == 404
    sections = set(PrivilegedAccess.objects.values_list("section", flat=True))
    assert sections == {"company_profile", "employees", "vehicles", "clients", "orders", "rentals"}
    viewed = SystemJournalEntry.objects.filter(action="privileged.viewed")
    assert viewed.count() == 6 and all(e.privileged_session_id for e in viewed)
    assert PrivilegedAccess.objects.get(section="rentals").object_id == rental_id


def test_finance_and_documents_with_their_permissions(admin: Member, s: Scenario) -> None:
    sid = open_session(admin, s.cmp, ["finance", "documents"]).json()["session"]["id"]
    base = f"/api/staff/privileged/{sid}"
    assert admin.client.get(f"{base}/finance").status_code == 200
    assert admin.client.get(f"{base}/documents").status_code == 200
    orders = admin.client.get(f"{base}/orders").json()["orders"]
    assert all("amounts" in o for o in orders)
    # Files need their own area too.
    assert err(admin.client.get(f"{base}/documents/{s.client}/file")) == "PRIVILEGED_SCOPE_DENIED"


def test_the_hour_ends_the_session(support: Member, s: Scenario) -> None:
    sid = open_session(support, s.cmp).json()["session"]["id"]
    PrivilegedSession.objects.filter(pk=sid).update(
        started_at=timezone.now() - timedelta(hours=2),
        expires_at=timezone.now() - timedelta(hours=1),
    )
    base = f"/api/staff/privileged/{sid}"
    assert err(support.client.get(f"{base}/company")) == "PRIVILEGED_SESSION_EXPIRED"
    assert privileged.run(timezone.now()) >= 1
    ended = PrivilegedSession.objects.get(pk=sid)
    assert ended.end_reason == "expired" and ended.ended_at == ended.expires_at
    assert err(support.client.get(f"{base}/company")) == "PRIVILEGED_SESSION_ENDED"
    # A new session needs a new reason.
    assert open_session(support, s.cmp).status_code == 201


def test_the_company_is_told_after_the_end(support: Member, owner: Client, s: Scenario) -> None:
    security, security_employment = hire(
        owner, s.cmp, "sec@example.com", [{"type": "grant", "permission": "company.security"}]
    )
    plain, plain_employment = hire(
        owner, s.cmp, "plain@example.com", [{"type": "grant", "permission": "vehicle.view"}]
    )
    sid = open_session(support, s.cmp).json()["session"]["id"]
    support.client.get(f"/api/staff/privileged/{sid}/vehicles")
    assert not Notification.objects.filter(event="security.privileged_session").exists()
    closed = sp(support, f"privileged/{sid}/close")
    assert closed.status_code == 200, closed.content
    assert closed.json()["session"]["notice"] == "sent"
    notice = Notification.objects.get(event="security.privileged_session")
    told = {str(x) for x in notice.recipients.values_list("employment_id", flat=True)}
    assert security_employment in told and plain_employment not in told
    assert len(told) == 2  # and the top-authority director
    # The company journal: unit, reason category and sections, no person of the staff.
    entry = AuditEvent.objects.get(action="security.privileged_session")
    assert entry.actor_user_id is None and entry.after is not None
    assert entry.after["unit"] == "Support" and entry.after["sections"] == ["vehicles"]
    assert entry.after["reason_category"] == "support_ticket"
    assert "booking" not in str(entry.after)  # the reason text stays internal
    shown = security.get(f"/api/c/{s.cmp}/staff-access").json()["entries"]
    assert len(shown) == 1 and shown[0]["sections"] == ["vehicles"]
    assert "support@c4rsys.test" not in str(shown)
    assert plain.get(f"/api/c/{s.cmp}/staff-access").status_code == 403
    assert owner.get(f"/api/c/{s.cmp}/staff-access").status_code == 200


def test_the_notice_is_deferred_only_by_its_permission(support: Member, s: Scenario) -> None:
    sid = open_session(support, s.cmp).json()["session"]["id"]
    reason = {"reason": "Security investigation in progress"}
    assert err(sp(support, f"privileged/{sid}/notice/defer", reason)) == "PERMISSION_DENIED"
    custom_role("investigator", ["privileged.notice_defer"])
    grant(support, "investigator")
    deferred = sp(support, f"privileged/{sid}/notice/defer", reason)
    assert deferred.status_code == 200, deferred.content
    sp(support, f"privileged/{sid}/close")
    privileged.run(timezone.now())
    assert not Notification.objects.filter(event="security.privileged_session").exists()
    entry = SystemJournalEntry.objects.get(action="privileged.notice_deferred")
    assert entry.reason == reason["reason"] and entry.actor_user_id is not None
    released = sp(support, f"privileged/{sid}/notice/release")
    assert released.json()["session"]["notice"] == "sent"
    assert Notification.objects.filter(event="security.privileged_session").count() == 1


def test_a_session_for_a_user(support: Member, owner: Client) -> None:
    usr = owner.get("/api/me").json()["user"]["public_id"]
    body = {
        "target_type": "user",
        "target": usr,
        "reason_category": "verification",
        "reason": "Check of a disputed contact",
    }
    sid = sp(support, "privileged", body).json()["session"]["id"]
    shown = support.client.get(f"/api/staff/privileged/{sid}/user").json()["user"]
    assert shown["contacts"]["login_emails"] == ["owner@example.com"]
    assert support.client.get(f"/api/staff/privileged/{sid}/company").status_code == 404
    assert PrivilegedAccess.objects.get().section == "user_profile"


def test_another_members_session_is_not_reachable(support: Member, s: Scenario) -> None:
    sid = open_session(support, s.cmp).json()["session"]["id"]
    other = member("other@c4rsys.test", ["support"])
    assert other.client.get(f"/api/staff/privileged/{sid}/company").status_code == 404


def test_a_download_needs_the_files_area_and_is_recorded(
    admin: Member, owner: Client, s: Scenario
) -> None:
    d = _ok(_create(owner, s), 201)
    sid = open_session(admin, s.cmp, ["documents", "files"]).json()["session"]["id"]
    listed = admin.client.get(f"/api/staff/privileged/{sid}/documents").json()["documents"]
    assert [x["id"] for x in listed] == [d["id"]]
    got = admin.client.get(f"/api/staff/privileged/{sid}/documents/{d['id']}/file")
    assert got.status_code == 200 and got.content == PDF
    assert PrivilegedAccess.objects.filter(kind="download", object_id=d["id"]).count() == 1
    assert SystemJournalEntry.objects.filter(action="privileged.downloaded").count() == 1
    sp(admin, f"privileged/{sid}/close")
    entry = AuditEvent.objects.get(action="security.privileged_session")
    assert entry.after is not None and entry.after["downloads"] == 1
