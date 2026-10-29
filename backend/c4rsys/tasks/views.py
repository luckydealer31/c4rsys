"""HTTP API of work tasks (ТЗ гл. 19, § 23.9; decisions З-1…З-11).

Each task is shown only to an employee who may see it by § 23.9; a link from a card never opens
a task by itself (§ 19.1.7).
"""

from collections.abc import Callable
from datetime import date, time
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, history, staff
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.tasks import extensions, objects, series, services
from c4rsys.tasks.models import ACTIVE_STATES, Task

LIST_LIMIT = 300


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _uuid(value: Any, key: str) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _required(data: dict[str, Any], key: str) -> UUID:
    value = _uuid(data.get(key), key)
    if value is None:
        raise errors.validation_error(key, code="FIELD_REQUIRED")
    return value


def _link(data: Any, key: str) -> services.Link | None:
    if data in (None, ""):
        return None
    if not isinstance(data, dict) or data.get("object_type") not in objects.TYPES:
        raise errors.validation_error(key)
    object_id = _uuid(data.get("object_id"), key)
    if object_id is None:
        raise errors.validation_error(key, code="FIELD_REQUIRED")
    return services.Link(object_type=data["object_type"], object_id=object_id)


def _due(data: dict[str, Any]) -> services.Due | None:
    """`due_date` (YYYY-MM-DD) and optional `due_time` (HH:MM); no date — no deadline."""
    value = data.get("due_date")
    if value in (None, ""):
        return None
    try:
        on = date.fromisoformat(str(value))
        raw = data.get("due_time")
        at = time.fromisoformat(str(raw)) if raw not in (None, "") else None
    except ValueError as exc:
        raise errors.validation_error("due_date", code="DATE_INVALID") from exc
    return services.Due(on=on, at=at)


def _minutes(value: Any) -> list[int]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(
        isinstance(x, int) and not isinstance(x, bool) for x in value
    ):
        raise errors.validation_error("reminders")
    return value


def _texts(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise errors.validation_error("checklist")
    return [x for x in value if x.strip()]


def _ids(value: Any, key: str) -> list[UUID]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise errors.validation_error(key)
    return [UUID(str(_uuid(x, key))) for x in value]


def _repeat(value: Any) -> series.Repeat | None:
    """`{frequency: daily | weekly | monthly, weekdays: [1..7], ends_on}` (§ 19.4.1)."""
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise errors.validation_error("repeat")
    weekdays = value.get("weekdays") or []
    if not isinstance(weekdays, list) or not all(
        isinstance(x, int) and not isinstance(x, bool) for x in weekdays
    ):
        raise errors.validation_error("repeat.weekdays")
    ends = value.get("ends_on")
    try:
        ends_on = date.fromisoformat(str(ends)) if ends not in (None, "") else None
    except ValueError as exc:
        raise errors.validation_error("repeat.ends_on", code="DATE_INVALID") from exc
    return series.Repeat(
        frequency=str(value.get("frequency", "")), weekdays=weekdays, ends_on=ends_on
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _payload(
    t: Task, actor: Actor, names: dict[UUID, str], *, detail: bool = False
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": str(t.id),
        "version": t.version,
        "number": t.number,
        "title": t.title,
        "priority": t.priority,
        "state": t.state,
        "state_note": t.state_note,
        "location_id": str(t.location_id) if t.location_id else None,
        "assignee_id": str(t.assignee_id) if t.assignee_id else None,
        "assignee_name": names.get(t.assignee_id) if t.assignee_id else None,
        "is_mine": t.assignee_id is not None and t.assignee_id == actor.employment_id,
        "source": t.source,
        "object_type": t.object_type or None,
        "object_id": str(t.object_id) if t.object_id else None,
        "due_at": _iso(t.due_at),
        "due_has_time": t.due_has_time,
        "overdue": services.overdue(t),
        "created_at": t.created_at.isoformat(),
        "closed_at": _iso(t.closed_at),
        "blocked": t.state in ACTIVE_STATES and extensions.blocked(t),
        "group_id": str(t.group_id) if t.group_id else None,
        "tags": extensions.tag_names(t),
        "repeating": t.series_id is not None,
    }
    if detail:
        result.update(
            {
                "reminders": t.reminders,
                "checklist_required": t.checklist_required,
                "checklist": [
                    {
                        "id": str(x.id),
                        "text": x.text,
                        "done": x.done,
                        "done_at": _iso(x.done_at),
                    }
                    for x in extensions.checklist_of(t)
                ],
                "predecessors": [
                    {"id": str(x.id), "number": x.number, "title": x.title, "state": x.state}
                    for x in extensions.predecessors_of(t)
                ],
                "series": series.payload(t.series),
                "series_on": _iso(t.series_on),
                "description": t.description,
                "system_event": t.system_event or None,
                "system_condition": t.system_condition,
                "cancel_reason": t.cancel_reason,
                "created_by_name": names.get(t.created_by_employment_id)
                if t.created_by_employment_id
                else None,
                "is_creator": t.created_by_employment_id == actor.employment_id,
                "links": [
                    {"id": str(x.id), "object_type": x.object_type, "object_id": str(x.object_id)}
                    for x in services.links_of(t)
                ],
                "comments": _comments(t),
                "history": history.payload(
                    history.object_history(t.company_id, [("task", str(t.id))])
                ),
                "can": {
                    p.replace("tasks.", ""): services.has(actor, p, t.location_id)
                    for p in ("tasks.manage", "tasks.own", "tasks.queue")
                },
                "policy": _policy_payload(services.policy_of(t.company_id)),
            }
        )
    return result


def _comments(t: Task) -> list[dict[str, Any]]:
    from c4rsys.accounts import services as accounts

    items = services.comments_of(t)
    authors = accounts.names({c.author_user_id for c in items})
    return [
        {
            "id": str(c.id),
            "text": c.text,
            "file_id": str(c.file_id) if c.file_id else None,
            "file_name": c.file_name,
            "author": authors.get(c.author_user_id, ""),
            "created_at": c.created_at.isoformat(),
        }
        for c in items
    ]


def _names(company_id: UUID, items: list[Task]) -> dict[UUID, str]:
    ids = {x for t in items for x in (t.assignee_id, t.created_by_employment_id) if x}
    return staff.employee_names(company_id, ids) if ids else {}


def _policy_payload(p: Any) -> dict[str, Any]:
    return {
        "executor_may_release": p.executor_may_release,
        "executor_may_change_due": p.executor_may_change_due,
        "overdue_repeat_hours": p.overdue_repeat_hours,
        "version": p.version,
    }


def _visible(context: companies.CompanyContext, actor: Actor, task_id: UUID) -> Task:
    t = services.tasks_of(context.company.id).filter(pk=task_id).first()
    if t is None or not services.can_view(actor, t):
        raise errors.not_found()
    return t


@require_http_methods(["GET", "POST"])
def task_list(request: HttpRequest, cmp: str) -> JsonResponse:
    """GET: `view` mine | queue | all | created, `object_type` + `object_id`, `closed=1`,
    `overdue=1`; POST: a new task (§ 19.1.2)."""
    context, actor = _actor(request, cmp)
    company = context.company
    if request.method == "GET":
        params = request.GET
        query = services.tasks_of(company.id)
        if params.get("object_type") and params.get("object_id"):
            object_id = UUID(str(_uuid(params["object_id"], "object_id")))
            query = services.linked_to(company.id, params["object_type"], object_id)
        query = (
            query.exclude(state__in=ACTIVE_STATES)
            if params.get("closed") == "1"
            else query.filter(state__in=ACTIVE_STATES)
        )
        view = params.get("view", "")
        if view == "mine":
            query = query.filter(assignee_id=actor.employment_id)
        elif view == "queue":
            query = query.filter(assignee__isnull=True, state=Task.State.OPEN)
        elif view == "created":
            query = query.filter(created_by_employment_id=actor.employment_id)
        # § 19.4.7: search and filter by tags; by group (§ 19.4.5); by the title.
        if params.get("tag"):
            query = query.filter(tag_links__tag_id=_uuid(params["tag"], "tag"))
        if params.get("group"):
            query = query.filter(group_id=_uuid(params["group"], "group"))
        if params.get("q"):
            query = query.filter(title__icontains=params["q"][:100])
        resolver = access.RightsResolver()
        items = [
            t
            for t in query.order_by("due_at", "-number")[:LIST_LIMIT]
            if services.can_view(actor, t, resolver)
        ]
        if params.get("overdue") == "1":
            items = [t for t in items if services.overdue(t)]
        names = _names(company.id, items)
        return JsonResponse({"tasks": [_payload(t, actor, names) for t in items]})

    data = parse_json(request)
    links = data.get("links") or []
    if not isinstance(links, list):
        raise errors.validation_error("links")
    new = services.NewTask(
        title=get_str(data, "title", max_length=300).strip(),
        description=get_str(data, "description", max_length=10000, required=False),
        priority=get_str(data, "priority", max_length=10, required=False) or "normal",
        assign=get_str(data, "assign", max_length=40, required=False) or "self",
        location_id=_uuid(data.get("location_id"), "location_id"),
        location_given="location_id" in data,
        main=_link(data.get("object"), "object"),
        links=[x for x in (_link(item, "links") for item in links) if x is not None],
        due=_due(data),
        checklist=_texts(data.get("checklist")),
        checklist_required=data.get("checklist_required") is True,
        group_id=_uuid(data.get("group_id"), "group_id"),
        tag_ids=_ids(data.get("tag_ids"), "tag_ids"),
        repeat=_repeat(data.get("repeat")),
        reminders=services.check_reminders(_minutes(data.get("reminders"))),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        t = services.create(ctx, company, new)
        return CommandResult(
            201, {"task": _payload(t, actor, _names(company.id, [t]), detail=True)}
        )

    payload = {
        k: data.get(k)
        for k in (
            "title",
            "description",
            "priority",
            "assign",
            "location_id",
            "object",
            "links",
            "due_date",
            "due_time",
            "checklist",
            "checklist_required",
            "group_id",
            "tag_ids",
            "repeat",
            "reminders",
        )
    }
    return run_and_respond(request, "task.create", actor, payload, handler)


@require_http_methods(["GET", "PATCH"])
def task_detail(request: HttpRequest, cmp: str, task_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    company = context.company
    t = _visible(context, actor, task_id)
    if request.method == "GET":
        return JsonResponse({"task": _payload(t, actor, _names(company.id, [t]), detail=True)})
    data = parse_json(request)
    expected = get_int(data, "expected_version")
    changes = services.Changes(
        title=get_str(data, "title", max_length=300) if "title" in data else None,
        description=get_str(data, "description", max_length=10000, required=False)
        if "description" in data
        else None,
        priority=get_str(data, "priority", max_length=10) if "priority" in data else None,
        due=_due(data),
        due_set="due_date" in data,
        reminders=_minutes(data.get("reminders")) if "reminders" in data else None,
    )

    def handler(ctx: CommandContext) -> CommandResult:
        found = services.locked(company.id, task_id, expected)
        services.change(ctx, company, found, changes)
        return CommandResult(
            200, {"task": _payload(found, actor, _names(company.id, [found]), detail=True)}
        )

    payload = {
        "task_id": str(task_id),
        "expected_version": expected,
        **{
            k: data.get(k)
            for k in ("title", "description", "priority", "due_date", "due_time", "reminders")
        },
    }
    return run_and_respond(request, "task.change", actor, payload, handler)


ACTIONS: dict[str, Callable[[CommandContext, Task, dict[str, Any]], Any]] = {
    "take": lambda ctx, t, data: services.take(ctx, t),
    "start": lambda ctx, t, data: services.start(ctx, t),
    "wait": lambda ctx, t, data: services.hold(ctx, t, Task.State.WAITING, data["note"]),
    "pause": lambda ctx, t, data: services.hold(ctx, t, Task.State.PAUSED, data["note"]),
    "resume": lambda ctx, t, data: services.resume(ctx, t),
    "complete": lambda ctx, t, data: services.complete(ctx, t),
    "cancel": lambda ctx, t, data: services.cancel(ctx, t, data["reason"]),
    "assign": lambda ctx, t, data: services.assign(ctx, t, data["assignee_id"]),
    "release": lambda ctx, t, data: services.release(ctx, t),
    "link": lambda ctx, t, data: services.add_link(ctx, t, data["link"]),
    "comment": lambda ctx, t, data: services.comment(
        ctx, t, data["text"], data["file_id"], data["file_name"]
    ),
    # § 19.4: checklist, dependencies, group and tags.
    "checklist_add": lambda ctx, t, data: extensions.add_item(ctx, t, data["text"]),
    "checklist_rename": lambda ctx, t, data: extensions.rename_item(
        ctx, t, data["item_id"], data["text"]
    ),
    "checklist_move": lambda ctx, t, data: extensions.move_item(
        ctx, t, data["item_id"], data["position"]
    ),
    "checklist_remove": lambda ctx, t, data: extensions.remove_item(ctx, t, data["item_id"]),
    "checklist_mark": lambda ctx, t, data: extensions.mark_item(
        ctx, t, data["item_id"], data["done"]
    ),
    "depend": lambda ctx, t, data: extensions.add_dependency(ctx, t, data["predecessor_id"]),
    "undepend": lambda ctx, t, data: extensions.remove_dependency(ctx, t, data["predecessor_id"]),
    "group": lambda ctx, t, data: _change_group(ctx, t, data["group_id"]),
    "tags": lambda ctx, t, data: _change_tags(ctx, t, data["tag_ids"]),
}


def _editor(ctx: CommandContext, t: Task) -> None:
    """Choosing existing groups and tags goes with editing the task (§ 23.9.5)."""
    if t.created_by_employment_id != ctx.actor.employment_id:
        if not services.has(ctx.actor, "tasks.manage", t.location_id):
            raise errors.permission_denied("tasks.manage")


def _change_group(ctx: CommandContext, t: Task, group_id: UUID | None) -> None:
    _editor(ctx, t)
    extensions.set_group(ctx, t, group_id)


def _change_tags(ctx: CommandContext, t: Task, tag_ids: list[UUID]) -> None:
    _editor(ctx, t)
    extensions.set_tags(ctx, t, tag_ids)


def _arguments(action: str, data: dict[str, Any]) -> dict[str, Any]:
    if action in ("wait", "pause"):
        return {"note": get_str(data, "note", max_length=1000, required=False)}
    if action == "cancel":
        return {"reason": get_str(data, "reason", max_length=1000, required=False)}
    if action == "assign":
        assignee = _uuid(data.get("assignee_id"), "assignee_id")
        if assignee is None:
            raise errors.validation_error("assignee_id", code="FIELD_REQUIRED")
        return {"assignee_id": assignee}
    if action == "link":
        return {"link": _link(data, "object")}
    if action in ("checklist_add", "checklist_rename"):
        result: dict[str, Any] = {"text": get_str(data, "text", max_length=500, required=False)}
        if action == "checklist_rename":
            result["item_id"] = _required(data, "item_id")
        return result
    if action in ("checklist_move", "checklist_remove", "checklist_mark"):
        result = {"item_id": _required(data, "item_id")}
        if action == "checklist_move":
            result["position"] = get_int(data, "position")
        if action == "checklist_mark":
            if not isinstance(data.get("done"), bool):
                raise errors.validation_error("done")
            result["done"] = data["done"]
        return result
    if action in ("depend", "undepend"):
        return {"predecessor_id": _required(data, "predecessor_id")}
    if action == "group":
        return {"group_id": _uuid(data.get("group_id"), "group_id")}
    if action == "tags":
        return {"tag_ids": _ids(data.get("tag_ids"), "tag_ids")}
    if action == "comment":
        return {
            "text": get_str(data, "text", max_length=10000, required=False),
            "file_id": _uuid(data.get("file_id"), "file_id"),
            "file_name": get_str(data, "file_name", max_length=255, required=False),
        }
    return {}


@require_POST
def task_action(request: HttpRequest, cmp: str, task_id: UUID, action: str) -> JsonResponse:
    if action not in ACTIONS:
        raise errors.not_found()
    context, actor = _actor(request, cmp)
    company = context.company
    _visible(context, actor, task_id)
    data = parse_json(request)
    # Comments, links and checklist marks do not conflict with other changes.
    free = ("comment", "link", "checklist_mark")
    expected = None if action in free else get_int(data, "expected_version")
    arguments = _arguments(action, data)

    def handler(ctx: CommandContext) -> CommandResult:
        found = services.locked(company.id, task_id, expected)
        ACTIONS[action](ctx, found, arguments)
        found.refresh_from_db()
        return CommandResult(
            200, {"task": _payload(found, actor, _names(company.id, [found]), detail=True)}
        )

    payload = {"task_id": str(task_id), "expected_version": expected, **data}
    return run_and_respond(request, f"task.{action}", actor, payload, handler)


@require_http_methods(["GET"])
def assignees(request: HttpRequest, cmp: str) -> JsonResponse:
    """Who may be the executor in a scope (§ 19.1.6): for «manage» and for oneself."""
    context, actor = _actor(request, cmp)
    location_id = _uuid(request.GET.get("location_id"), "location_id")
    if not services.has(actor, "tasks.manage", location_id):
        raise errors.permission_denied("tasks.manage")
    ids = services.candidates(context.company.id, location_id)
    names = staff.employee_names(context.company.id, set(ids)) if ids else {}
    found = sorted(((str(x), names.get(x, "")) for x in ids), key=lambda pair: pair[1])
    return JsonResponse({"employees": [{"id": i, "name": n} for i, n in found]})


@require_http_methods(["GET", "PUT"])
def policy(request: HttpRequest, cmp: str) -> JsonResponse:
    """The company's rules for executors (decision З-2)."""
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        return JsonResponse({"policy": _policy_payload(services.policy_of(context.company.id))})
    if not access.RightsResolver().has_permission(actor, "company.settings"):
        raise errors.permission_denied("company.settings")
    data = parse_json(request)
    release = data.get("executor_may_release")
    change_due = data.get("executor_may_change_due")
    if not isinstance(release, bool) or not isinstance(change_due, bool):
        raise errors.validation_error("executor_may_release")
    expected = get_int(data, "expected_version")
    hours = data.get("overdue_repeat_hours")
    if hours is not None and (isinstance(hours, bool) or not isinstance(hours, int) or hours < 0):
        raise errors.validation_error("overdue_repeat_hours")

    def handler(ctx: CommandContext) -> CommandResult:
        found = services.set_policy(ctx, context.company, release, change_due, expected, hours)
        return CommandResult(200, {"policy": _policy_payload(found)})

    payload = {"release": release, "change_due": change_due, "hours": hours, "expected": expected}
    return run_and_respond(request, "task.policy", actor, payload, handler)


# --- Groups and tags (§ 19.4.5, § 19.4.7) --------------------------------------------------------


def _group_payload(g: Any) -> dict[str, Any]:
    return {"id": str(g.id), "name": g.name, "description": g.description, "version": g.version}


def _any_task_right(actor: Actor) -> bool:
    resolver = access.RightsResolver()
    codes = ("tasks.own", "tasks.queue", "tasks.create", "tasks.view_all", "tasks.manage")
    return any(resolver.has_permission(actor, p) for p in codes)


@require_http_methods(["GET", "POST"])
def group_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not _any_task_right(actor):
            raise errors.permission_denied("tasks.own")
        groups = extensions.groups_of(context.company.id)
        return JsonResponse({"groups": [_group_payload(g) for g in groups]})
    data = parse_json(request)
    name = get_str(data, "name", max_length=200)
    description = get_str(data, "description", max_length=2000, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        g = extensions.save_group(ctx, context.company, None, name, description)
        return CommandResult(201, {"group": _group_payload(g)})

    payload = {"name": name, "description": description}
    return run_and_respond(request, "task_group.create", actor, payload, handler)


@require_http_methods(["PATCH", "DELETE"])
def group_detail(request: HttpRequest, cmp: str, group_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "DELETE":

        def remove(ctx: CommandContext) -> CommandResult:
            extensions.remove_group(ctx, context.company, group_id)
            return CommandResult(200, {"removed": str(group_id)})

        payload = {"group_id": str(group_id)}
        return run_and_respond(request, "task_group.remove", actor, payload, remove)
    data = parse_json(request)
    name = get_str(data, "name", max_length=200)
    description = get_str(data, "description", max_length=2000, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        g = extensions.save_group(ctx, context.company, group_id, name, description)
        return CommandResult(200, {"group": _group_payload(g)})

    payload = {"group_id": str(group_id), "name": name, "description": description}
    return run_and_respond(request, "task_group.change", actor, payload, handler)


@require_http_methods(["GET", "POST"])
def tag_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not _any_task_right(actor):
            raise errors.permission_denied("tasks.own")
        tags = extensions.tags_of(context.company.id)
        return JsonResponse({"tags": [{"id": str(x.id), "name": x.name} for x in tags]})
    name = get_str(parse_json(request), "name", max_length=100)

    def handler(ctx: CommandContext) -> CommandResult:
        tag = extensions.save_tag(ctx, context.company, None, name)
        return CommandResult(201, {"tag": {"id": str(tag.id), "name": tag.name}})

    return run_and_respond(request, "task_tag.create", actor, {"name": name}, handler)


@require_http_methods(["PATCH", "DELETE"])
def tag_detail(request: HttpRequest, cmp: str, tag_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "DELETE":

        def remove(ctx: CommandContext) -> CommandResult:
            extensions.remove_tag(ctx, context.company, tag_id)
            return CommandResult(200, {"removed": str(tag_id)})

        return run_and_respond(request, "task_tag.remove", actor, {"tag_id": str(tag_id)}, remove)
    name = get_str(parse_json(request), "name", max_length=100)

    def handler(ctx: CommandContext) -> CommandResult:
        tag = extensions.save_tag(ctx, context.company, tag_id, name)
        return CommandResult(200, {"tag": {"id": str(tag.id), "name": tag.name}})

    payload = {"tag_id": str(tag_id), "name": name}
    return run_and_respond(request, "task_tag.rename", actor, payload, handler)


# --- Repetition (§ 19.4.2) -----------------------------------------------------------------------


@require_POST
def task_repeat(request: HttpRequest, cmp: str, task_id: UUID) -> JsonResponse:
    """«This and the following» (`change`) or the state of the repetition: pause, resume,
    stop."""
    context, actor = _actor(request, cmp)
    company = context.company
    t = _visible(context, actor, task_id)
    if t.series_id is None:
        raise errors.business_rule("TASK_NOT_REPEATING")
    data = parse_json(request)
    action = get_str(data, "action", max_length=10)
    expected = get_int(data, "expected_version")
    if action not in ("change", "pause", "resume", "stop"):
        raise errors.validation_error("action")
    changes = None
    if action == "change":
        raw_time = data.get("due_time")
        raw_end = data.get("ends_on")
        try:
            due_time = time.fromisoformat(str(raw_time)) if raw_time not in (None, "") else None
            ends_on = date.fromisoformat(str(raw_end)) if raw_end not in (None, "") else None
        except ValueError as exc:
            raise errors.validation_error("due_time", code="DATE_INVALID") from exc
        changes = series.SeriesChanges(
            title=get_str(data, "title", max_length=300) if "title" in data else None,
            description=get_str(data, "description", max_length=10000, required=False)
            if "description" in data
            else None,
            priority=get_str(data, "priority", max_length=10) if "priority" in data else None,
            due_time=due_time,
            due_time_set="due_time" in data,
            ends_on=ends_on,
            ends_on_set="ends_on" in data,
            checklist=_texts(data.get("checklist")) if "checklist" in data else None,
            checklist_required=data["checklist_required"]
            if isinstance(data.get("checklist_required"), bool)
            else None,
        )
    states = {"pause": "paused", "resume": "active", "stop": "stopped"}

    def handler(ctx: CommandContext) -> CommandResult:
        found = services.locked(company.id, task_id, None)
        held = series.locked(company.id, found.series_id, expected)  # type: ignore[arg-type]
        if changes is not None:
            series.change_following(ctx, company, found, changes)
        else:
            series.set_state(ctx, held, states[action])
        found.refresh_from_db()
        return CommandResult(
            200, {"task": _payload(found, actor, _names(company.id, [found]), detail=True)}
        )

    payload = {"task_id": str(task_id), **data}
    return run_and_respond(request, f"task.repeat_{action}", actor, payload, handler)
