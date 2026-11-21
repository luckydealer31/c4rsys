"""HTTP API of tags (customer's decisions of 17.11.2026)."""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, staff
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.tags import services
from c4rsys.tags.models import NAME_MAX, Tag

MANAGE = "tags.manage"


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _may_manage(actor: Actor) -> bool:
    return access.RightsResolver().has_permission(actor, MANAGE)


def _may_change(actor: Actor, tag: Tag) -> bool:
    """A tag of the company is changed with the right or by an employee named in its group; a
    personal one — by its owner, who alone sees it."""
    return (
        tag.owner_user_id is not None
        or _may_manage(actor)
        or services.is_named(tag.group_id, actor.employment_id)
    )


def _require_owner_or_right(actor: Actor, tag: Tag) -> None:
    if not _may_change(actor, tag):
        raise errors.permission_denied(MANAGE)


def _optional_uuid(data: dict[str, Any], key: str) -> UUID | None:
    return None if data.get(key) in (None, "") else _uuid(data[key], key)


def _uuid(value: Any, key: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


@require_http_methods(["GET", "POST"])
def tag_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    company_id = context.company.id
    if request.method == "GET":
        may = _may_manage(actor)
        answer: dict[str, Any] = {
            "tags": services.listing(company_id, actor.user_id),
            "groups": services.group_payloads(company_id, actor, may),
            "can_manage": may,
            "palette": list(services.PALETTE),
            "kinds": list(services.LISTED),
        }
        if may:
            # Whom a group may name: the employees of the company.
            names = staff.employee_names(company_id)
            answer["employees"] = sorted(
                ({"id": str(i), "name": n} for i, n in names.items()), key=lambda e: e["name"]
            )
        return JsonResponse(answer)
    data = parse_json(request)
    personal = data.get("personal") is True
    group_id = None if personal else _optional_uuid(data, "group_id")
    # A tag of the company: with the right, or in a group the employee is named in.
    if not (personal or _may_manage(actor) or services.is_named(group_id, actor.employment_id)):
        raise errors.permission_denied(MANAGE)
    name = get_str(data, "name", max_length=NAME_MAX)
    color = get_str(data, "color", max_length=20)

    def handler(ctx: CommandContext) -> CommandResult:
        tag = services.create(ctx, company_id, name, color, personal=personal, group_id=group_id)
        return CommandResult(201, {"tag": services.payload(tag, 0)})

    payload = {
        "name": name,
        "color": color,
        "personal": personal,
        "group_id": str(group_id) if group_id else None,
    }
    return run_and_respond(request, "tag.create", actor, payload, handler)


@require_http_methods(["PATCH"])
def tag_detail(request: HttpRequest, cmp: str, tag_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    company_id = context.company.id
    tag = services.get(company_id, actor.user_id, tag_id)
    _require_owner_or_right(actor, tag)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    name = get_str(data, "name", max_length=NAME_MAX) if "name" in data else None
    color = get_str(data, "color", max_length=20) if "color" in data else None
    # A tag is moved to another group, or out of its group, with the right only.
    group: tuple[UUID | None] | None = None
    if "group_id" in data and tag.owner_user_id is None:
        wanted = _optional_uuid(data, "group_id")
        if wanted != tag.group_id:
            if not _may_manage(actor):
                raise errors.permission_denied(MANAGE)
            group = (wanted,)
    # Nothing new: there is no command to run.
    same_name = name is None or " ".join(name.split()) == tag.name
    same = same_name and color in (None, tag.color) and group is None
    if same and tag.version == expected_version:
        return JsonResponse({"tag": services.payload(tag)})

    def handler(ctx: CommandContext) -> CommandResult:
        changed = services.change(
            ctx, company_id, tag_id, expected_version, name=name, color=color, group=group
        )
        return CommandResult(200, {"tag": services.payload(changed)})

    payload = {
        "tag_id": str(tag_id),
        "name": name,
        "color": color,
        "group": [str(group[0]) if group[0] else None] if group else None,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "tag.change", actor, payload, handler)


@require_POST
def tag_action(request: HttpRequest, cmp: str, tag_id: UUID, action: str) -> JsonResponse:
    if action not in ("archive", "restore"):
        raise errors.not_found()
    context, actor = _actor(request, cmp)
    company_id = context.company.id
    tag = services.get(company_id, actor.user_id, tag_id)
    _require_owner_or_right(actor, tag)
    expected_version = get_int(parse_json(request), "expected_version")
    if (tag.archived_at is not None) == (action == "archive") and tag.version == expected_version:
        return JsonResponse({"tag": services.payload(tag)})

    def handler(ctx: CommandContext) -> CommandResult:
        tag = services.set_archived(ctx, company_id, tag_id, expected_version, action == "archive")
        return CommandResult(200, {"tag": services.payload(tag)})

    payload = {"tag_id": str(tag_id), "expected_version": expected_version}
    return run_and_respond(request, f"tag.{action}", actor, payload, handler)


@require_GET
def tag_objects(request: HttpRequest, cmp: str, tag_id: UUID) -> JsonResponse:
    """The page of a tag: everything it is put on that the employee may see."""
    context, actor = _actor(request, cmp)
    tag = services.get(context.company.id, actor.user_id, tag_id)
    return JsonResponse(
        {
            "tag": services.payload(tag),
            "can_manage": _may_change(actor, tag),
            "objects": services.objects_of(actor, context.company.id, tag),
        }
    )


def _object_type(value: Any) -> str:
    if value not in services.TYPES:
        raise errors.validation_error("type", code="TAG_OBJECT_TYPE_UNKNOWN")
    return str(value)


@require_http_methods(["GET", "PUT"])
def tag_links(request: HttpRequest, cmp: str) -> JsonResponse:
    """GET `?type=&id=` — the tags of a record; without `id` — of every record of the kind.
    PUT — puts exactly the given tags on a record the employee sees."""
    context, actor = _actor(request, cmp)
    company_id = context.company.id
    if request.method == "GET":
        object_type = _object_type(request.GET.get("type"))
        if not request.GET.get("id"):
            return JsonResponse(
                {"links": services.links_of_type(company_id, actor.user_id, object_type)}
            )
        object_id = _uuid(request.GET.get("id"), "id")
        if not services.may_see(actor, company_id, object_type, object_id):
            raise errors.not_found()
        found = services.tags_of(company_id, actor.user_id, object_type, object_id)
        return JsonResponse({"tags": [services.payload(t) for t in found]})
    data = parse_json(request)
    object_type = _object_type(data.get("type"))
    object_id = _uuid(data.get("id"), "id")
    raw = data.get("tag_ids")
    if not isinstance(raw, list) or len(raw) > 100:
        raise errors.validation_error("tag_ids")
    wanted = {_uuid(value, "tag_ids") for value in raw}
    if not services.may_see(actor, company_id, object_type, object_id):
        raise errors.not_found()
    if services.same_links(company_id, actor.user_id, object_type, object_id, wanted):
        found = services.tags_of(company_id, actor.user_id, object_type, object_id)
        return JsonResponse({"tags": [services.payload(t) for t in found]})

    def handler(ctx: CommandContext) -> CommandResult:
        services.set_links(ctx, company_id, object_type, object_id, wanted)
        found = services.tags_of(company_id, actor.user_id, object_type, object_id)
        return CommandResult(200, {"tags": [services.payload(t) for t in found]})

    payload = {
        "type": object_type,
        "id": str(object_id),
        "tag_ids": sorted(str(i) for i in wanted),
    }
    return run_and_respond(request, "tag.set_links", actor, payload, handler)


# --- Groups of tags -----------------------------------------------------------------------------


def _strings(value: Any, key: str) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > 100
        or not all(isinstance(x, str) for x in value)
    ):
        raise errors.validation_error(key)
    return list(value)


@require_POST
def group_list(request: HttpRequest, cmp: str) -> JsonResponse:
    """A new group: its name, where its tags are offered and who makes them — with the right."""
    context, actor = _actor(request, cmp)
    company_id = context.company.id
    if not _may_manage(actor):
        raise errors.permission_denied(MANAGE)
    data = parse_json(request)
    name = get_str(data, "name", max_length=NAME_MAX)
    kinds = _strings(data.get("kinds", []), "kinds")
    members = {_uuid(x, "member_ids") for x in _strings(data.get("member_ids", []), "member_ids")}

    def handler(ctx: CommandContext) -> CommandResult:
        group = services.create_group(ctx, company_id, name, kinds, members)
        return CommandResult(201, {"group_id": str(group.id)})

    payload = {"name": name, "kinds": kinds, "member_ids": sorted(str(m) for m in members)}
    return run_and_respond(request, "tag_group.create", actor, payload, handler)


@require_http_methods(["PATCH", "DELETE"])
def group_detail(request: HttpRequest, cmp: str, group_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    company_id = context.company.id
    if not _may_manage(actor):
        raise errors.permission_denied(MANAGE)
    if request.method == "DELETE":

        def remove(ctx: CommandContext) -> CommandResult:
            services.remove_group(ctx, company_id, group_id)
            return CommandResult(200, {"removed": str(group_id)})

        return run_and_respond(
            request, "tag_group.remove", actor, {"group_id": str(group_id)}, remove
        )
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    name = get_str(data, "name", max_length=NAME_MAX) if "name" in data else None
    kinds = _strings(data["kinds"], "kinds") if "kinds" in data else None
    members = (
        {_uuid(x, "member_ids") for x in _strings(data["member_ids"], "member_ids")}
        if "member_ids" in data
        else None
    )

    def handler(ctx: CommandContext) -> CommandResult:
        services.change_group(
            ctx, company_id, group_id, expected_version, name=name, kinds=kinds, member_ids=members
        )
        return CommandResult(200, {"group_id": str(group_id)})

    payload = {
        "group_id": str(group_id),
        "name": name,
        "kinds": kinds,
        "member_ids": sorted(str(m) for m in members) if members is not None else None,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "tag_group.change", actor, payload, handler)
