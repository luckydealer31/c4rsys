"""Work tasks, part З2: checklists, groups, tags, dependencies and repetition (ТЗ § 19.4,
§ 23.9.1, § 23.9.5)."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from c4rsys.tasks import series
from c4rsys.tasks.models import Task
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import Scenario
from tests.tasks.test_tasks import _code, _create, _do, _list, _ok

pytestmark = pytest.mark.django_db

BKK = ZoneInfo("Asia/Bangkok")


def _get(client: Client, s: Scenario, t: dict[str, Any]) -> dict[str, Any]:
    return _ok(client.get(f"/api/c/{s.cmp}/tasks/{t['id']}"))


def test_checklist_required_for_completion(owner: Client, s: Scenario) -> None:
    mechanic, mechanic_id = hire(
        owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}]
    )
    t = _ok(
        _create(
            owner,
            s,
            assign=mechanic_id,
            checklist=["Photo of the odometer", "Fuel level"],
            checklist_required=True,
        ),
        201,
    )
    items = t["checklist"]
    assert [x["text"] for x in items] == ["Photo of the odometer", "Fuel level"]
    t = _ok(_do(mechanic, s, t, "start"))
    assert _code(_do(mechanic, s, t, "complete")) == "TASK_CHECKLIST_NOT_DONE"
    # The executor marks items, but the composition is changed with «manage» (§ 23.9.1).
    assert _do(mechanic, s, t, "checklist_add", text="Tyres").status_code == 403
    for item in items:
        assert (
            _do(mechanic, s, t, "checklist_mark", item_id=item["id"], done=True).status_code == 200
        )
    t = _get(mechanic, s, t)
    assert all(x["done"] and x["done_at"] for x in t["checklist"])
    t = _ok(_do(owner, s, t, "checklist_add", text="Tyres"))
    added = t["checklist"][-1]
    t = _ok(_do(owner, s, t, "checklist_move", item_id=added["id"], position=0))
    assert t["checklist"][0]["text"] == "Tyres"
    t = _ok(_do(owner, s, t, "checklist_remove", item_id=added["id"]))
    assert _ok(_do(mechanic, s, t, "complete"))["state"] == "done"


def test_groups_and_tags(owner: Client, s: Scenario) -> None:
    mechanic, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}])
    assert post(mechanic, f"/api/c/{s.cmp}/tasks/tags", {"name": "x"}, key()).status_code == 403
    group = post(owner, f"/api/c/{s.cmp}/tasks/groups", {"name": "Season start"}, key()).json()[
        "group"
    ]
    tag = post(owner, f"/api/c/{s.cmp}/tasks/tags", {"name": "urgent"}, key()).json()["tag"]
    same = post(owner, f"/api/c/{s.cmp}/tasks/tags", {"name": "urgent"}, key())
    assert _code(same) == "TASK_TAG_EXISTS"
    t = _ok(_create(owner, s, group_id=group["id"], tag_ids=[tag["id"]]), 201)
    _create(owner, s, title="Other")
    assert [x["id"] for x in _list(owner, s, f"tag={tag['id']}")] == [t["id"]]
    assert [x["id"] for x in _list(owner, s, f"group={group['id']}")] == [t["id"]]
    assert [x["id"] for x in _list(owner, s, "q=insurer")] == [t["id"]]
    # Renaming keeps the links; removing the group keeps the task (§ 19.4.5, § 19.4.7).
    renamed = owner.patch(
        f"/api/c/{s.cmp}/tasks/tags/{tag['id']}",
        data={"name": "very urgent"},
        content_type="application/json",
        headers=key(),
    )
    assert renamed.status_code == 200
    assert _get(owner, s, t)["tags"] == [{"id": tag["id"], "name": "very urgent"}]
    removed = owner.delete(f"/api/c/{s.cmp}/tasks/groups/{group['id']}", headers=key())
    assert removed.status_code == 200
    assert _get(owner, s, t)["group_id"] is None


def test_dependencies(owner: Client, s: Scenario) -> None:
    first = _ok(_create(owner, s, title="Order parts"), 201)
    second = _ok(_create(owner, s, title="Repair"), 201)
    second = _ok(_do(owner, s, second, "depend", predecessor_id=first["id"]))
    assert second["blocked"] and second["predecessors"][0]["number"] == first["number"]
    assert _code(_do(owner, s, second, "start")) == "TASK_BLOCKED_BY_DEPENDENCY"
    assert _code(_do(owner, s, second, "complete")) == "TASK_BLOCKED_BY_DEPENDENCY"
    # No cycles and no self-dependency (§ 19.4.6).
    assert (
        _code(_do(owner, s, first, "depend", predecessor_id=second["id"]))
        == "TASK_DEPENDENCY_CYCLE"
    )
    assert (
        _code(_do(owner, s, first, "depend", predecessor_id=first["id"])) == "TASK_DEPENDENCY_CYCLE"
    )
    first = _ok(_do(owner, s, first, "start"))
    _ok(_do(owner, s, first, "complete"))
    second = _get(owner, s, second)
    # Done predecessors lift the block but do not start the task.
    assert not second["blocked"] and second["state"] == "open"
    assert _ok(_do(owner, s, second, "start"))["state"] == "in_progress"


def test_cancelled_predecessor_keeps_the_block(owner: Client, s: Scenario) -> None:
    first = _ok(_create(owner, s, title="Order parts"), 201)
    second = _ok(_create(owner, s, title="Repair"), 201)
    second = _ok(_do(owner, s, second, "depend", predecessor_id=first["id"]))
    _ok(_do(owner, s, first, "cancel"))
    assert _get(owner, s, second)["blocked"]
    second = _get(owner, s, second)
    second = _ok(_do(owner, s, second, "undepend", predecessor_id=first["id"]))
    assert not second["blocked"]


def test_monthly_repetition_keeps_the_original_day(owner: Client, s: Scenario) -> None:
    t = _ok(
        _create(
            owner,
            s,
            title="Pay the rent",
            due_date="2027-01-31",
            checklist=["Transfer"],
            repeat={"frequency": "monthly"},
        ),
        201,
    )
    assert t["series_on"] == "2027-01-31" and t["series"]["next_on"] == "2027-02-28"
    assert series.run(datetime(2027, 2, 27, 23, 0, tzinfo=BKK)) == 0
    assert series.run(datetime(2027, 2, 28, 0, 5, tzinfo=BKK)) == 1
    assert series.run(datetime(2027, 3, 31, 1, 0, tzinfo=BKK)) == 1
    made = Task.objects.filter(series_id=t["series"]["id"]).order_by("series_on")
    assert [str(x.series_on) for x in made] == ["2027-01-31", "2027-02-28", "2027-03-31"]
    # Each repetition has its own empty checklist (§ 19.4.4).
    last = _get(owner, s, {"id": str(made.last().id)})  # type: ignore[union-attr]
    assert [(x["text"], x["done"]) for x in last["checklist"]] == [("Transfer", False)]


def test_weekly_pause_skip_and_change_following(owner: Client, s: Scenario) -> None:
    # 2027-01-04 is a Monday.
    t = _ok(
        _create(
            owner,
            s,
            title="Wash the fleet",
            due_date="2027-01-04",
            due_time="18:00",
            repeat={"frequency": "weekly", "weekdays": [1, 4], "ends_on": "2027-03-01"},
        ),
        201,
    )
    assert t["series"]["next_on"] == "2027-01-07"
    # Two weeks without the schedule: only the nearest date is made (§ 24.2.3).
    assert series.run(datetime(2027, 1, 19, 9, 0, tzinfo=BKK)) == 1
    made = Task.objects.filter(series_id=t["series"]["id"]).order_by("series_on")
    assert [str(x.series_on) for x in made] == ["2027-01-04", "2027-01-18"]
    paused = post(
        owner,
        f"/api/c/{s.cmp}/tasks/{t['id']}/repeat",
        {"action": "pause", "expected_version": t["series"]["version"]},
        key(),
    )
    assert paused.status_code == 200, paused.content
    assert series.run(datetime(2027, 1, 22, 9, 0, tzinfo=BKK)) == 0
    # «This and the following»: the first task is done and stays as it was.
    first = _ok(_do(owner, s, t, "start"))
    _ok(_do(owner, s, first, "complete"))
    later = _get(owner, s, {"id": str(made.last().id)})  # type: ignore[union-attr]
    changed = post(
        owner,
        f"/api/c/{s.cmp}/tasks/{later['id']}/repeat",
        {"action": "change", "title": "Wash and vacuum", "expected_version": 2},
        key(),
    )
    assert changed.status_code == 200, changed.content
    assert changed.json()["task"]["title"] == "Wash and vacuum"
    assert _get(owner, s, t)["title"] == "Wash the fleet"
    resumed = post(
        owner,
        f"/api/c/{s.cmp}/tasks/{t['id']}/repeat",
        {"action": "resume", "expected_version": 3},
        key(),
    )
    assert resumed.status_code == 200, resumed.content
