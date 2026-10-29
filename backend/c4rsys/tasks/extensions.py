"""Extensions of tasks (ТЗ § 19.4, § 23.9.1, § 23.9.5): checklists, groups, tags and dependencies.

* The checklist is set when the task is created; later its composition is changed with
  «manage»; the executor marks items (§ 23.9.1, § 23.9.5). With «all items for completion» the
  task is not done while an item is open (§ 19.4.4).
* Groups and tags are created, renamed and removed with «manage»; existing ones are chosen when a
  task is created or edited (§ 23.9.5). Removing a group or a tag keeps the tasks (§ 19.4.5,
  § 19.4.7).
* «Start after completion»: until every predecessor is done the task neither starts nor is done;
  a cancelled predecessor is not done (§ 19.4.6). No self-dependencies or cycles.
"""

from typing import Any
from uuid import UUID

from django.db.models import Max
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.tasks.models import (
    Task,
    TaskChecklistItem,
    TaskDependency,
    TaskGroup,
    TaskTag,
    TaskTagLink,
)

MAX_ITEMS = 100


# --- Dependencies (§ 19.4.6) ---------------------------------------------------------------------


def predecessors_of(t: Task) -> list[Task]:
    return [
        x.predecessor
        for x in TaskDependency.objects.filter(task=t)
        .select_related("predecessor")
        .order_by("created_at")
    ]


def blocked(t: Task) -> bool:
    """«Blocked by a dependency»: a required predecessor is not done (§ 19.1.4, § 19.4.6)."""
    return (
        TaskDependency.objects.filter(task=t).exclude(predecessor__state=Task.State.DONE).exists()
    )


def _reaches(start: UUID, target: UUID) -> bool:
    """Whether `target` is among the predecessors of `start`, at any depth."""
    seen: set[UUID] = set()
    queue = [start]
    while queue:
        current = queue.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        queue.extend(
            TaskDependency.objects.filter(task_id=current).values_list("predecessor_id", flat=True)
        )
    return False


def _manage(ctx: CommandContext, t: Task) -> None:
    from c4rsys.tasks import services

    if not services.has(ctx.actor, "tasks.manage", t.location_id):
        raise errors.permission_denied("tasks.manage")


def add_dependency(ctx: CommandContext, t: Task, predecessor_id: UUID) -> None:
    from c4rsys.tasks import services

    _manage(ctx, t)
    if predecessor_id == t.id:
        raise errors.business_rule("TASK_DEPENDENCY_CYCLE")
    predecessor = services.tasks_of(t.company_id).filter(pk=predecessor_id).first()
    if predecessor is None or not services.can_view(ctx.actor, predecessor):
        raise errors.validation_error("predecessor_id", code="TASK_OBJECT_UNKNOWN")
    if _reaches(predecessor_id, t.id):
        raise errors.business_rule("TASK_DEPENDENCY_CYCLE")
    _, created = TaskDependency.objects.get_or_create(
        company_id=t.company_id,
        task=t,
        predecessor=predecessor,
        defaults={"created_by_user_id": ctx.actor.user_id},
    )
    if not created:
        raise errors.business_rule("TASK_DEPENDENCY_EXISTS")
    _touch(ctx, t, "task.dependency_added", after={"predecessor": predecessor.number})


def remove_dependency(ctx: CommandContext, t: Task, predecessor_id: UUID) -> None:
    _manage(ctx, t)
    found = TaskDependency.objects.filter(task=t, predecessor_id=predecessor_id).first()
    if found is None:
        raise errors.not_found()
    number = found.predecessor.number
    found.delete()
    _touch(ctx, t, "task.dependency_removed", before={"predecessor": number})


# --- Checklist (§ 19.4.4) ------------------------------------------------------------------------


def checklist_of(t: Task) -> list[TaskChecklistItem]:
    return list(TaskChecklistItem.objects.filter(task=t).order_by("position", "id"))


def check_completion(t: Task) -> None:
    if t.checklist_required and TaskChecklistItem.objects.filter(task=t, done=False).exists():
        raise errors.business_rule("TASK_CHECKLIST_NOT_DONE")


def _text(text: str) -> str:
    if not text.strip():
        raise errors.validation_error("text", code="FIELD_REQUIRED")
    return text.strip()[:500]


def fill_checklist(ctx: CommandContext, t: Task, texts: list[str]) -> None:
    """The checklist given at the creation of a task or of a repetition (§ 19.4.4)."""
    if len(texts) > MAX_ITEMS:
        raise errors.validation_error("checklist")
    TaskChecklistItem.objects.bulk_create(
        TaskChecklistItem(
            company_id=t.company_id,
            task=t,
            position=index,
            text=_text(text),
            updated_by_user_id=ctx.actor.user_id,
        )
        for index, text in enumerate(texts)
    )


def _item(t: Task, item_id: UUID) -> TaskChecklistItem:
    found = TaskChecklistItem.objects.select_for_update().filter(task=t, pk=item_id).first()
    if found is None:
        raise errors.not_found()
    return found


def _open(t: Task) -> None:
    from c4rsys.tasks.models import ACTIVE_STATES

    if t.state not in ACTIVE_STATES:
        raise errors.business_rule("TASK_CLOSED")


def add_item(ctx: CommandContext, t: Task, text: str) -> None:
    _open(t)
    _manage(ctx, t)
    if TaskChecklistItem.objects.filter(task=t).count() >= MAX_ITEMS:
        raise errors.validation_error("text")
    last = TaskChecklistItem.objects.filter(task=t).aggregate(m=Max("position"))["m"]
    TaskChecklistItem.objects.create(
        company_id=t.company_id,
        task=t,
        position=(last + 1) if last is not None else 0,
        text=_text(text),
        updated_by_user_id=ctx.actor.user_id,
    )
    _touch(ctx, t, "task.checklist_added", after={"text": text.strip()})


def rename_item(ctx: CommandContext, t: Task, item_id: UUID, text: str) -> None:
    _open(t)
    _manage(ctx, t)
    item = _item(t, item_id)
    before = item.text
    item.text = _text(text)
    item.updated_by_user_id = ctx.actor.user_id
    item.updated_at = timezone.now()
    item.save()
    _touch(ctx, t, "task.checklist_renamed", before={"text": before}, after={"text": item.text})


def move_item(ctx: CommandContext, t: Task, item_id: UUID, position: int) -> None:
    _open(t)
    _manage(ctx, t)
    items = checklist_of(t)
    moving = next((x for x in items if x.id == item_id), None)
    if moving is None:
        raise errors.not_found()
    if position < 0 or position >= len(items):
        raise errors.validation_error("position")
    items.remove(moving)
    items.insert(position, moving)
    for index, x in enumerate(items):
        if x.position != index:
            x.position = index
            x.save(update_fields=["position"])
    _touch(ctx, t, "task.checklist_moved", after={"text": moving.text, "position": position})


def remove_item(ctx: CommandContext, t: Task, item_id: UUID) -> None:
    _open(t)
    _manage(ctx, t)
    item = _item(t, item_id)
    text = item.text
    item.delete()
    _touch(ctx, t, "task.checklist_removed", before={"text": text})


def mark_item(ctx: CommandContext, t: Task, item_id: UUID, done: bool) -> None:
    """The executor or «manage» marks an item; who and when is kept (§ 19.4.4, § 23.9.5)."""
    from c4rsys.tasks import services

    _open(t)
    services.executor_or_manager(ctx, t)
    item = _item(t, item_id)
    if item.done == done:
        raise errors.business_rule("NOTHING_CHANGED")
    now = timezone.now()
    item.done = done
    item.done_at = now if done else None
    item.done_by_user_id = ctx.actor.user_id if done else None
    item.updated_at = now
    item.updated_by_user_id = ctx.actor.user_id
    item.save()
    _touch(
        ctx,
        t,
        "task.checklist_marked" if done else "task.checklist_unmarked",
        after={"text": item.text},
    )


def _touch(
    ctx: CommandContext,
    t: Task,
    action: str,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    t.version += 1
    t.save(update_fields=["version", "updated_at"])
    ctx.audit(action, object_type="task", object_id=t.id, before=before, after=after)


# --- Groups and tags (§ 19.4.5, § 19.4.7) --------------------------------------------------------


def groups_of(company_id: UUID) -> list[TaskGroup]:
    return list(TaskGroup.objects.filter(company_id=company_id).order_by("name"))


def tags_of(company_id: UUID) -> list[TaskTag]:
    return list(TaskTag.objects.filter(company_id=company_id).order_by("name"))


def group_of(company_id: UUID, group_id: UUID | None) -> TaskGroup | None:
    if group_id is None:
        return None
    found = TaskGroup.objects.filter(company_id=company_id, pk=group_id).first()
    if found is None:
        raise errors.validation_error("group_id", code="TASK_GROUP_UNKNOWN")
    return found


def tags_by_ids(company_id: UUID, tag_ids: list[UUID]) -> list[TaskTag]:
    found = list(TaskTag.objects.filter(company_id=company_id, pk__in=tag_ids))
    if len(found) != len(set(tag_ids)):
        raise errors.validation_error("tag_ids", code="TASK_TAG_UNKNOWN")
    return found


def tag_names(t: Task) -> list[dict[str, str]]:
    return [
        {"id": str(x.tag_id), "name": x.tag.name}
        for x in TaskTagLink.objects.filter(task=t).select_related("tag").order_by("tag__name")
    ]


def set_tags(ctx: CommandContext, t: Task, tag_ids: list[UUID], *, audit: bool = True) -> None:
    tags = tags_by_ids(t.company_id, tag_ids)
    before = sorted(x["name"] for x in tag_names(t))
    TaskTagLink.objects.filter(task=t).exclude(tag_id__in=tag_ids).delete()
    for tag in tags:
        TaskTagLink.objects.get_or_create(company_id=t.company_id, task=t, tag=tag)
    after = sorted(x.name for x in tags)
    if audit:
        if before == after:
            raise errors.business_rule("NOTHING_CHANGED")
        _touch(ctx, t, "task.tags_changed", before={"tags": before}, after={"tags": after})


def set_group(ctx: CommandContext, t: Task, group_id: UUID | None) -> None:
    group = group_of(t.company_id, group_id)
    if t.group_id == group_id:
        raise errors.business_rule("NOTHING_CHANGED")
    before = t.group.name if t.group else None
    t.group = group
    t.save(update_fields=["group", "updated_at"])
    _touch(
        ctx,
        t,
        "task.group_changed",
        before={"group": before},
        after={"group": group.name if group else None},
    )


def _require_manage_anywhere(ctx: CommandContext) -> None:
    from c4rsys.companies import access

    if not access.RightsResolver().has_permission(ctx.actor, "tasks.manage"):
        raise errors.permission_denied("tasks.manage")


def save_group(
    ctx: CommandContext, company: Company, group_id: UUID | None, name: str, description: str
) -> TaskGroup:
    _require_manage_anywhere(ctx)
    if not name.strip():
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    if group_id is None:
        group = TaskGroup.objects.create(
            company=company,
            name=name.strip(),
            description=description,
            created_by_user_id=ctx.actor.user_id,
        )
        ctx.audit(
            "task_group.created",
            object_type="task_group",
            object_id=group.id,
            after={"name": group.name},
        )
        return group
    found = TaskGroup.objects.select_for_update().filter(company=company, pk=group_id).first()
    if found is None:
        raise errors.not_found()
    group = found
    before = {"name": group.name, "description": group.description}
    group.name = name.strip()
    group.description = description
    group.version += 1
    group.save()
    ctx.audit(
        "task_group.changed",
        object_type="task_group",
        object_id=group.id,
        before=before,
        after={"name": group.name, "description": group.description},
    )
    return group


def remove_group(ctx: CommandContext, company: Company, group_id: UUID) -> None:
    """The tasks stay, without the group (§ 19.4.5)."""
    from c4rsys.tasks.models import TaskSeries

    _require_manage_anywhere(ctx)
    group = TaskGroup.objects.select_for_update().filter(company=company, pk=group_id).first()
    if group is None:
        raise errors.not_found()
    Task.objects.filter(group=group).update(group=None)
    TaskSeries.objects.filter(group=group).update(group=None)
    name = group.name
    group.delete()
    ctx.audit(
        "task_group.removed", object_type="task_group", object_id=group_id, before={"name": name}
    )


def save_tag(ctx: CommandContext, company: Company, tag_id: UUID | None, name: str) -> TaskTag:
    """A renamed tag keeps its links to the tasks (§ 19.4.7)."""
    _require_manage_anywhere(ctx)
    name = name.strip()
    if not name:
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    clash = TaskTag.objects.filter(company=company, name=name)
    if tag_id is not None:
        clash = clash.exclude(pk=tag_id)
    if clash.exists():
        raise errors.business_rule("TASK_TAG_EXISTS")
    if tag_id is None:
        tag = TaskTag.objects.create(
            company=company, name=name, created_by_user_id=ctx.actor.user_id
        )
        ctx.audit(
            "task_tag.created", object_type="task_tag", object_id=tag.id, after={"name": name}
        )
        return tag
    found = TaskTag.objects.select_for_update().filter(company=company, pk=tag_id).first()
    if found is None:
        raise errors.not_found()
    tag = found
    before = tag.name
    tag.name = name
    tag.save()
    ctx.audit(
        "task_tag.renamed",
        object_type="task_tag",
        object_id=tag.id,
        before={"name": before},
        after={"name": name},
    )
    return tag


def remove_tag(ctx: CommandContext, company: Company, tag_id: UUID) -> None:
    """The tag is taken off the tasks; the tasks stay (§ 19.4.7)."""
    from c4rsys.tasks.models import TaskSeries

    _require_manage_anywhere(ctx)
    tag = TaskTag.objects.select_for_update().filter(company=company, pk=tag_id).first()
    if tag is None:
        raise errors.not_found()
    TaskTagLink.objects.filter(tag=tag).delete()
    for series in TaskSeries.objects.filter(company=company):
        if str(tag_id) in series.tag_ids:
            series.tag_ids = [x for x in series.tag_ids if x != str(tag_id)]
            series.save(update_fields=["tag_ids"])
    name = tag.name
    tag.delete()
    ctx.audit("task_tag.removed", object_type="task_tag", object_id=tag_id, before={"name": name})
