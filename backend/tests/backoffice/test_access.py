"""The internal interface: its own subdomain, its own sign-in, system sets and permissions
(ТЗ § 24.1.1, § 24.1.5–24.1.6; decision В-11.1)."""

import pytest
from django.core.management import call_command
from django.test import Client

from c4rsys.accounts.models import UserAccount, UserSession
from c4rsys.backoffice.models import SystemJournalEntry, SystemStaffMember
from tests.accounts.helpers import login
from tests.backoffice.helpers import (
    STAFF,
    Member,
    custom_role,
    err,
    grant,
    member,
    sp,
    staff_client,
)
from tests.companies.helpers import named_user

pytestmark = pytest.mark.django_db


def test_internal_api_answers_only_on_the_staff_host(admin: Member, owner: Client) -> None:
    # On the ordinary domain and on the cabinet the internal API does not exist.
    assert owner.get("/api/staff/me").status_code == 404
    assert owner.get("/api/staff/users?q=x").status_code == 404
    assert owner.get("/api/staff/me", HTTP_HOST="cabinet.localhost").status_code == 404
    # On the staff host only its own API, sign-in, the site and the health answer.
    anyone = staff_client()
    assert anyone.get("/api/site").json() == {"cabinet": False, "staff": True}
    assert anyone.get("/api/health").status_code == 200
    assert anyone.get("/api/auth/csrf").status_code == 200
    assert err(admin.client.get("/api/companies")) == "STAFF_HOST"
    assert err(admin.client.get("/api/me")) == "STAFF_HOST"
    assert admin.client.get("/api/c/CMP-00000000").status_code == 404
    assert admin.client.get("/api/cabinet/companies").status_code == 404
    # Registration is not offered in the internal interface.
    reg = anyone.post("/api/auth/register/start", {"email": "x@y.z"}, "application/json")
    assert err(reg) == "STAFF_HOST"
    assert admin.client.get("/api/staff/me").status_code == 200


def test_login_on_the_staff_host_needs_a_membership() -> None:
    # § 24.1.1: a personal account alone gives no access.
    personal = Client()
    named_user(personal, "nobody@example.com")
    stranger = staff_client()
    refused = login(stranger, "nobody@example.com")
    assert refused.status_code == 403 and err(refused) == "STAFF_ACCESS_DENIED"
    assert not UserSession.objects.filter(
        user__contacts__value="nobody@example.com", site="staff"
    ).exists()
    # The same account signs in on the ordinary domain as before.
    assert login(Client(), "nobody@example.com").status_code == 200

    m = member("agent@c4rsys.test", ["support"])
    me = m.client.get("/api/staff/me").json()
    assert me["roles"] == ["support"]
    assert "tickets.manage" in me["permissions"] and "staff.manage" not in me["permissions"]
    assert SystemJournalEntry.objects.filter(action="staff.signed_in").count() == 1


def test_sessions_of_the_two_interfaces_are_separate(admin: Member) -> None:
    # A session of the ordinary domain is not one of the internal interface, and back.
    personal = Client()
    assert login(personal, "admin@c4rsys.test").status_code == 200
    cookie = personal.cookies["c4r_session"].value
    borrowed = staff_client()
    borrowed.cookies["c4r_session"] = cookie
    assert err(borrowed.get("/api/staff/me")) == "SESSION_REQUIRED"
    staff_cookie = admin.client.cookies["c4r_session"].value
    other = Client()
    other.cookies["c4r_session"] = staff_cookie
    assert err(other.get("/api/me")) == "SESSION_REQUIRED"


def test_a_revoked_member_loses_access_at_once(admin: Member, support: Member) -> None:
    assert support.client.get("/api/staff/me").status_code == 200
    revoked = sp(admin, f"members/{support.member_id}/revoke", {"reason": "left", "version": 1})
    assert revoked.status_code == 200, revoked.content
    assert err(support.client.get("/api/staff/me")) == "STAFF_ACCESS_DENIED"
    assert login(staff_client(), "support@c4rsys.test").status_code == 403


def test_starter_sets_and_their_limits(admin: Member, support: Member) -> None:
    verification = member("verify@c4rsys.test", ["verification"])
    for m in (admin, support, verification):
        assert m.client.get("/api/staff/users?q=Anna").status_code == 200
    # Support does not manage system roles, blocks or read the journal (§ 24.1.5).
    assert err(support.client.get("/api/staff/roles")) == "PERMISSION_DENIED"
    assert err(sp(support, "members", {"user": admin.public_id, "roles": []})) == (
        "PERMISSION_DENIED"
    )
    assert err(support.client.get("/api/staff/blocks")) == "PERMISSION_DENIED"
    assert err(support.client.get("/api/staff/journal")) == "PERMISSION_DENIED"
    assert admin.client.get("/api/staff/journal").status_code == 200
    assert admin.client.get("/api/staff/blocks").status_code == 200


def test_no_set_no_data_and_a_custom_set_gives_only_its_permissions() -> None:
    # § 24.1.6: a programmer gets nothing by his position.
    programmer = member("dev@c4rsys.test", [])
    assert programmer.client.get("/api/staff/me").json()["permissions"] == []
    assert err(programmer.client.get("/api/staff/users?q=a")) == "PERMISSION_DENIED"
    assert err(programmer.client.get("/api/staff/tickets")) == "PERMISSION_DENIED"
    custom_role("journal_reader", ["journal.view"])
    grant(programmer, "journal_reader")
    assert programmer.client.get("/api/staff/journal").status_code == 200
    assert err(programmer.client.get("/api/staff/journal/export")) == "PERMISSION_DENIED"
    assert err(programmer.client.get("/api/staff/users?q=a")) == "PERMISSION_DENIED"


def test_admin_manages_members_and_sets(admin: Member) -> None:
    personal = Client()
    named_user(personal, "newbie@example.com")
    newbie = UserAccount.objects.get(contacts__value="newbie@example.com")
    added = sp(
        admin, "members", {"user": newbie.public_id, "unit": "Support", "roles": ["support"]}
    )
    assert added.status_code == 201, added.content
    body = added.json()["member"]
    assert body["roles"] == ["support"] and body["unit"] == "Support"
    # Several sets for one member (§ 24.1.6).
    changed = sp(
        admin,
        f"members/{body['id']}",
        {"roles": ["support", "verification"], "version": body["version"]},
    )
    assert changed.status_code == 200, changed.content
    assert changed.json()["member"]["roles"] == ["support", "verification"]
    assert login(staff_client(), "newbie@example.com").status_code == 200
    # A custom set with concrete permissions; unknown codes are refused.
    role = sp(admin, "roles", {"code": "tech_audit", "permissions": ["journal.view"]})
    assert role.status_code == 201, role.content
    assert err(sp(admin, "roles", {"code": "bad_one", "permissions": ["rental.book"]})) == (
        "SYSTEM_PERMISSION_UNKNOWN"
    )
    # An administrator does not take the management away from himself.
    own = sp(admin, f"members/{admin.member_id}", {"roles": ["support"]})
    assert err(own) == "STAFF_OWN_ADMIN_REMOVAL"
    actions = set(SystemJournalEntry.objects.values_list("action", flat=True))
    assert {"staff.member_added", "staff.member_changed", "staff.role_created"} <= actions


def test_bootstrap_command_grants_the_first_admin(capsys: pytest.CaptureFixture[str]) -> None:
    personal = Client()
    named_user(personal, "founder@example.com")
    founder = UserAccount.objects.get(contacts__value="founder@example.com")
    call_command("grant_platform_admin", founder.public_id.lower(), "--unit", "Platform")
    assert "Platform administrator" in capsys.readouterr().out
    member_row = SystemStaffMember.objects.get(user=founder)
    assert member_row.unit == "Platform"
    entry = SystemJournalEntry.objects.get(action="staff.platform_admin_granted")
    assert entry.system_process == "bootstrap" and entry.target_user_id == founder.id
    signed = staff_client()
    assert login(signed, "founder@example.com").status_code == 200
    assert "staff.manage" in signed.get("/api/staff/me").json()["permissions"]
    assert signed.get("/api/staff/me", HTTP_HOST=STAFF).status_code == 200
