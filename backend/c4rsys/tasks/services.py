"""Work tasks: creation, queue, executor, states, deadline, comments (ТЗ § 19.1–19.3, § 23.9;
customer's decisions З-1…З-11 of 29.10.2026).

Rights (§ 23.9), each in the task's scope — the whole company or its location:
* «own tasks» opens the tasks assigned to the employee, «queue» — the open unassigned ones,
  «all tasks of the area» — the others, read only; «create» — manual creation and assigning to
  oneself; «manage» — assigning others, deadline and priority, return to the queue, cancelling
  after taking (§ 19.3.1).
* The executor changes the state of an own manual task (§ 19.2.1). A system task is done only by
  its required result (§ 19.2.1).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies import access, locations
from c4rsys.companies.models import Company, Employment
from c4rsys.files import services as files
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.tasks import extensions, notices, objects, series
from c4rsys.tasks.models import ACTIVE_STATES, Task, TaskComment, TaskLink, TaskPolicy

State = Task.State

# --- Rights and scope (§ 19.1.7, § 23.9) ---------------------------------------------------------


def has(actor: Actor, permission: str, location_id: UUID | None, resolver: Any = None) -> bool:
    resolver = resolver or access.RightsResolver()
    if location_id is None:
        return bool(resolver.has_permission(actor, permission, whole_company=True))
    return bool(resolver.has_permission(actor, permission, location_id=location_id))


def _mine(actor: Actor, t: Task) -> bool:
    return t.assignee_id is not None and t.assignee_id == actor.employment_id


def can_view(actor: Actor, t: Task, resolver: Any = None) -> bool:
    resolver = resolver or access.RightsResolver()
    if _mine(actor, t) and has(actor, "tasks.own", t.location_id, resolver):
        return True
    in_queue = t.assignee_id is None and t.state == State.OPEN
    if in_queue and has(actor, "tasks.queue", t.location_id, resolver):
        return True
    created = t.created_by_employment_id == actor.employment_id
    if created and has(actor, "tasks.create", t.location_id, resolver):
        return True
    return any(has(actor, p, t.location_id, resolver) for p in ("tasks.view_all", "tasks.manage"))


def _require(actor: Actor, permission: str, t: Task) -> None:
    if not has(actor, permission, t.location_id):
        raise errors.permission_denied(permission)


def policy_of(company_id: UUID) -> TaskPolicy:
    found = TaskPolicy.objects.filter(company_id=company_id).first()
    return found or TaskPolicy(company_id=company_id, version=0)


def set_policy(
    ctx: CommandContext,
    company: Company,
    may_release: bool,
    may_change_due: bool,
    expected: int,
    repeat_hours: int | None = None,
) -> TaskPolicy:
    found = TaskPolicy.objects.select_for_update().filter(company=company).first()
    if (found.version if found else 0) != expected:
        raise errors.version_conflict()
    before = (
        {"release": found.executor_may_release, "change_due": found.executor_may_change_due}
        if found
        else None
    )
    found = found or TaskPolicy(company=company, version=0)
    found.executor_may_release = may_release
    found.executor_may_change_due = may_change_due
    if repeat_hours is not None:
        if repeat_hours > 24 * 30:
            raise errors.validation_error("overdue_repeat_hours")
        found.overdue_repeat_hours = repeat_hours
    found.updated_by_user_id = ctx.actor.user_id
    found.updated_at = timezone.now()
    found.version += 1
    found.save()
    ctx.audit(
        "task.policy_changed",
        object_type="task_policy",
        object_id=company.id,
        before=before,
        after={
            "release": may_release,
            "change_due": may_change_due,
            "overdue_repeat_hours": found.overdue_repeat_hours,
        },
    )
    return found


# --- Deadline (§ 19.2.2) -------------------------------------------------------------------------


def zone_of(company: Company, location_id: UUID | None) -> str:
    if location_id is not None:
        info = locations.location_info(company.id, location_id)
        if info is not None and info.timezone:
            return info.timezone
    return company.timezone or "UTC"


@dataclass(frozen=True)
class Due:
    on: date
    at: time | None = None


def due_moment(company: Company, location_id: UUID | None, due: Due | None) -> datetime | None:
    """Without a time — the end of the chosen day in the zone of the task (§ 19.2.2)."""
    if due is None:
        return None
    zone = ZoneInfo(zone_of(company, location_id))
    return datetime.combine(due.on, due.at or time.max, tzinfo=zone)


def overdue(t: Task, now: datetime | None = None) -> bool:
    return t.state in ACTIVE_STATES and t.due_at is not None and (now or timezone.now()) > t.due_at


# --- Assignee ------------------------------------------------------------------------------------


def eligible(company_id: UUID, location_id: UUID | None, employment_id: UUID) -> bool:
    """An executor is an active employee with access to their own tasks in the scope (§ 19.1.6)."""
    e = Employment.objects.filter(
        company_id=company_id, pk=employment_id, state=Employment.State.ACTIVE
    ).first()
    if e is None or e.user_id is None:
        return False
    return has(
        Actor(user_id=e.user_id, company_id=company_id, employment_id=e.id),
        "tasks.own",
        location_id,
    )


def candidates(company_id: UUID, location_id: UUID | None) -> list[UUID]:
    resolver = access.RightsResolver()
    result = []
    for e in Employment.objects.filter(
        company_id=company_id, state=Employment.State.ACTIVE, user__isnull=False
    ):
        actor = Actor(user_id=e.user_id or e.id, company_id=company_id, employment_id=e.id)
        if has(actor, "tasks.own", location_id, resolver):
            result.append(e.id)
    return result


def _assignee(t: Task, employment_id: UUID) -> None:
    if not eligible(t.company_id, t.location_id, employment_id):
        raise errors.validation_error("assignee_id", code="TASK_ASSIGNEE_NO_ACCESS")


# --- Creation (§ 19.1.2, § 19.1.7–19.1.8) --------------------------------------------------------


@dataclass(frozen=True)
class Link:
    object_type: str
    object_id: UUID


@dataclass(frozen=True)
class NewTask:
    title: str
    description: str = ""
    priority: str = Task.Priority.NORMAL
    # "self", "queue" or an employment id as text (§ 19.1.2).
    assign: str = "self"
    location_id: UUID | None = None
    location_given: bool = False
    main: Link | None = None
    links: list[Link] = field(default_factory=list)
    due: Due | None = None
    # § 19.4: checklist, group, tags and repetition.
    checklist: list[str] = field(default_factory=list)
    checklist_required: bool = False
    group_id: UUID | None = None
    tag_ids: list[UUID] = field(default_factory=list)
    repeat: Any = None
    # Minutes before the deadline (decision З-4).
    reminders: list[int] = field(default_factory=list)


def snapshot(t: Task) -> dict[str, Any]:
    return {
        "number": t.number,
        "title": t.title,
        "priority": t.priority,
        "state": t.state,
        "state_note": t.state_note,
        "location": str(t.location_id) if t.location_id else None,
        "assignee": str(t.assignee_id) if t.assignee_id else None,
        "object": f"{t.object_type}:{t.object_id}" if t.object_type else None,
        "due_at": t.due_at,
    }


def _scope(company: Company, data: NewTask) -> UUID | None:
    """The scope is never wider than the main object's (§ 19.1.7); by default it is the object's."""
    place = None
    if data.main is not None:
        obj = objects.resolve(company.id, data.main.object_type, data.main.object_id)
        place = objects.place_of(data.main.object_type, obj)
    chosen = data.location_id if data.location_given else place
    if place is not None and chosen != place:
        raise errors.validation_error("location_id", code="TASK_SCOPE_WIDER_THAN_OBJECT")
    if chosen is not None and not locations.location_exists(company.id, chosen):
        raise errors.validation_error("location_id", code="LOCATION_UNKNOWN")
    return chosen


def create(ctx: CommandContext, company: Company, data: NewTask) -> Task:
    if data.priority not in Task.Priority.values:
        raise errors.validation_error("priority")
    location_id = _scope(company, data)
    actor = ctx.actor
    if not has(actor, "tasks.create", location_id):
        raise errors.permission_denied("tasks.create")
    for link in data.links:
        objects.resolve(company.id, link.object_type, link.object_id, "links")
    t = Task(
        company=company,
        number=next_number(company.id, "task"),
        title=data.title,
        description=data.description,
        priority=data.priority,
        location_id=location_id,
        object_type=data.main.object_type if data.main else "",
        object_id=data.main.object_id if data.main else None,
        due_at=due_moment(company, location_id, data.due),
        due_has_time=bool(data.due and data.due.at),
        created_by_user_id=actor.user_id,
        created_by_employment_id=actor.employment_id,
        created_at=timezone.now(),
        checklist_required=data.checklist_required,
        group=extensions.group_of(company.id, data.group_id),
    )
    extensions.tags_by_ids(company.id, data.tag_ids)
    if data.checklist_required and not data.checklist:
        raise errors.validation_error("checklist", code="FIELD_REQUIRED")
    if data.assign == "self":
        t.assignee_id = actor.employment_id
        if not eligible(company.id, location_id, actor.employment_id):  # type: ignore[arg-type]
            raise errors.validation_error("assign", code="TASK_ASSIGNEE_NO_ACCESS")
    elif data.assign != "queue":
        if not has(actor, "tasks.manage", location_id):
            raise errors.permission_denied("tasks.manage")
        try:
            target = UUID(data.assign)
        except ValueError as exc:
            raise errors.validation_error("assign") from exc
        _assignee(t, target)
        t.assignee_id = target
    t.save()
    for link in data.links:
        TaskLink.objects.get_or_create(
            company=company,
            task=t,
            object_type=link.object_type,
            object_id=link.object_id,
            defaults={"created_by_user_id": actor.user_id},
        )
    if data.reminders:
        if data.due is None:
            raise errors.validation_error("reminders", code="TASK_REMINDERS_NEED_DUE")
        t.reminders = data.reminders
        t.save(update_fields=["reminders"])
    extensions.fill_checklist(ctx, t, data.checklist)
    extensions.set_tags(ctx, t, data.tag_ids, audit=False)
    ctx.audit("task.created", object_type="task", object_id=t.id, after=snapshot(t))
    if data.repeat is not None:
        if data.due is None:
            raise errors.validation_error("due_date", code="TASK_REPEAT_NEEDS_DATE")
        series.start(
            ctx,
            company,
            t,
            data.repeat,
            first_on=data.due.on,
            due_time=data.due.at,
            checklist=[x.strip() for x in data.checklist],
            tag_ids=data.tag_ids,
        )
    notices.sync(t, actor.employment_id)
    return t


# --- Reading -------------------------------------------------------------------------------------


def tasks_of(company_id: UUID) -> QuerySet[Task]:
    return Task.objects.filter(company_id=company_id)


def linked_to(company_id: UUID, object_type: str, object_id: UUID) -> QuerySet[Task]:
    linked = TaskLink.objects.filter(
        company_id=company_id, object_type=object_type, object_id=object_id
    ).values("task_id")
    return tasks_of(company_id).filter(
        Q(object_type=object_type, object_id=object_id) | Q(pk__in=linked)
    )


def locked(company_id: UUID, task_id: UUID, expected_version: int | None) -> Task:
    t = Task.objects.select_for_update().filter(company_id=company_id, pk=task_id).first()
    if t is None:
        raise errors.not_found()
    if expected_version is not None and t.version != expected_version:
        raise errors.version_conflict()
    return t


def links_of(t: Task) -> list[TaskLink]:
    return list(TaskLink.objects.filter(task=t).order_by("created_at"))


def comments_of(t: Task) -> list[TaskComment]:
    return list(TaskComment.objects.filter(task=t).order_by("created_at", "id"))


# --- Changes of state (§ 19.1.4–19.1.6, § 19.2.1) ------------------------------------------------


def _active(t: Task) -> None:
    if t.state not in ACTIVE_STATES:
        raise errors.business_rule("TASK_CLOSED")


def _save(
    ctx: CommandContext, t: Task, action: str, before: dict[str, Any], reason: str = ""
) -> Task:
    t.version += 1
    t.save()
    notices.sync(t, ctx.actor.employment_id)
    after = snapshot(t)
    ctx.audit(
        action,
        object_type="task",
        object_id=t.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
        reason=reason,
    )
    return t


def executor_or_manager(ctx: CommandContext, t: Task) -> None:
    """The executor changes the state of an own task; another employee needs «manage»."""
    if _mine(ctx.actor, t) and has(ctx.actor, "tasks.own", t.location_id):
        return
    _require(ctx.actor, "tasks.manage", t)


def take(ctx: CommandContext, t: Task) -> Task:
    """An open task of the queue: the employee becomes its executor; it is in work (§ 19.1.5)."""
    _active(t)
    if t.assignee_id is not None or t.state != State.OPEN:
        raise errors.business_rule("TASK_NOT_IN_QUEUE")
    _require(ctx.actor, "tasks.queue", t)
    if not eligible(t.company_id, t.location_id, ctx.actor.employment_id):  # type: ignore[arg-type]
        raise errors.permission_denied("tasks.own")
    _blocked(t)
    before = snapshot(t)
    t.assignee_id = ctx.actor.employment_id
    t.state = State.IN_PROGRESS
    return _save(ctx, t, "task.taken", before)


def _blocked(t: Task) -> None:
    """Hook for the dependencies of part З2: a task waiting for its predecessors does not start."""
    if extensions.blocked(t):
        raise errors.business_rule("TASK_BLOCKED_BY_DEPENDENCY")


def start(ctx: CommandContext, t: Task) -> Task:
    _active(t)
    if t.state != State.OPEN or t.assignee_id is None:
        raise errors.business_rule("TASK_STATE_INVALID", state=t.state)
    executor_or_manager(ctx, t)
    _blocked(t)
    before = snapshot(t)
    t.state = State.IN_PROGRESS
    return _save(ctx, t, "task.started", before)


def hold(ctx: CommandContext, t: Task, state: str, note: str) -> Task:
    """«Awaiting an answer» with what it waits for, or «paused» with a reason (§ 19.1.4)."""
    _active(t)
    if state not in (State.WAITING, State.PAUSED):
        raise errors.validation_error("state")
    if not note.strip():
        raise errors.validation_error("note", code="FIELD_REQUIRED")
    if t.state != State.IN_PROGRESS:
        raise errors.business_rule("TASK_STATE_INVALID", state=t.state)
    executor_or_manager(ctx, t)
    before = snapshot(t)
    t.state = state
    t.state_note = note.strip()
    return _save(ctx, t, "task.waiting" if state == State.WAITING else "task.paused", before)


def resume(ctx: CommandContext, t: Task) -> Task:
    _active(t)
    if t.state not in (State.WAITING, State.PAUSED):
        raise errors.business_rule("TASK_STATE_INVALID", state=t.state)
    executor_or_manager(ctx, t)
    before = snapshot(t)
    t.state = State.IN_PROGRESS
    t.state_note = ""
    return _save(ctx, t, "task.resumed", before)


def complete(ctx: CommandContext, t: Task) -> Task:
    """A manual task is done by its executor or another employee with the rights (§ 19.2.1)."""
    _active(t)
    if t.source == Task.Source.SYSTEM:
        raise errors.business_rule("TASK_SYSTEM_RESULT_REQUIRED")
    executor_or_manager(ctx, t)
    _blocked(t)
    extensions.check_completion(t)
    before = snapshot(t)
    t.state = State.DONE
    t.state_note = ""
    t.closed_at = timezone.now()
    t.closed_by_user_id = ctx.actor.user_id
    return _save(ctx, t, "task.completed", before)


def cancel(ctx: CommandContext, t: Task, reason: str) -> Task:
    """The creator cancels before the task is taken in work; afterwards — «manage» with a
    reason (§ 19.2.1)."""
    _active(t)
    taken = t.state != State.OPEN
    creator = t.created_by_employment_id == ctx.actor.employment_id
    if taken or not creator:
        _require(ctx.actor, "tasks.manage", t)
        if not reason.strip():
            raise errors.validation_error("reason", code="FIELD_REQUIRED")
    before = snapshot(t)
    t.state = State.CANCELLED
    t.state_note = ""
    t.cancel_reason = reason.strip()
    t.closed_at = timezone.now()
    t.closed_by_user_id = ctx.actor.user_id
    return _save(ctx, t, "task.cancelled", before, reason=reason.strip())


def assign(ctx: CommandContext, t: Task, employment_id: UUID) -> Task:
    """Assigning or reassigning another employee needs «manage» (§ 19.1.6, § 23.9.3)."""
    _active(t)
    _require(ctx.actor, "tasks.manage", t)
    if t.assignee_id == employment_id:
        raise errors.business_rule("NOTHING_CHANGED")
    _assignee(t, employment_id)
    before = snapshot(t)
    t.assignee_id = employment_id
    return _save(
        ctx, t, "task.assigned" if before["assignee"] is None else "task.reassigned", before
    )


def release(ctx: CommandContext, t: Task) -> Task:
    """Back to the common queue: by the executor if the company allows it, or by «manage»."""
    _active(t)
    if t.assignee_id is None:
        raise errors.business_rule("TASK_NOT_ASSIGNED")
    if not (_mine(ctx.actor, t) and policy_of(t.company_id).executor_may_release):
        _require(ctx.actor, "tasks.manage", t)
    before = snapshot(t)
    t.assignee_id = None
    t.state = State.OPEN
    t.state_note = ""
    return _save(ctx, t, "task.released", before)


@dataclass(frozen=True)
class Changes:
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    due: Due | None = None
    due_set: bool = False
    reminders: list[int] | None = None


def check_reminders(values: list[int]) -> list[int]:
    """Up to 5 reminders, from a minute to 30 days before the deadline (decision З-4)."""
    if len(values) > notices.MAX_REMINDERS or any(v < 1 or v > 43200 for v in values):
        raise errors.validation_error("reminders")
    return sorted(set(values), reverse=True)


def change(ctx: CommandContext, company: Company, t: Task, changes: Changes) -> Task:
    """Title and description — the creator or «manage»; priority — «manage»; the deadline —
    «manage», or the executor when the company allows it (§ 19.2.2, § 23.9.1). The former and
    the new deadline stay in the history."""
    _active(t)
    actor = ctx.actor
    manager = has(actor, "tasks.manage", t.location_id)
    if changes.title is not None or changes.description is not None:
        if t.created_by_employment_id != actor.employment_id and not manager:
            raise errors.permission_denied("tasks.manage")
    if changes.priority is not None:
        if not manager:
            raise errors.permission_denied("tasks.manage")
        if changes.priority not in Task.Priority.values:
            raise errors.validation_error("priority")
    if changes.due_set and not manager:
        if not (_mine(actor, t) and policy_of(t.company_id).executor_may_change_due):
            raise errors.permission_denied("tasks.manage")
    before = snapshot(t)
    description_before = t.description
    if changes.title is not None:
        if not changes.title.strip():
            raise errors.validation_error("title", code="FIELD_REQUIRED")
        t.title = changes.title.strip()
    if changes.description is not None:
        t.description = changes.description
    if changes.priority is not None:
        t.priority = changes.priority
    if changes.due_set:
        t.due_at = due_moment(company, t.location_id, changes.due)
        t.due_has_time = bool(changes.due and changes.due.at)
        t.reminders_sent = []
    reminders_before = list(t.reminders)
    if changes.reminders is not None:
        if not (_mine(actor, t) or manager or t.created_by_employment_id == actor.employment_id):
            raise errors.permission_denied("tasks.manage")
        t.reminders = check_reminders(changes.reminders)
        t.reminders_sent = []
    if (
        snapshot(t) == before
        and t.description == description_before
        and t.reminders == reminders_before
    ):
        raise errors.business_rule("NOTHING_CHANGED")
    return _save(ctx, t, "task.changed", before)


def add_link(ctx: CommandContext, t: Task, link: Link) -> TaskLink:
    _active(t)
    if t.created_by_employment_id != ctx.actor.employment_id:
        executor_or_manager(ctx, t)
    objects.resolve(t.company_id, link.object_type, link.object_id, "object")
    item, created = TaskLink.objects.get_or_create(
        company_id=t.company_id,
        task=t,
        object_type=link.object_type,
        object_id=link.object_id,
        defaults={"created_by_user_id": ctx.actor.user_id},
    )
    if not created:
        raise errors.business_rule("TASK_ALREADY_LINKED")
    t.version += 1
    t.save(update_fields=["version", "updated_at"])
    ctx.audit(
        "task.linked",
        object_type="task",
        object_id=t.id,
        after={"object_type": link.object_type, "object_id": str(link.object_id)},
    )
    return item


def comment(
    ctx: CommandContext, t: Task, text: str, file_id: UUID | None, file_name: str
) -> TaskComment:
    """Anyone who sees the task comments it; a file is attached by its uploader (decision З-10)."""
    if not text.strip() and file_id is None:
        raise errors.validation_error("text", code="FIELD_REQUIRED")
    if file_id is not None:
        files.usable(
            file_id,
            purpose=files.TASK_ATTACHMENT,
            user_id=ctx.actor.user_id,
            company_id=t.company_id,
        )
        files.attach_to_company(file_id, t.company_id)
    item = TaskComment.objects.create(
        company_id=t.company_id,
        task=t,
        text=text.strip(),
        file_id=file_id,
        file_name=file_name,
        author_user_id=ctx.actor.user_id,
    )
    t.version += 1
    t.save(update_fields=["version", "updated_at"])
    ctx.audit(
        "task.commented",
        object_type="task",
        object_id=t.id,
        after={"comment": str(item.id), "file": str(file_id) if file_id else None},
    )
    return item


def file_reader(user_id: UUID, item: Any) -> bool:
    """An attachment is read by whoever may view its task (decision З-10)."""
    found = TaskComment.objects.select_related("task").filter(file_id=item.id).first()
    if found is None:
        return False
    t = found.task
    employment = Employment.objects.filter(
        company_id=t.company_id, user_id=user_id, state=Employment.State.ACTIVE
    ).first()
    if employment is None:
        return False
    return can_view(Actor(user_id=user_id, company_id=t.company_id, employment_id=employment.id), t)


# --- System tasks (§ 19.1.8–19.1.9, § 19.2.1) ----------------------------------------------------

SYSTEM_PROCESS = "task_schedule"


def ensure_system(
    company_id: UUID,
    *,
    event: str,
    condition: str,
    location_id: UUID | None,
    object_type: str,
    object_id: UUID,
    params: dict[str, Any],
    priority: str = Task.Priority.HIGH,
) -> Task | None:
    """A system task of an event, once per condition, in the common queue of its scope. The
    title is the event; the interface shows it in the user's language."""
    from c4rsys.platform.models import AuditEvent

    found = tasks_of(company_id).filter(
        source=Task.Source.SYSTEM, system_condition=condition, state__in=ACTIVE_STATES
    )
    if found.exists():
        return None
    t = Task.objects.create(
        company_id=company_id,
        number=next_number(company_id, "task"),
        title=event,
        priority=priority,
        location_id=location_id,
        source=Task.Source.SYSTEM,
        system_event=event,
        system_condition=condition,
        object_type=object_type,
        object_id=object_id,
        created_at=timezone.now(),
    )
    AuditEvent.objects.create(
        company_id=company_id,
        system_process=SYSTEM_PROCESS,
        action="task.created",
        object_type="task",
        object_id=str(t.id),
        after={**snapshot(t), "event": event, "params": params},
    )
    return t


def close_system(company_id: UUID, condition: str, *, done: bool, result: str) -> int:
    """The required result is recorded (done) or the condition is gone (cancelled); the result
    is kept (§ 19.2.1)."""
    from c4rsys.platform.models import AuditEvent

    closed = 0
    for t in Task.objects.select_for_update().filter(
        company_id=company_id,
        source=Task.Source.SYSTEM,
        system_condition=condition,
        state__in=ACTIVE_STATES,
    ):
        before = snapshot(t)
        t.state = State.DONE if done else State.CANCELLED
        t.state_note = result
        t.cancel_reason = "" if done else result
        t.closed_at = timezone.now()
        t.version += 1
        t.save()
        notices.sync(t, None)
        AuditEvent.objects.create(
            company_id=company_id,
            system_process=SYSTEM_PROCESS,
            action="task.completed" if done else "task.cancelled",
            object_type="task",
            object_id=str(t.id),
            before=before,
            after={**snapshot(t), "result": result},
        )
        closed += 1
    return closed
