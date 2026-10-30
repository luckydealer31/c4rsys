"""HTTP API of client groups (ТЗ § 6.2.14; decision В-3)."""

from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.parties import groups
from c4rsys.parties.models import ClientGroup
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _has(actor: Actor, *permissions: str) -> bool:
    resolver = access.RightsResolver()
    return any(resolver.has_permission(actor, p) for p in permissions)


def _payload(g: ClientGroup) -> dict[str, object]:
    return {"id": str(g.id), "name": g.name, "state": g.state, "version": g.version}


@require_http_methods(["GET", "POST"])
def group_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not _has(actor, "client.find", "client.list", "order.discount", "company.settings"):
            raise errors.permission_denied("client.find")
        return JsonResponse({"groups": [_payload(g) for g in groups.groups_of(context.company.id)]})
    if not _has(actor, "client.edit"):
        raise errors.permission_denied("client.edit")
    name = get_str(parse_json(request), "name", max_length=100)

    def handler(ctx: CommandContext) -> CommandResult:
        return CommandResult(201, {"group": _payload(groups.create(ctx, context.company, name))})

    return run_and_respond(request, "client_group.create", actor, {"name": name}, handler)


@require_http_methods(["PATCH"])
def group_detail(request: HttpRequest, cmp: str, group_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if not _has(actor, "client.edit"):
        raise errors.permission_denied("client.edit")
    data = parse_json(request)
    name = get_str(data, "name", max_length=100, required=False) if "name" in data else None
    archived = data.get("archived")
    if archived is not None and not isinstance(archived, bool):
        raise errors.validation_error("archived")

    def handler(ctx: CommandContext) -> CommandResult:
        found = groups.change(ctx, context.company.id, group_id, name=name, archived=archived)
        return CommandResult(200, {"group": _payload(found)})

    payload = {"group_id": str(group_id), "name": name, "archived": archived}
    return run_and_respond(request, "client_group.change", actor, payload, handler)


@require_http_methods(["GET", "PUT"])
def client_groups(request: HttpRequest, cmp: str, client_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not _has(actor, "client.find", "client.list"):
            raise errors.permission_denied("client.find")
        return JsonResponse({"group_ids": [str(g) for g in groups.ids_of_client(client_id)]})
    if not _has(actor, "client.edit"):
        raise errors.permission_denied("client.edit")
    raw = parse_json(request).get("group_ids")
    if not isinstance(raw, list):
        raise errors.validation_error("group_ids")
    try:
        ids = [UUID(str(x)) for x in raw]
    except ValueError as exc:
        raise errors.validation_error("group_ids") from exc

    def handler(ctx: CommandContext) -> CommandResult:
        result = groups.set_of_client(ctx, context.company.id, client_id, ids)
        return CommandResult(200, {"group_ids": [str(g) for g in result]})

    payload = {"client_id": str(client_id), "group_ids": [str(x) for x in ids]}
    return run_and_respond(request, "client.groups", actor, payload, handler)
