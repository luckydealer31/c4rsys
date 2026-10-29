"""Notifications of tasks (ТЗ § 19.2.2, § 20.3.3, § 5.2.3; decisions З-1, З-4, З-5).

* Assigning or reassigning another employee tells the executor; the notice ends when the task
  is taken in work, reassigned or closed.
* Reminders before the deadline (up to 5, «N minutes before») are one active reminder with its
  repetitions in the history.
* After the deadline the executor, the creator and the employees with «manage» in the task's
  scope are told (decision З-1); the notice repeats every N hours by the company's rule (З-4).
* Once the task is done or cancelled every notice of it ends for all recipients.
* An executor blocked or without access to tasks in the scope gives the active task back to the
  queue (§ 19.1.6–19.1.7).
"""

from datetime import datetime, timedelta
from uuid import UUID

from django.db import transaction

from c4rsys.notifications import services as notifications
from c4rsys.platform.models import AuditEvent
from c4rsys.tasks.models import ACTIVE_STATES, Task, TaskPolicy

PROCESS = "task_schedule"
MAX_REMINDERS = 5


def _condition(kind: str, t: Task) -> str:
    return f"task.{kind}:{t.id}"


def _params(t: Task) -> dict[str, object]:
    return {
        "number": t.number,
        "title": t.title,
        "due_at": t.due_at.isoformat() if t.due_at else None,
    }


def _notify(
    t: Task,
    kind: str,
    notice_kind: str,
    people: list[UUID],
    *,
    repeat: bool = False,
    now: datetime | None = None,
) -> None:
    notifications.notify(
        t.company_id,
        condition=_condition(kind, t),
        event=f"task.{kind}",
        kind=notice_kind,
        recipients=people,
        location_id=t.location_id,
        object_type="task",
        object_id=t.id,
        params=_params(t),
        repeat=repeat,
        now=now,
    )


def sync(t: Task, actor_employment_id: UUID | None) -> None:
    """After every change of a task: what its notices should be now."""
    if t.state not in ACTIVE_STATES:
        for kind in ("assigned", "reminder", "overdue"):
            notifications.resolve(t.company_id, _condition(kind, t), "task_closed")
        return
    assigned = notifications.active(t.company_id, _condition("assigned", t))
    current = assigned.params.get("assignee") if assigned else None
    wanted = str(t.assignee_id) if t.assignee_id and t.state == Task.State.OPEN else None
    if current != wanted:
        if assigned is not None:
            notifications.resolve(t.company_id, assigned.condition, "task_assignment_changed")
        # One's own assignment is not news (§ 19.1.2).
        if wanted is not None and t.assignee_id != actor_employment_id:
            notifications.notify(
                t.company_id,
                condition=_condition("assigned", t),
                event="task.assigned",
                kind=notifications.Kind.INFO,
                recipients=[t.assignee_id],  # type: ignore[list-item]
                location_id=t.location_id,
                object_type="task",
                object_id=t.id,
                params={**_params(t), "assignee": wanted},
            )
    # A changed deadline starts the reminders and the overdue notice anew.
    reminder = notifications.active(t.company_id, _condition("reminder", t))
    overdue = notifications.active(t.company_id, _condition("overdue", t))
    due = t.due_at.isoformat() if t.due_at else None
    for notice in (reminder, overdue):
        if notice is not None and notice.params.get("due_at") != due:
            notifications.resolve(t.company_id, notice.condition, "task_due_changed")


def _overdue_people(t: Task) -> list[UUID]:
    """Decision З-1: the executor, the creator and «manage» in the scope; without the
    organisation structure yet."""
    people = {x for x in (t.assignee_id, t.created_by_employment_id) if x}
    people |= set(notifications.with_permission(t.company_id, "tasks.manage", t.location_id))
    return sorted(people) or notifications.heads(t.company_id)


def _release_lost(t: Task, now: datetime) -> bool:
    from c4rsys.tasks import services

    if t.assignee_id is None or services.eligible(t.company_id, t.location_id, t.assignee_id):
        return False
    before = str(t.assignee_id)
    t.assignee_id = None
    t.state = Task.State.OPEN
    t.state_note = ""
    t.version += 1
    t.save()
    AuditEvent.objects.create(
        company_id=t.company_id,
        system_process=PROCESS,
        action="task.released",
        object_type="task",
        object_id=str(t.id),
        before={"assignee": before},
        after={"assignee": None, "state": t.state},
        reason="executor_without_access",
    )
    sync(t, None)
    return True


def run(now: datetime) -> int:
    """Reminders, overdue notices and tasks of executors without access. Run every 5 minutes."""
    done = 0
    repeat_hours = dict(TaskPolicy.objects.values_list("company_id", "overdue_repeat_hours"))
    for task_id in Task.objects.filter(state__in=ACTIVE_STATES).values_list("id", flat=True):
        with transaction.atomic():
            t = Task.objects.select_for_update().get(pk=task_id)
            if t.state not in ACTIVE_STATES:
                continue
            if _release_lost(t, now):
                done += 1
            if t.due_at is None:
                continue
            people = [t.assignee_id] if t.assignee_id else _overdue_people(t)
            if now < t.due_at:
                due = [
                    m
                    for m in t.reminders
                    if m not in t.reminders_sent and now >= t.due_at - timedelta(minutes=m)
                ]
                if due:
                    exists = notifications.active(t.company_id, _condition("reminder", t))
                    _notify(
                        t,
                        "reminder",
                        notifications.Kind.REMINDER,
                        people,
                        repeat=exists is not None,
                        now=now,
                    )
                    t.reminders_sent = sorted(set(t.reminders_sent) | set(due))
                    t.save(update_fields=["reminders_sent"])
                    done += 1
                continue
            notifications.resolve(t.company_id, _condition("reminder", t), "task_overdue", now)
            overdue = notifications.active(t.company_id, _condition("overdue", t))
            hours = repeat_hours.get(t.company_id, 24)
            if overdue is None:
                _notify(t, "overdue", notifications.Kind.WARNING, _overdue_people(t), now=now)
                done += 1
            elif hours and now - overdue.last_at >= timedelta(hours=hours):
                _notify(
                    t,
                    "overdue",
                    notifications.Kind.WARNING,
                    _overdue_people(t),
                    repeat=True,
                    now=now,
                )
                done += 1
    return done
