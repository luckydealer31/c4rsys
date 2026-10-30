"""Service С6: reopening a finished service, correcting a finished work (ТЗ § 16.8, § 9.1.11)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.fleet.models import OdometerReading
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import Scenario
from tests.service.test_maintenance import _add_rule, _service_done, _status, today  # noqa: F401
from tests.service.test_service import _at, _do, _job, _ok, _work

pytestmark = pytest.mark.django_db


def _correct(client: Client, s: Scenario, job: dict[str, Any], **body: Any) -> Any:
    data = {"expected_version": job["version"], "reason": "typo", **body}
    return post(client, f"/api/c/{s.cmp}/service/jobs/{job['id']}/correct", data, key())


def test_correct_a_done_work_recounts_maintenance(owner: Client, s: Scenario) -> None:
    _add_rule(owner, s, interval_distance=5000)
    oil = _work(owner, s, system_work="engine_oil")
    op = _service_done(owner, s, oil, "10500")
    job = op["jobs"][0]

    # A finished work is closed for changes until it is opened for editing.
    closed = _correct(owner, s, job, odometer="10050")
    assert closed.json()["error"]["code"] == "SERVICE_JOB_NOT_OPEN"
    opened = _ok(_job(owner, s, job, "open", reason="wrong odometer"))
    job = opened["jobs"][0]
    # It stays done; its time does not change by the opening.
    assert job["state"] == "done" and job["editing_since"]
    assert opened["state"] == "works_done"
    wrong = _correct(owner, s, job, actual_end=_at(-5))
    assert wrong.json()["error"]["field"] == "actual_end"  # before the start: not saved
    fixed = _correct(owner, s, job, odometer="10050")
    assert fixed.status_code == 200, fixed.content
    rules = owner.get(f"/api/c/{s.cmp}/service/vehicles/{s.vehicle}/maintenance")
    # § 16.8.2: the next maintenance counts from the corrected facts.
    assert _status(rules)["base_odometer"] == "10050"
    # The wrong reading stays, replaced by the corrected one.
    readings = OdometerReading.objects.filter(source_type="service_job", source_id=job["id"])
    assert sorted(r.replaced_by_id is None for r in readings) == [False, True]
    event = AuditEvent.objects.get(action="service_job.corrected")
    before, after = event.before or {}, event.after or {}
    assert before["odometer"] == "10500" and after["odometer"] == "10050"
    assert event.reason == "typo"
    # The service stays finished; the work keeps its result.
    assert fixed.json()["operation"]["state"] == "works_done"
    job = fixed.json()["operation"]["jobs"][0]
    closed_again = _ok(_job(owner, s, job, "close"))
    assert closed_again["jobs"][0]["editing_since"] is None


def test_reopen_needs_its_own_right_and_an_open_order(owner: Client, s: Scenario) -> None:
    oil = _work(owner, s, system_work="engine_oil")
    op = _service_done(owner, s, oil, "10500")
    rights = [{"type": "grant", "permission": p} for p in ("service.view", "service.complete")]
    manager, _ = hire(owner, s.cmp, "manager@example.com", rights)
    assert _do(manager, s, op, "reopen", reason="mistake").status_code == 403
    assert _job(manager, s, op["jobs"][0], "open", reason="x").status_code == 403

    no_reason = _do(owner, s, op, "reopen", reason="")
    assert no_reason.json()["error"]["field"] == "reason"
    reopened = _ok(_do(owner, s, op, "reopen", reason="one more work"))
    assert reopened["state"] == "in_progress"
    # The done work keeps its result; the service is finished again by the usual action, but
    # not while a work is open for editing.
    assert reopened["jobs"][0]["state"] == "done"
    editing = _ok(_job(owner, s, reopened["jobs"][0], "open", reason="check"))
    blocked = _do(owner, s, editing, "finish")
    assert blocked.json()["error"]["code"] == "SERVICE_JOBS_NOT_FINISHED"
    done = _ok(_job(owner, s, editing["jobs"][0], "close"))
    assert _ok(_do(owner, s, done, "finish"))["state"] == "works_done"
