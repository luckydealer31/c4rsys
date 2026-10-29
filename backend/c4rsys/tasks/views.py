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
from c4rsys.tasks import objects, services
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
    }
    if detail:
        result.update(
            {
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
        **{k: data.get(k) for k in ("title", "description", "priority", "due_date", "due_time")},
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
}


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
    # Comments and links do not conflict with other changes: no expected version for them.
    expected = None if action in ("comment", "link") else get_int(data, "expected_version")
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

    def handler(ctx: CommandContext) -> CommandResult:
        found = services.set_policy(ctx, context.company, release, change_due, expected)
        return CommandResult(200, {"policy": _policy_payload(found)})

    payload = {"release": release, "change_due": change_due, "expected": expected}
    return run_and_respond(request, "task.policy", actor, payload, handler)
