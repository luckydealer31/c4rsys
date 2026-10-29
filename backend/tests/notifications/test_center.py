"""The notification centre, part З3 (ТЗ § 19.2.2, § 20.1–20.3, § 18.1.13; decisions З-1, З-4,
З-5, Р-7)."""

from datetime import datetime, timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.companies.models import Employment
from c4rsys.notifications import services as notifications
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.documents.test_registry import _create as _document
from tests.documents.test_registry import _do as _document_do
from tests.rentals.scenario import Scenario
from tests.tasks.test_tasks import _create, _do, _ok

pytestmark = pytest.mark.django_db


def _mine(client: Client, query: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get(f"/api/notifications?{query}").json()["notifications"]
    return items


def _unread(client: Client) -> int:
    count: int = client.get("/api/notifications/count").json()["unread"]
    return count


def test_assignment_tells_the_executor(owner: Client, s: Scenario) -> None:
    mechanic, mechanic_id = hire(
        owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}]
    )
    _ok(_create(owner, s, title="Mine"), 201)
    assert _mine(owner) == []  # one's own assignment is not news
    t = _ok(_create(owner, s, assign=mechanic_id), 201)
    assert _unread(mechanic) == 1
    item = _mine(mechanic)[0]
    assert item["event"] == "task.assigned" and item["kind"] == "info"
    assert item["params"]["number"] == t["number"] and item["company"]["public_id"] == s.cmp
    read = post(mechanic, "/api/notifications/read", {"ids": [item["id"]]}, key())
    assert read.json() == {"marked": 1}
    # Reading does not end it (§ 20.3.3); taking the task in work does.
    assert _unread(mechanic) == 0 and _mine(mechanic, "state=active")
    _ok(_do(mechanic, s, t, "start"))
    assert _mine(mechanic, "state=active") == [] and _mine(mechanic, "state=done")


def test_reminders_overdue_and_repetitions(owner: Client, s: Scenario) -> None:
    mechanic, mechanic_id = hire(
        owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}]
    )
    day = (timezone.localdate() + timedelta(days=2)).isoformat()
    t = _ok(
        _create(owner, s, assign=mechanic_id, due_date=day, due_time="12:00", reminders=[60, 30]),
        201,
    )
    due = datetime.fromisoformat(t["due_at"])
    assert notifications.run_checks(due - timedelta(minutes=90)) == 0
    assert notifications.run_checks(due - timedelta(minutes=50)) >= 1
    assert [x["event"] for x in _mine(mechanic, "kind=reminder")] == ["task.reminder"]
    notifications.run_checks(due - timedelta(minutes=20))
    reminder = _mine(mechanic, "kind=reminder")
    # § 20.3.2: one active notification, the repetition in its history.
    assert len(reminder) == 1 and reminder[0]["repeats"] == 1
    history = mechanic.get(f"/api/notifications/{reminder[0]['id']}").json()["notification"]
    assert len(history["history"]) == 1

    notifications.run_checks(due + timedelta(minutes=5))
    assert _mine(mechanic, "kind=reminder&state=active") == []
    overdue = _mine(mechanic, "kind=warning&state=active")
    assert [x["event"] for x in overdue] == ["task.overdue"]
    # Decision З-1: the creator and «manage» are told too.
    assert [x["event"] for x in _mine(owner, "kind=warning")] == ["task.overdue"]
    notifications.run_checks(due + timedelta(hours=2))
    assert _mine(mechanic, "kind=warning")[0]["repeats"] == 0
    notifications.run_checks(due + timedelta(hours=25))
    assert _mine(mechanic, "kind=warning")[0]["repeats"] == 1  # daily by default (З-4)
    t = _ok(owner.get(f"/api/c/{s.cmp}/tasks/{t['id']}"))
    t = _ok(_do(mechanic, s, t, "start"))
    _ok(_do(mechanic, s, t, "complete"))
    assert _mine(owner, "state=active") == []


def test_executor_without_access_returns_the_task(owner: Client, s: Scenario) -> None:
    _mechanic, mechanic_id = hire(
        owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}]
    )
    t = _ok(_create(owner, s, assign=mechanic_id), 201)
    Employment.objects.filter(pk=mechanic_id).update(state=Employment.State.BLOCKED)
    notifications.run_checks()
    t = _ok(owner.get(f"/api/c/{s.cmp}/tasks/{t['id']}"))
    assert t["assignee_id"] is None and t["state"] == "open"
    released = [e for e in t["history"] if e["action"] == "task.released"]
    assert released[0]["actor"]["system_process"] == "task_schedule"


def test_document_term_goes_to_the_centre(owner: Client, s: Scenario) -> None:
    today = timezone.localdate()
    d = _document(owner, s, valid_until=(today + timedelta(days=5)).isoformat(), warn_days=10)
    d = d.json()["document"]
    notifications.run_checks()
    notifications.run_checks()  # no repetitions (§ 20.3.6)
    found = _mine(owner, "kind=warning")
    assert [(x["event"], x["params"]["state"], x["repeats"]) for x in found] == [
        ("document.term", "expiring", 0)
    ]
    _document_do(owner, s, d, "archive", reason="Sold")
    notifications.run_checks()
    assert _mine(owner, "state=active") == []


def test_read_all_and_filters(owner: Client, s: Scenario) -> None:
    mechanic, mechanic_id = hire(
        owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}]
    )
    _create(owner, s, assign=mechanic_id)
    _create(owner, s, title="Second", assign=mechanic_id)
    assert _unread(mechanic) == 2
    assert len(_mine(mechanic, f"company={s.cmp}&kind=info")) == 2
    assert _mine(mechanic, "company=CMP-NONE") == []
    post(mechanic, "/api/notifications/read", {"all": True}, key())
    assert _unread(mechanic) == 0
    assert _mine(mechanic, "unread=1") == []
