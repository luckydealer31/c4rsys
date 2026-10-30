"""Deactivation and restoration of a company (ТЗ § 3.6)."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.companies import deactivation
from c4rsys.companies.models import Company, CompanyStateRequest
from c4rsys.platform.models import AuditEvent
from c4rsys.tasks import series
from c4rsys.tasks.models import Task, TaskSeries
from tests.accounts.helpers import CODE, post
from tests.companies.helpers import hire, key
from tests.companies.test_ownership import (
    _policy,
    _two_owners,
    answer,
    code,
    company,
    started,
    transfer,
    user,
)
from tests.rentals.scenario import build, issue

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_code_limits() -> Iterator[None]:
    """Several confirmations in one test; the limits of § 2.4.4 are tested in accounts."""
    with mock.patch("c4rsys.accounts.otp.check_send_allowed"):
        yield


@pytest.fixture
def cmp(owner: Client) -> str:
    return company(owner)


def deactivate(client: Client, cmp: str, code_value: str = CODE) -> Any:
    flow = post(client, "/api/me/confirmations", {"action": "deactivate_company"}).json()["flow"]
    return post(client, f"/api/c/{cmp}/deactivation", {"flow": flow, "code": code_value}, key())


def restore(client: Client, cmp: str) -> Any:
    return post(client, f"/api/c/{cmp}/restoration", {}, key())


def is_deactivated(cmp: str) -> bool:
    return Company.objects.get(public_id=cmp).deactivated_at is not None


def state_answer(client: Client, cmp: str, approve: bool = True) -> Any:
    items = [x for x in client.get("/api/ownership-requests").json()["states"]]
    found = [x for x in items if x["company"]["public_id"] == cmp]
    assert found, items
    verb = "approve" if approve else "decline"
    return post(
        client,
        f"/api/ownership-requests/states/{found[0]['request_id']}/{verb}",
        {"party": found[0]["party_key"]},
        key(),
    )


@pytest.fixture
def blocked() -> Iterator[list[str]]:
    codes: list[str] = []
    deactivation.register_blocker(lambda company_id: list(codes))
    yield codes
    deactivation._blockers.pop()


def test_single_owner_deactivates_with_a_code(owner: Client, cmp: str) -> None:
    assert code(deactivate(owner, cmp, "000000")) == "CODE_INVALID"
    response = deactivate(owner, cmp)
    assert response.status_code == 200, response.content
    assert is_deactivated(cmp)
    # The banner reads the state of the company; the data and the ID stay (§ 3.6.1).
    assert owner.get(f"/api/c/{cmp}").json()["company"]["deactivated_at"]
    assert AuditEvent.objects.filter(action="company.deactivated").count() == 1
    assert code(deactivate(owner, cmp)) == "COMPANY_ALREADY_DEACTIVATED"


def test_deactivation_needs_the_right(owner: Client, cmp: str) -> None:
    worker, _ = hire(owner, cmp, "w@example.com", [{"type": "grant", "permission": "client.list"}])
    assert deactivate(worker, cmp).status_code == 403
    admin, _ = hire(owner, cmp, "adm@example.com", [{"type": "set", "set": "company_admin"}])
    assert deactivate(admin, cmp).status_code == 200


def test_blockers_stop_the_deactivation(owner: Client, cmp: str, blocked: list[str]) -> None:
    blocked.append("rentals_not_finished")
    response = deactivate(owner, cmp)
    assert code(response) == "COMPANY_DEACTIVATION_BLOCKED"
    assert response.json()["error"]["params"]["blockers"] == ["rentals_not_finished"]
    assert not is_deactivated(cmp)
    assert owner.get(f"/api/c/{cmp}/state").json()["blockers"] == ["rentals_not_finished"]


def test_an_active_rental_blocks_the_deactivation(owner: Client) -> None:
    s = build(owner)
    issued = issue(owner, s, actual_start=(timezone.now() - timedelta(minutes=5)).isoformat())
    assert issued.status_code == 201, issued.content
    response = deactivate(owner, s.cmp)
    assert code(response) == "COMPANY_DEACTIVATION_BLOCKED"
    assert "rentals_not_finished" in response.json()["error"]["params"]["blockers"]


def test_no_new_activity_but_settlements_go_on(owner: Client, cmp: str) -> None:
    account = post(
        owner,
        f"/api/c/{cmp}/cash-accounts",
        {"name": "Main cash", "type": "cash", "currency": "THB"},
        key(),
    )
    assert account.status_code == 201, account.content
    account_id = account.json()["cash_account"]["id"]
    assert deactivate(owner, cmp).status_code == 200

    # New activity is refused in one place for every command (§ 3.6.1).
    created = post(owner, f"/api/c/{cmp}/locations", {"name": "Phuket"}, key())
    assert code(created) == "COMPANY_DEACTIVATED"
    new_account = post(
        owner,
        f"/api/c/{cmp}/cash-accounts",
        {"name": "Bank", "type": "bank", "currency": "THB"},
        key(),
    )
    assert code(new_account) == "COMPANY_DEACTIVATED"
    task = post(owner, f"/api/c/{cmp}/tasks", {"title": "Call the insurer"}, key())
    assert code(task) == "COMPANY_DEACTIVATED"
    # Former settlements stay allowed (§ 3.6.3): a payout of a debt is a money record.
    articles = owner.get(f"/api/c/{cmp}/finance-articles").json()["articles"]
    article = next(a["id"] for a in articles if a["kind"] == "expense")
    payout = post(
        owner,
        f"/api/c/{cmp}/money-records",
        {
            "direction": "out",
            "cash_account_id": account_id,
            "amount": "100",
            "currency": "THB",
            "article_id": article,
        },
        key(),
    )
    assert payout.status_code == 201, payout.content
    renamed = owner.patch(
        f"/api/c/{cmp}/cash-accounts/{account_id}",
        data={"name": "Old cash", "expected_version": account.json()["cash_account"]["version"]},
        content_type="application/json",
        headers=key(),
    )
    assert renamed.status_code == 200, renamed.content
    assert deactivation.allowed_when_deactivated("client.refund")
    assert deactivation.allowed_when_deactivated("supplier.payment")
    assert deactivation.allowed_when_deactivated("deposit.refund")
    assert not deactivation.allowed_when_deactivated("order.create")
    assert not deactivation.allowed_when_deactivated("rental.issue")
    # History and export by rights stay; the profile may be read.
    assert owner.get(f"/api/c/{cmp}").status_code == 200

    assert restore(owner, cmp).status_code == 200
    assert not is_deactivated(cmp)
    created = post(owner, f"/api/c/{cmp}/locations", {"name": "Phuket"}, key())
    assert created.status_code == 201, created.content


def test_several_owners_default_to_all(owner: Client, cmp: str) -> None:
    b, _ = _two_owners(owner, cmp)
    response = deactivate(owner, cmp)
    assert response.status_code == 200
    body = response.json()
    assert body["policy"]["configured"] == "unset" and body["policy"]["mode"] == "all"
    assert body["request"]["state"] == "pending" and not is_deactivated(cmp)
    assert state_answer(b, cmp).status_code == 200
    assert is_deactivated(cmp)


def test_policy_none_and_percent(owner: Client, cmp: str) -> None:
    b, _ = _two_owners(owner, cmp)
    assert _policy(owner, cmp, state_mode="none").status_code == 200
    assert deactivate(owner, cmp).status_code == 200 and is_deactivated(cmp)
    assert restore(owner, cmp).status_code == 200 and not is_deactivated(cmp)
    assert _policy(owner, cmp, state_mode="percent", state_percent="80").status_code == 200
    assert deactivate(owner, cmp).json()["request"]["state"] == "pending"
    assert state_answer(b, cmp).status_code == 200
    assert is_deactivated(cmp)


def test_a_decline_under_all_ends_the_request(owner: Client, cmp: str) -> None:
    b, _ = _two_owners(owner, cmp)
    deactivate(owner, cmp)
    assert state_answer(b, cmp, approve=False).status_code == 200
    assert CompanyStateRequest.objects.get().state == "declined"
    assert not is_deactivated(cmp)


def test_changed_owners_make_former_approvals_stale(owner: Client, cmp: str) -> None:
    b, b_id = _two_owners(owner, cmp)
    c, c_id = user("c@example.com")
    t = started(transfer(owner, cmp, c_id, "20"))
    answer(c, t["id"])  # owners: A 50, B 30, C 20
    deactivate(owner, cmp)  # A approves by starting
    assert state_answer(b, cmp).status_code == 200
    # The shares change while the request waits (§ 3.6.2).
    t = started(transfer(owner, cmp, b_id, "5"))
    answer(b, t["id"])
    assert state_answer(c, cmp).status_code == 200
    assert not is_deactivated(cmp)
    request = CompanyStateRequest.objects.get()
    payload = deactivation.request_payload(request)
    assert [h["counted"] for h in payload["history"]] == [False, False, True]
    # A and B are asked again by the current composition.
    assert state_answer(b, cmp).status_code == 200
    assert state_answer(owner, cmp).status_code == 200
    assert is_deactivated(cmp)


def test_blocker_found_right_before_deactivation(
    owner: Client, cmp: str, blocked: list[str]
) -> None:
    b, _ = _two_owners(owner, cmp)
    deactivate(owner, cmp)
    blocked.append("service_operations_not_finished")
    assert state_answer(b, cmp).status_code == 200
    assert not is_deactivated(cmp)
    request = CompanyStateRequest.objects.get()
    retry = post(owner, f"/api/c/{cmp}/state-requests/{request.id}/complete", {}, key())
    assert code(retry) == "COMPANY_DEACTIVATION_BLOCKED"
    blocked.clear()
    assert (
        post(owner, f"/api/c/{cmp}/state-requests/{request.id}/complete", {}, key()).status_code
        == 200
    )
    assert is_deactivated(cmp)


def test_repeating_tasks_pause_and_resume_without_backfill(owner: Client, cmp: str) -> None:
    today = timezone.localdate()
    created = post(
        owner,
        f"/api/c/{cmp}/tasks",
        {
            "title": "Wash the fleet",
            "due_date": today.isoformat(),
            "due_time": "23:59",
            "repeat": {"frequency": "daily"},
        },
        key(),
    )
    assert created.status_code == 201, created.content
    series_id = created.json()["task"]["series"]["id"]
    assert deactivate(owner, cmp).status_code == 200
    later = timezone.now() + timedelta(days=5)
    assert series.run(later) == 0  # paused (§ 3.6.4)
    assert Task.objects.filter(series_id=series_id).count() == 1
    TaskSeries.objects.filter(pk=series_id).update(next_on=today - timedelta(days=3))
    assert restore(owner, cmp).status_code == 200
    resumed = TaskSeries.objects.get(pk=series_id)
    # The repetitions of the deactivation period are not made; the next is the nearest ahead.
    assert resumed.next_on is not None and resumed.next_on >= today
    assert series.run(timezone.now()) == 0 or resumed.next_on == today
    assert Task.objects.filter(series_id=series_id, series_on__lt=today).count() == 0
