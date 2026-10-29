"""Events of the modules and the company's rules, part З4 (ТЗ § 19.1.9, § 20.3.4–20.3.6,
§ 14.3.7; decisions З-7, З-8, З-9, З-11)."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from c4rsys.notifications import services as notifications
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.orders.test_close import _close, _finished_rental, desk, frozen_clock  # noqa: F401
from tests.rentals.scenario import ISSUE_AT, Scenario, issue
from tests.rentals.test_issue_policies import PLANNED_END, _decide, _policy

pytestmark = pytest.mark.django_db


def _mine(client: Client, query: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get(f"/api/notifications?{query}").json()["notifications"]
    return items


def _later() -> Any:
    """The journal is read with a lag of two minutes."""
    return timezone.now() + timedelta(minutes=3)


def _rules(client: Client, s: Scenario) -> dict[str, dict[str, Any]]:
    body = client.get(f"/api/c/{s.cmp}/notification-rules").json()
    return {r["event"]: r for r in body["rules"]}


def _set(client: Client, s: Scenario, event: str, **changes: Any) -> Any:
    current = _rules(client, s)[event]
    body = {
        "event": event,
        "enabled": current["enabled"],
        "lead_minutes": current["lead_minutes"],
        "repeat_minutes": current["repeat_minutes"],
        "recipient_ids": current["recipient_ids"],
        "auto_task": current["auto_task"],
        "expected_version": current["version"],
        **changes,
    }
    return post(client, f"/api/c/{s.cmp}/notification-rules/change", body, key())


def _tasks(client: Client, s: Scenario, query: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get(f"/api/c/{s.cmp}/tasks?{query}").json()["tasks"]
    return items


def test_exception_gives_a_system_task(owner: Client, s: Scenario) -> None:
    _policy(owner, s, payment="require")
    rental = issue(
        owner, s, planned_end=PLANNED_END, exception_mode="after_fact", exception_reason="Late"
    ).json()["rental"]
    notifications.run_checks()
    action = _mine(owner, "kind=action&state=active")
    assert [x["event"] for x in action] == ["rental.exception_pending"]
    system = [t for t in _tasks(owner, s) if t["source"] == "system"]
    assert [t["title"] for t in system] == ["rental.exception_pending"]
    task = owner.get(f"/api/c/{s.cmp}/tasks/{system[0]['id']}").json()["task"]
    # § 19.2.1: not done by hand without the required result.
    done = post(
        owner,
        f"/api/c/{s.cmp}/tasks/{task['id']}/complete",
        {"expected_version": task["version"]},
        key(),
    )
    assert done.json()["error"]["code"] == "TASK_SYSTEM_RESULT_REQUIRED"
    assert _decide(owner, s, rental, "confirm").status_code == 200
    notifications.run_checks()
    task = owner.get(f"/api/c/{s.cmp}/tasks/{task['id']}").json()["task"]
    assert task["state"] == "done" and task["state_note"] == "confirmed"
    assert _mine(owner, "kind=action&state=active") == []


def test_overdue_return_and_rule_off(owner: Client, s: Scenario) -> None:
    issue(owner, s, planned_end=PLANNED_END)
    late = ISSUE_AT + timedelta(days=3)
    notifications.run_checks(late)
    assert [x["event"] for x in _mine(owner, "kind=warning")] == ["rental.return_overdue"]
    # Repeated every 2 hours by default (decision З-7).
    notifications.run_checks(late + timedelta(hours=3))
    assert _mine(owner, "kind=warning")[0]["repeats"] == 1
    assert _set(owner, s, "rental.return_overdue", enabled=False).status_code == 200
    notifications.run_checks(late + timedelta(hours=6))
    assert _mine(owner, "kind=warning&state=active") == []
    # Mandatory notifications cannot be switched off (§ 20.3.4).
    refused = _set(owner, s, "rental.exception_pending", enabled=False)
    assert refused.json()["error"]["code"] == "NOTIFICATION_MANDATORY"


def test_booking_cancelled_and_upcoming_issue(owner: Client, s: Scenario) -> None:
    start = timezone.now() + timedelta(minutes=30)
    booking = post(
        owner,
        f"/api/c/{s.cmp}/bookings",
        {
            "vehicle_card_id": s.vehicle,
            "planned_start": start.isoformat(),
            "planned_end": (start + timedelta(days=1)).isoformat(),
            "client_id": s.client,
            "tariff_id": s.tariff,
            "issue_location_id": s.location,
        },
        key(),
    ).json()["rental"]
    notifications.run_checks()
    assert [x["event"] for x in _mine(owner, "kind=reminder")] == ["rental.issue_upcoming"]
    cancelled = post(
        owner,
        f"/api/c/{s.cmp}/bookings/{booking['id']}/cancel",
        {"reason": "Client changed plans", "expected_version": booking["version"]},
        key(),
    )
    assert cancelled.status_code == 200, cancelled.content
    notifications.run_checks()
    assert _mine(owner, "kind=reminder&state=active") == []
    info = _mine(owner, "kind=info")
    assert [(x["event"], x["state"], x["read"]) for x in info] == [
        ("rental.booking_cancelled", "done", False)
    ]


def test_debt_of_a_closed_order_with_a_task(owner: Client, s: Scenario, desk: str) -> None:  # noqa: F811
    notifications.run_checks()  # the journal is read from now on
    assert _set(owner, s, "finance.debt", auto_task=True).status_code == 200
    rental = _finished_rental(owner, s)
    assert _close(owner, s, rental["order_id"]).json()["order"]["state"] == "closed"
    notifications.run_checks(_later())
    debt = _mine(owner, "kind=warning")
    assert [x["event"] for x in debt] == ["finance.debt"]
    assert debt[0]["params"]["debt"] == {"THB": "2000.0000"}
    assert [t["title"] for t in _tasks(owner, s) if t["source"] == "system"] == ["finance.debt"]


def test_access_change_is_told(owner: Client, s: Scenario) -> None:
    notifications.run_checks()
    worker, employment = hire(owner, s.cmp, "w@example.com", [{"type": "set", "set": "mechanic"}])
    changed = owner.put(
        f"/api/c/{s.cmp}/employees/{employment}/rights",
        data={"rights": [{"type": "set", "set": "driver"}], "expected_version": 2},
        content_type="application/json",
        headers=key(),
    )
    assert changed.status_code == 200, changed.content
    notifications.run_checks(_later())
    assert [x["event"] for x in _mine(worker)] == ["employee.access_changed"]


def test_chosen_recipients_need_access(owner: Client, s: Scenario) -> None:
    driver, driver_id = hire(owner, s.cmp, "d@example.com", [{"type": "set", "set": "driver"}])
    manager, manager_id = hire(
        owner, s.cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}]
    )
    assert (
        _set(owner, s, "rental.return_overdue", recipient_ids=[driver_id, manager_id]).status_code
        == 200
    )
    issue(owner, s, planned_end=PLANNED_END)
    notifications.run_checks(ISSUE_AT + timedelta(days=3))
    # The driver has no access to rentals (§ 20.3.6): only the manager receives it.
    assert _mine(driver) == []
    assert [x["event"] for x in _mine(manager)] == ["rental.return_overdue"]
    assert _mine(owner) == []
