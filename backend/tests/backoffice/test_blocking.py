"""System blocks and restoration (ТЗ § 24.2.1–24.2.4)."""

from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone

from c4rsys.accounts.models import UserSession
from c4rsys.backoffice import blocking
from c4rsys.backoffice.models import SystemBlock, SystemJournalEntry
from c4rsys.companies.models import Company
from c4rsys.finance import deposits
from c4rsys.notifications.models import Notification
from c4rsys.rentals import bookings
from c4rsys.rentals.models import Rental
from c4rsys.tasks import series
from c4rsys.tasks.models import Task, TaskSeries
from tests.accounts.helpers import login, post
from tests.backoffice.helpers import Member, custom_role, err, grant, sp, staff_client
from tests.companies.helpers import hire, key
from tests.companies.test_ownership import company
from tests.documents.test_signing import _by_link, _invite, _send, doc, links  # noqa: F401
from tests.rentals.scenario import Scenario

pytestmark = pytest.mark.django_db


def _block(m: Member, target_type: str, target: str, **extra: Any) -> Any:
    body = {
        "target_type": target_type,
        "target": target,
        "category": "security_threat",
        "comment": "Credential stuffing from many addresses",
        **extra,
    }
    return sp(m, "blocks", body)


def _usr(client: Client) -> str:
    public_id: str = client.get("/api/me").json()["user"]["public_id"]
    return public_id


@pytest.fixture
def email_mode(settings: Any) -> None:
    settings.C4R_MAIL_MODE = "email"
    settings.C4R_SUPPORT_CONTACT = "support@c4rsys.test"


def test_a_user_block_ends_sessions_and_refuses_login(
    admin: Member,
    owner: Client,
    s: Scenario,
    email_mode: None,
    django_capture_on_commit_callbacks: Any,
) -> None:
    colleague, _ = hire(
        owner, s.cmp, "colleague@example.com", [{"type": "grant", "permission": "vehicle.view"}]
    )
    second = Client()
    assert login(second, "owner@example.com").status_code == 200
    usr = _usr(owner)
    assert err(_block(admin, "user", usr, category="curiosity")) == "VALIDATION_ERROR"
    assert err(_block(admin, "user", usr, comment="")) == "FIELD_REQUIRED"
    with django_capture_on_commit_callbacks(execute=True):
        blocked = _block(admin, "user", usr, disclosed_reason="Suspicious logins")
    assert blocked.status_code == 201, blocked.content
    # All sessions end at once (§ 24.2.2).
    assert owner.get("/api/me").status_code == 401
    assert second.get("/api/me").status_code == 401
    assert UserSession.objects.filter(end_reason="blocked").count() == 2
    refused = login(Client(), "owner@example.com")
    assert refused.status_code == 403 and err(refused) == "USER_BLOCKED"
    params = refused.json()["error"]["params"]
    assert params == {
        "support": "support@c4rsys.test",
        "category": "security_threat",
        "reason": "Suspicious logins",
    }
    # The company and its other employees go on.
    assert colleague.get(f"/api/c/{s.cmp}").status_code == 200
    # The user is told with the reason and the support contact.
    assert len(mail.outbox) == 1
    assert "Suspicious logins" in str(mail.outbox[0].body)
    assert "support@c4rsys.test" in str(mail.outbox[0].body)
    assert err(_block(admin, "user", usr)) == "BLOCK_EXISTS"

    block_id = blocked.json()["block"]["id"]
    assert err(sp(admin, f"blocks/{block_id}/restore", {})) == "FIELD_REQUIRED"
    restored = sp(admin, f"blocks/{block_id}/restore", {"reason": "Owner verified by phone"})
    assert restored.status_code == 200, restored.content
    assert login(Client(), "owner@example.com").status_code == 200
    actions = list(
        SystemJournalEntry.objects.filter(object_id=block_id).values_list("action", flat=True)
    )
    assert {"block.created", "block.notice_sent", "block.restored"} <= set(actions)


def test_blocks_need_the_permission(support: Member, owner: Client) -> None:
    assert err(_block(support, "user", _usr(owner))) == "PERMISSION_DENIED"


def test_a_company_block_stops_access_commands_and_automatic_actions(
    admin: Member,
    owner: Client,
    s: Scenario,
    email_mode: None,
    django_capture_on_commit_callbacks: Any,
) -> None:
    other = company(owner, "Other Rent")
    today = timezone.localdate()
    repeated = post(
        owner,
        f"/api/c/{s.cmp}/tasks",
        {
            "title": "Wash",
            "due_date": today.isoformat(),
            "due_time": "23:59",
            "repeat": {"frequency": "daily"},
        },
        key(),
    )
    assert repeated.status_code == 201, repeated.content
    series_id = repeated.json()["task"]["series"]["id"]
    booking = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": s.vehicle,
            "planned_start": (timezone.now() + timedelta(hours=1)).isoformat(),
            "planned_end": (timezone.now() + timedelta(days=2)).isoformat(),
        },
        key(),
    )
    assert booking.status_code == 201, booking.content
    rental_id = booking.json()["rental"]["id"]
    Rental.objects.filter(pk=rental_id).update(auto_cancel_at=timezone.now() + timedelta(hours=2))

    with django_capture_on_commit_callbacks(execute=True):
        blocked = _block(admin, "company", s.cmp, disclosed_reason="Fraud reports")
    assert blocked.status_code == 201, blocked.content
    # The owners are told (§ 24.2.2).
    assert [m.to for m in mail.outbox] == [["owner@example.com"]]
    assert "Test Rent" in str(mail.outbox[0].body)
    # The access of the employees stops; the account and the other company stay.
    refused = owner.get(f"/api/c/{s.cmp}")
    assert refused.status_code == 403 and err(refused) == "COMPANY_BLOCKED"
    assert refused.json()["error"]["params"] == {"support": "support@c4rsys.test"}
    assert owner.get(f"/api/c/{other}").status_code == 200
    assert owner.get("/api/me").status_code == 200
    # No new operations.
    created = post(owner, f"/api/c/{s.cmp}/locations", {"name": "New"}, key())
    assert err(created) == "COMPANY_BLOCKED"
    # Automatic actions wait: repeating tasks and auto-cancel.
    later = timezone.now() + timedelta(days=3)
    assert series.run(later) == 0
    assert bookings.cancel_no_shows(later) == 0
    assert Rental.objects.get(pk=rental_id).state == "booked"
    assert deposits.release_expired(later) == 0

    # The block ends after the auto-cancel moment has passed (§ 24.2.3).
    Rental.objects.filter(pk=rental_id).update(auto_cancel_at=timezone.now() - timedelta(minutes=5))
    TaskSeries.objects.filter(pk=series_id).update(next_on=today - timedelta(days=3))
    block_id = blocked.json()["block"]["id"]
    assert sp(admin, f"blocks/{block_id}/restore", {"reason": "Cleared"}).status_code == 200
    assert owner.get(f"/api/c/{s.cmp}").status_code == 200
    rental = Rental.objects.get(pk=rental_id)
    # Not cancelled retroactively: a conflict and a task for an employee instead.
    assert rental.state == "booked" and rental.auto_cancel_at is None
    assert Notification.objects.filter(event="rental.auto_cancel_missed").count() == 1
    assert Task.objects.filter(system_event="rental.auto_cancel_missed").count() == 1
    assert bookings.cancel_no_shows(later) == 0
    # The missed repetitions are not made; the next one is the nearest ahead.
    resumed = TaskSeries.objects.get(pk=series_id)
    assert resumed.next_on is not None and resumed.next_on >= today
    assert Task.objects.filter(series_id=series_id, series_on__lt=today).count() == 0


def test_external_signing_links_are_suspended(
    admin: Member,
    owner: Client,
    s: Scenario,
    doc: dict[str, Any],  # noqa: F811
    links: list[str],  # noqa: F811
    django_capture_on_commit_callbacks: Any,
) -> None:
    _send(owner, s, doc, ("client", s.client))
    token = _invite(owner, s, doc, links, django_capture_on_commit_callbacks)
    assert _by_link(token).status_code == 200
    block_id = _block(admin, "company", s.cmp).json()["block"]["id"]
    assert err(_by_link(token)) == "SIGNING_LINK_SUSPENDED"
    sp(admin, f"blocks/{block_id}/restore", {"reason": "ok"})
    assert _by_link(token).status_code == 200


def test_a_block_applies_to_a_deactivated_company_and_does_not_activate_it(
    admin: Member, owner: Client, s: Scenario
) -> None:
    Company.objects.filter(public_id=s.cmp).update(deactivated_at=timezone.now())
    blocked = _block(admin, "company", s.cmp)
    assert blocked.status_code == 201, blocked.content
    assert err(owner.get(f"/api/c/{s.cmp}")) == "COMPANY_BLOCKED"
    sp(admin, f"blocks/{blocked.json()['block']['id']}/restore", {"reason": "ok"})
    # § 24.2.4: the deactivation stays.
    assert Company.objects.get(public_id=s.cmp).deactivated_at is not None
    assert owner.get(f"/api/c/{s.cmp}").json()["company"]["deactivated_at"] is not None


def test_a_block_ends_by_its_term(admin: Member, owner: Client, s: Scenario) -> None:
    ends = timezone.now() + timedelta(hours=1)
    blocked = _block(admin, "company", s.cmp, ends_at=ends.isoformat())
    assert blocked.status_code == 201, blocked.content
    past = _block(
        admin, "user", _usr(owner), ends_at=(timezone.now() - timedelta(hours=1)).isoformat()
    )
    assert err(past) == "BLOCK_END_IN_PAST"
    assert blocking.run(timezone.now()) == 0
    assert blocking.run(ends + timedelta(minutes=1)) == 1
    found = SystemBlock.objects.get(pk=blocked.json()["block"]["id"])
    assert found.end_kind == "expired" and found.ended_at is not None
    entry = SystemJournalEntry.objects.get(action="block.expired")
    assert entry.system_process == "system_blocks"
    assert owner.get(f"/api/c/{s.cmp}").status_code == 200


def test_a_deferred_notice_needs_its_permission(
    admin: Member,
    owner: Client,
    s: Scenario,
    email_mode: None,
    django_capture_on_commit_callbacks: Any,
) -> None:
    defer = {"defer_reason": "The attacker must not learn of it yet"}
    assert err(_block(admin, "company", s.cmp, **defer)) == "PERMISSION_DENIED"
    custom_role("security_lead", ["block.notice_defer"])
    grant(admin, "security_lead")
    with django_capture_on_commit_callbacks(execute=True):
        blocked = _block(admin, "company", s.cmp, **defer)
    assert blocked.status_code == 201, blocked.content
    assert blocked.json()["block"]["notice"] == "deferred" and mail.outbox == []
    entry = SystemJournalEntry.objects.get(action="block.notice_deferred")
    assert entry.reason == defer["defer_reason"]
    with django_capture_on_commit_callbacks(execute=True):
        released = sp(admin, f"blocks/{blocked.json()['block']['id']}/notice/release")
    assert released.json()["block"]["notice"] == "sent"
    assert len(mail.outbox) == 1


def test_a_staff_member_cannot_block_himself(admin: Member) -> None:
    assert err(_block(admin, "user", admin.public_id)) == "BLOCK_SELF"
    assert staff_client().get("/api/staff/blocks").status_code == 401
