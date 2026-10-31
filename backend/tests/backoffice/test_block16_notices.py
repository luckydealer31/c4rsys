"""Block 16 part Б: a system block of a company or an account and its end in the notification
centre (ТЗ § 20.3.4, § 24.2.2)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.notifications.models import Notification, NotificationRecipient
from tests.backoffice.helpers import Member, custom_role, grant, sp
from tests.backoffice.test_blocking import _block, _usr
from tests.companies.helpers import hire
from tests.rentals.scenario import Scenario

pytestmark = pytest.mark.django_db


def test_company_block_and_restoration_are_in_the_centre(
    admin: Member, owner: Client, s: Scenario, django_capture_on_commit_callbacks: Any
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        blocked = _block(admin, "company", s.cmp, disclosed_reason="Fraud reports")
    assert blocked.status_code == 201, blocked.content
    n = Notification.objects.get(event="company.system_blocked")
    assert n.state == "active" and n.params["reason"] == "Fraud reports"
    assert NotificationRecipient.objects.filter(notification=n).count() == 1
    # The centre belongs to the user: it is read while the company is blocked.
    listed = owner.get("/api/notifications").json()["notifications"]
    assert "company.system_blocked" in {x["event"] for x in listed}
    block_id = blocked.json()["block"]["id"]
    assert sp(admin, f"blocks/{block_id}/restore", {"reason": "Cleared"}).status_code == 200
    n.refresh_from_db()
    assert n.state == "done"
    assert Notification.objects.filter(event="company.system_unblocked").count() == 1


def test_deferred_notice_is_not_in_the_centre_and_the_end_of_a_user_block_is(
    admin: Member, owner: Client, s: Scenario
) -> None:
    custom_role("security_lead", ["block.notice_defer"])
    grant(admin, "security_lead")
    colleague, employment = hire(
        owner, s.cmp, "colleague@example.com", [{"type": "grant", "permission": "vehicle.view"}]
    )
    deferred = _block(admin, "company", s.cmp, defer_reason="Investigation")
    assert deferred.status_code == 201, deferred.content
    assert not Notification.objects.filter(event="company.system_blocked").exists()
    sp(admin, f"blocks/{deferred.json()['block']['id']}/restore", {"reason": "ok"})
    user_block = _block(admin, "user", _usr(colleague))
    assert user_block.status_code == 201, user_block.content
    sp(admin, f"blocks/{user_block.json()['block']['id']}/restore", {"reason": "ok"})
    ended = Notification.objects.get(event="account.system_unblocked")
    assert [str(x) for x in ended.recipients.values_list("employment_id", flat=True)] == [
        employment
    ]
