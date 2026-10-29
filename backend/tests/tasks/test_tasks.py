"""Work tasks, part З1 (ТЗ § 19.1–19.3, § 23.9; customer's decisions З-1…З-11 of 29.10.2026)."""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.rentals import issue as issue_module
from c4rsys.tasks.models import Task
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.files.helpers import upload
from tests.rentals.scenario import NOW, Scenario, add_location, issue

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\n" + b"0" * 100


def _create(client: Client, s: Scenario, **body: Any) -> Any:
    return post(client, f"/api/c/{s.cmp}/tasks", {"title": "Call the insurer", **body}, key())


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.content
    task: dict[str, Any] = response.json()["task"]
    return task


def _do(client: Client, s: Scenario, t: dict[str, Any], action: str, **body: Any) -> Any:
    data = {"expected_version": t["version"], **body}
    return post(client, f"/api/c/{s.cmp}/tasks/{t['id']}/{action}", data, key())


def _list(client: Client, s: Scenario, query: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get(f"/api/c/{s.cmp}/tasks?{query}").json()["tasks"]
    return items


def _code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def test_create_for_self_queue_and_other(owner: Client, s: Scenario) -> None:
    mechanic, mechanic_id = hire(
        owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}]
    )
    mine = _ok(_create(owner, s, priority="high", due_date="2026-11-05"), 201)
    assert mine["is_mine"] and mine["state"] == "open" and mine["priority"] == "high"
    # § 19.2.2: without a time the deadline is the end of the day in the company's zone.
    assert mine["due_at"].startswith("2026-11-05T23:59:59") and mine["due_at"].endswith("+07:00")
    queued = _ok(_create(mechanic, s, title="Wash the van", assign="queue"), 201)
    assert queued["assignee_id"] is None
    # Assigning another employee needs «manage» (§ 23.9.3).
    refused = _create(mechanic, s, assign=str(mechanic_id))
    assert refused.status_code == 403
    other = _ok(_create(owner, s, title="Check tyres", assign=mechanic_id), 201)
    assert other["assignee_id"] == mechanic_id

    assert [t["title"] for t in _list(mechanic, s, "view=mine")] == ["Check tyres"]
    assert [t["title"] for t in _list(mechanic, s, "view=queue")] == ["Wash the van"]
    # The mechanic does not see the owner's own task (no «all tasks of the area»).
    assert "Call the insurer" not in {t["title"] for t in _list(mechanic, s)}
    assert mechanic.get(f"/api/c/{s.cmp}/tasks/{mine['id']}").status_code == 404


def test_queue_states_and_closing(owner: Client, s: Scenario) -> None:
    mechanic, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}])
    t = _ok(_create(owner, s, assign="queue"), 201)
    t = _ok(_do(mechanic, s, t, "take"))
    assert t["state"] == "in_progress" and t["is_mine"]
    assert _code(_do(mechanic, s, t, "wait", note="")) == "FIELD_REQUIRED"
    t = _ok(_do(mechanic, s, t, "wait", note="Answer of the insurer"))
    assert t["state"] == "waiting" and t["state_note"] == "Answer of the insurer"
    t = _ok(_do(mechanic, s, t, "resume"))
    t = _ok(_do(mechanic, s, t, "pause", note="Holiday"))
    t = _ok(_do(mechanic, s, t, "resume"))
    # By default the executor may return the task to the queue (decision З-2).
    t = _ok(_do(mechanic, s, t, "release"))
    assert t["state"] == "open" and t["assignee_id"] is None
    t = _ok(_do(mechanic, s, t, "take"))
    t = _ok(_do(mechanic, s, t, "complete"))
    assert t["state"] == "done" and t["closed_at"]
    # § 19.2.1: a done task is not opened again.
    assert _code(_do(mechanic, s, t, "start")) == "TASK_CLOSED"
    history = [e["action"] for e in t["history"]]
    assert history[:3] == ["task.created", "task.taken", "task.waiting"]


def test_cancel_rules(owner: Client, s: Scenario) -> None:
    mechanic, mechanic_id = hire(
        owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}]
    )
    own = _ok(_create(mechanic, s, assign="queue"), 201)
    # The creator cancels before the task is taken, without a reason.
    assert _ok(_do(mechanic, s, own, "cancel"))["state"] == "cancelled"
    taken = _ok(_create(owner, s, assign=mechanic_id), 201)
    taken = _ok(_do(mechanic, s, taken, "start"))
    assert _do(mechanic, s, taken, "cancel", reason="x").status_code == 403
    assert _code(_do(owner, s, taken, "cancel")) == "FIELD_REQUIRED"
    done = _ok(_do(owner, s, taken, "cancel", reason="Not needed"))
    assert done["cancel_reason"] == "Not needed"


def test_deadline_policy_and_overdue(owner: Client, s: Scenario) -> None:
    mechanic, mechanic_id = hire(
        owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}]
    )
    past = (timezone.localdate() - timedelta(days=1)).isoformat()
    t = _ok(_create(owner, s, assign=mechanic_id, due_date=past, due_time="09:30"), 201)
    assert t["overdue"] and t["due_has_time"]
    assert [x["id"] for x in _list(mechanic, s, "overdue=1")] == [t["id"]]
    change = {"due_date": "2026-12-01", "expected_version": t["version"]}
    url = f"/api/c/{s.cmp}/tasks/{t['id']}"
    refused = mechanic.patch(url, data=change, content_type="application/json", headers=key())
    assert refused.status_code == 403
    policy = owner.get(f"/api/c/{s.cmp}/tasks/policy").json()["policy"]
    owner.put(
        f"/api/c/{s.cmp}/tasks/policy",
        data={
            "executor_may_release": False,
            "executor_may_change_due": True,
            "expected_version": policy["version"],
        },
        content_type="application/json",
        headers=key(),
    )
    moved = mechanic.patch(url, data=change, content_type="application/json", headers=key())
    t = _ok(moved)
    assert not t["overdue"]
    # The journal of the command is written with it; the card shows it on the next reading.
    fresh = _ok(owner.get(url))
    changed = next(e for e in fresh["history"] if e["action"] == "task.changed")
    assert changed["before"]["due_at"] and changed["after"]["due_at"]
    t = _ok(_do(mechanic, s, t, "start"))
    assert _do(mechanic, s, t, "release").status_code == 403


def test_scope_follows_the_object(owner: Client, s: Scenario) -> None:
    other = add_location(owner, s.cmp, "Kata")
    local, _ = hire(
        owner,
        s.cmp,
        "kata@example.com",
        [{"type": "set", "set": "rental_manager", "locations": [other]}],
    )
    clock = SimpleNamespace(now=lambda: NOW)
    with mock.patch.object(issue_module, "timezone", clock):
        rental = issue(owner, s).json()["rental"]
    target = {"object_type": "rental", "object_id": rental["id"]}
    t = _ok(_create(owner, s, object=target, assign="queue"), 201)
    assert t["location_id"] == s.location
    # § 19.1.7: the scope is never wider than the object's.
    wider = _create(owner, s, object=target, location_id=None)
    assert _code(wider) == "TASK_SCOPE_WIDER_THAN_OBJECT"
    assert t["id"] in {
        x["id"] for x in _list(owner, s, f"object_type=rental&object_id={rental['id']}")
    }
    # An employee of another location does not see the queue of this one.
    assert _list(local, s, "view=queue") == []
    assert _do(local, s, t, "take").status_code == 404


def test_comments_and_attachments(owner: Client, s: Scenario) -> None:
    mechanic, mechanic_id = hire(
        owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}]
    )
    t = _ok(_create(owner, s, assign=mechanic_id), 201)
    file_id = upload(mechanic, "task_attachment", PDF, key=key()["Idempotency-Key"]).json()["file"][
        "id"
    ]
    t = _ok(
        _do(
            mechanic,
            s,
            t,
            "comment",
            text="Photo of the damage",
            file_id=file_id,
            file_name="a.pdf",
        )
    )
    assert t["comments"][0]["text"] == "Photo of the damage"
    assert owner.get(f"/api/files/{file_id}").status_code == 200
    stranger, _ = hire(owner, s.cmp, "x@example.com", [{"type": "set", "set": "driver"}])
    assert stranger.get(f"/api/files/{file_id}").status_code == 404
    assert Task.objects.get(pk=t["id"]).version == t["version"]
