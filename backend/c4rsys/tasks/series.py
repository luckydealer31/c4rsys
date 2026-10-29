"""Repeating tasks (ТЗ § 19.4.1–19.4.2, § 24.2.3).

* Daily, on chosen weekdays or monthly, until an optional end date. Each repetition is a task of
  its own with its deadline, executor, result and an empty checklist; an unfinished former task
  does not stop the next one.
* Monthly: a day missing in a month gives the last day of that month; the next months count from
  the original day again (31 January → 28/29 February → 31 March).
* The first task is made at once for the first date of the repetition; each next one on its date
  by the schedule. Dates missed while the schedule did not run are not made afterwards: the
  repetition goes on from the nearest date (as after a block of the company, § 24.2.3).
* A change of «this and the following» changes the series and this task; done tasks are not
  rewritten. A repetition is paused, resumed or stopped without removing the history.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.platform.models import AuditEvent
from c4rsys.tasks.models import Task, TaskSeries, TaskTagLink

Frequency = TaskSeries.Frequency
PROCESS = "task_schedule"


@dataclass(frozen=True)
class Repeat:
    frequency: str
    weekdays: list[int] = field(default_factory=list)
    ends_on: date | None = None


def _month_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def occurrence_from(series: TaskSeries, start: date) -> date | None:
    """The first date of the repetition on or after `start`, within its end."""
    if series.frequency == Frequency.DAILY:
        found = start
    elif series.frequency == Frequency.WEEKLY:
        days = set(series.weekdays)
        found = next(
            start + timedelta(days=i)
            for i in range(7)
            if (start + timedelta(days=i)).isoweekday() in days
        )
    else:
        day = series.day_of_month or series.starts_on.day
        candidate = _month_day(start.year, start.month, day)
        if candidate < start:
            year, month = (
                (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
            )
            candidate = _month_day(year, month, day)
        found = candidate
    if series.ends_on is not None and found > series.ends_on:
        return None
    return found


def _validate(repeat: Repeat, starts_on: date) -> None:
    if repeat.frequency not in Frequency.values:
        raise errors.validation_error("repeat", code="CHOICE_REQUIRED")
    if repeat.frequency == Frequency.WEEKLY:
        if not repeat.weekdays or not set(repeat.weekdays) <= set(range(1, 8)):
            raise errors.validation_error("repeat.weekdays", code="FIELD_REQUIRED")
    if repeat.ends_on is not None and repeat.ends_on < starts_on:
        raise errors.validation_error("repeat.ends_on", code="DATE_INVALID")


def _zone(company: Company, location_id: UUID | None) -> ZoneInfo:
    from c4rsys.tasks import services

    return ZoneInfo(services.zone_of(company, location_id))


def _due(series: TaskSeries, company: Company, on: date) -> datetime:
    return datetime.combine(
        on, series.due_time or time.max, tzinfo=_zone(company, series.location_id)
    )


def start(
    ctx: CommandContext,
    company: Company,
    t: Task,
    repeat: Repeat,
    *,
    first_on: date,
    due_time: time | None,
    checklist: list[str],
    tag_ids: list[UUID],
) -> TaskSeries:
    """The repetition of a task just created; the task is its first repetition."""
    _validate(repeat, first_on)
    series = TaskSeries.objects.create(
        company=company,
        frequency=repeat.frequency,
        weekdays=sorted(set(repeat.weekdays)),
        day_of_month=first_on.day if repeat.frequency == Frequency.MONTHLY else None,
        starts_on=first_on,
        ends_on=repeat.ends_on,
        title=t.title,
        description=t.description,
        priority=t.priority,
        location_id=t.location_id,
        assignee_id=t.assignee_id,
        object_type=t.object_type,
        object_id=t.object_id,
        due_time=due_time,
        checklist=checklist,
        checklist_required=t.checklist_required,
        group_id=t.group_id,
        tag_ids=[str(x) for x in tag_ids],
        created_by_user_id=ctx.actor.user_id,
        created_by_employment_id=ctx.actor.employment_id,
    )
    first = occurrence_from(series, first_on)
    if first is None:
        raise errors.validation_error("repeat.ends_on", code="DATE_INVALID")
    t.series = series
    t.series_on = first
    t.due_at = _due(series, company, first)
    t.due_has_time = due_time is not None
    t.save()
    series.next_on = occurrence_from(series, first + timedelta(days=1))
    series.save(update_fields=["next_on"])
    ctx.audit(
        "task.repeat_set",
        object_type="task",
        object_id=t.id,
        after={
            "frequency": series.frequency,
            "weekdays": series.weekdays,
            "ends_on": series.ends_on,
        },
    )
    return series


def _instance(series: TaskSeries, company: Company, on: date) -> Task:
    t = Task.objects.create(
        company=company,
        number=next_number(company.id, "task"),
        title=series.title,
        description=series.description,
        priority=series.priority,
        location_id=series.location_id,
        assignee_id=series.assignee_id,
        object_type=series.object_type,
        object_id=series.object_id,
        due_at=_due(series, company, on),
        due_has_time=series.due_time is not None,
        checklist_required=series.checklist_required,
        group_id=series.group_id,
        series=series,
        series_on=on,
        created_by_user_id=series.created_by_user_id,
        created_by_employment_id=series.created_by_employment_id,
        created_at=timezone.now(),
    )
    from c4rsys.tasks.models import TaskChecklistItem

    TaskChecklistItem.objects.bulk_create(
        TaskChecklistItem(
            company=company,
            task=t,
            position=i,
            text=text,
            updated_by_user_id=series.created_by_user_id,
        )
        for i, text in enumerate(series.checklist)
    )
    for tag_id in series.tag_ids:
        TaskTagLink.objects.get_or_create(company=company, task=t, tag_id=tag_id)
    from c4rsys.tasks import notices

    notices.sync(t, series.created_by_employment_id)
    return t


def run(now: datetime | None = None) -> int:
    """Makes the repetitions whose date has come in the zone of the task. Run on a schedule."""
    moment = now or timezone.now()
    made = 0
    for series_id in TaskSeries.objects.filter(
        state=TaskSeries.State.ACTIVE, next_on__isnull=False
    ).values_list("id", flat=True):
        with transaction.atomic():
            made += _run_one(series_id, moment)
    return made


def _run_one(series_id: UUID, moment: datetime) -> int:
    series = TaskSeries.objects.select_for_update().select_related("company").get(pk=series_id)
    if series.state != TaskSeries.State.ACTIVE or series.next_on is None:
        return 0
    company = series.company
    today = moment.astimezone(_zone(company, series.location_id)).date()
    if series.next_on > today:
        return 0
    # The nearest date not later than today; earlier missed ones are not made (§ 24.2.3).
    on = series.next_on
    while True:
        following = occurrence_from(series, on + timedelta(days=1))
        if following is None or following > today:
            break
        on = following
    made = 0
    if not Task.objects.filter(series=series, series_on=on).exists():
        t = _instance(series, company, on)
        AuditEvent.objects.create(
            company_id=company.id,
            system_process=PROCESS,
            action="task.created",
            object_type="task",
            object_id=str(t.id),
            after={"number": t.number, "series": str(series.id), "on": on.isoformat()},
        )
        made = 1
    series.next_on = occurrence_from(series, on + timedelta(days=1))
    series.save(update_fields=["next_on"])
    return made


def locked(company_id: UUID, series_id: UUID, expected: int | None) -> TaskSeries:
    found = (
        TaskSeries.objects.select_for_update().filter(company_id=company_id, pk=series_id).first()
    )
    if found is None:
        raise errors.not_found()
    if expected is not None and found.version != expected:
        raise errors.version_conflict()
    return found


def _manage(ctx: CommandContext, series: TaskSeries) -> None:
    from c4rsys.tasks import services

    if not services.has(ctx.actor, "tasks.manage", series.location_id):
        raise errors.permission_denied("tasks.manage")


def set_state(ctx: CommandContext, series: TaskSeries, state: str) -> TaskSeries:
    """Pause, resume or stop the repetition; the made tasks stay (§ 19.4.2)."""
    _manage(ctx, series)
    allowed = {
        TaskSeries.State.ACTIVE: (TaskSeries.State.PAUSED, TaskSeries.State.STOPPED),
        TaskSeries.State.PAUSED: (TaskSeries.State.ACTIVE, TaskSeries.State.STOPPED),
        TaskSeries.State.STOPPED: (),
    }
    if state not in allowed[TaskSeries.State(series.state)]:
        raise errors.business_rule("TASK_REPEAT_STATE_INVALID", state=series.state)
    before = series.state
    series.state = state
    if state == TaskSeries.State.ACTIVE:
        today = timezone.now().astimezone(_zone(series.company, series.location_id)).date()
        series.next_on = occurrence_from(series, today)
    series.version += 1
    series.save()
    ctx.audit(
        "task.repeat_state_changed",
        object_type="task_series",
        object_id=series.id,
        before={"state": before},
        after={"state": state},
    )
    return series


@dataclass(frozen=True)
class SeriesChanges:
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    due_time: time | None = None
    due_time_set: bool = False
    ends_on: date | None = None
    ends_on_set: bool = False
    checklist: list[str] | None = None
    checklist_required: bool | None = None


def change_following(
    ctx: CommandContext, company: Company, t: Task, changes: SeriesChanges
) -> TaskSeries:
    """«This and the following»: the series and this task, if it is still active; done tasks
    stay as they are (§ 19.4.2)."""
    from c4rsys.tasks.models import ACTIVE_STATES, TaskChecklistItem

    if t.series_id is None:
        raise errors.business_rule("TASK_NOT_REPEATING")
    series = locked(company.id, t.series_id, None)
    _manage(ctx, series)
    if changes.priority is not None and changes.priority not in Task.Priority.values:
        raise errors.validation_error("priority")
    before = _series_snapshot(series)
    for name in ("title", "description", "priority", "checklist", "checklist_required"):
        value = getattr(changes, name)
        if value is not None:
            setattr(
                series, name, value.strip() if isinstance(value, str) and name == "title" else value
            )
    if changes.due_time_set:
        series.due_time = changes.due_time
    if changes.ends_on_set:
        if changes.ends_on is not None and changes.ends_on < series.starts_on:
            raise errors.validation_error("ends_on", code="DATE_INVALID")
        series.ends_on = changes.ends_on
        if (
            series.next_on is not None
            and changes.ends_on is not None
            and series.next_on > changes.ends_on
        ):
            series.next_on = None
    if not series.title:
        raise errors.validation_error("title", code="FIELD_REQUIRED")
    after = _series_snapshot(series)
    if before == after:
        raise errors.business_rule("NOTHING_CHANGED")
    series.version += 1
    series.save()
    if t.state in ACTIVE_STATES:
        t.title = series.title
        t.description = series.description
        t.priority = series.priority
        t.checklist_required = series.checklist_required
        if t.series_on is not None:
            t.due_at = _due(series, company, t.series_on)
            t.due_has_time = series.due_time is not None
        if changes.checklist is not None:
            # A new composition; the marks of the items kept by their text stay.
            marks = {x.text: x for x in TaskChecklistItem.objects.filter(task=t)}
            TaskChecklistItem.objects.filter(task=t).exclude(text__in=series.checklist).delete()
            for i, text in enumerate(series.checklist):
                item = marks.get(text)
                if item is None:
                    TaskChecklistItem.objects.create(
                        company=company,
                        task=t,
                        position=i,
                        text=text,
                        updated_by_user_id=ctx.actor.user_id,
                    )
                elif item.position != i:
                    item.position = i
                    item.save(update_fields=["position"])
        t.version += 1
        t.save()
    ctx.audit(
        "task.repeat_changed",
        object_type="task",
        object_id=t.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return series


def _series_snapshot(series: TaskSeries) -> dict[str, Any]:
    return {
        "title": series.title,
        "description": series.description,
        "priority": series.priority,
        "due_time": series.due_time,
        "ends_on": series.ends_on,
        "checklist": list(series.checklist),
        "checklist_required": series.checklist_required,
    }


def payload(series: TaskSeries | None) -> dict[str, Any] | None:
    if series is None:
        return None
    return {
        "id": str(series.id),
        "version": series.version,
        "frequency": series.frequency,
        "weekdays": series.weekdays,
        "day_of_month": series.day_of_month,
        "starts_on": series.starts_on.isoformat(),
        "ends_on": series.ends_on.isoformat() if series.ends_on else None,
        "next_on": series.next_on.isoformat() if series.next_on else None,
        "state": series.state,
        "due_time": series.due_time.isoformat(timespec="minutes") if series.due_time else None,
        "checklist": series.checklist,
    }
