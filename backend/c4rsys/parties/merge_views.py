"""HTTP API of the merge and split of duplicate client cards and counterparties (ТЗ § 6.2.3,
§ 6.1.13, § 6.5.2–6.5.5, § 23.8.6), of the history in the cards (§ 6.1.14, § 8.1.2) and of the
requests of fresh data from a linked profile (§ 6.4.4)."""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts import services as accounts
from c4rsys.accounts.middleware import require_user
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.parties import counterparties, data_requests, history, links, merges
from c4rsys.parties.link_views import VIEW, _actor, _answerer, _fields, _has, _own_link
from c4rsys.parties.models import CardMerge, ProfileDataRequest, ProfileLink
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json

MERGE = {"client": "client.merge", "counterparty": "counterparty.merge"}
SPLIT = {"client": "client.split", "counterparty": "counterparty.split"}
REASON_MAX = 2000


def _card_type(value: Any) -> str:
    if value not in merges.CARD_TYPES:
        raise errors.validation_error("card_type")
    return str(value)


def _uuid(value: Any, field: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(field) from exc


def _require(actor: Actor, permission: str) -> None:
    if not _has(actor, permission):
        raise errors.permission_denied(permission)


def _strings(data: dict[str, Any], key: str) -> dict[str, str]:
    value = data.get(key, {})
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise errors.validation_error(key)
    return value


# --- Merge ---------------------------------------------------------------------------------------


@require_GET
def merge_preview(request: HttpRequest, cmp: str) -> JsonResponse:
    """The comparison of two cards before the decision (§ 6.5.1): a read."""
    context, actor = _actor(request, cmp)
    card_type = _card_type(request.GET.get("card_type"))
    _require(actor, MERGE[card_type])
    primary = _uuid(request.GET.get("primary_id"), "primary_id")
    duplicate = _uuid(request.GET.get("duplicate_id"), "duplicate_id")
    return JsonResponse(
        {"comparison": merges.preview(context.company.id, card_type, primary, duplicate)}
    )


@require_http_methods(["GET", "POST"])
def merge_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        card_type = _card_type(request.GET.get("card_type"))
        if not _has(actor, *VIEW[card_type]):
            raise errors.permission_denied(VIEW[card_type][0])
        card_id = _uuid(request.GET.get("card_id"), "card_id")
        found = merges.merges_of(context.company.id, card_type, card_id)
        return JsonResponse({"merges": [merges.merge_payload(m) for m in found]})

    data = parse_json(request)
    card_type = _card_type(data.get("card_type"))
    _require(actor, MERGE[card_type])
    primary = _uuid(data.get("primary_id"), "primary_id")
    duplicate = _uuid(data.get("duplicate_id"), "duplicate_id")
    choices = _strings(data, "choices")
    reason = get_str(data, "reason", max_length=REASON_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        found = merges.merge(
            ctx,
            context.company,
            card_type,
            primary,
            duplicate,
            choices=choices,
            reason=reason,
        )
        return CommandResult(201, {"merge": merges.merge_payload(found)})

    payload = {
        "card_type": card_type,
        "primary_id": str(primary),
        "duplicate_id": str(duplicate),
        "choices": choices,
        "reason": reason,
    }
    return run_and_respond(request, f"{card_type}.merge", actor, payload, handler)


def _merge_type(company_id: UUID, merge_id: UUID) -> str:
    found = CardMerge.objects.filter(company_id=company_id, pk=merge_id).first()
    if found is None:
        raise errors.not_found()
    return found.card_type


@require_http_methods(["GET", "POST"])
def merge_split(request: HttpRequest, cmp: str, merge_id: UUID) -> JsonResponse:
    """§ 6.5.3: the records made after the merge, then their manual assignment."""
    context, actor = _actor(request, cmp)
    card_type = _merge_type(context.company.id, merge_id)
    _require(actor, SPLIT[card_type])
    if request.method == "GET":
        return JsonResponse(merges.split_preview(context.company.id, merge_id))
    data = parse_json(request)
    assignments = _strings(data, "assignments")
    reason = get_str(data, "reason", max_length=REASON_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        found = merges.split(
            ctx, context.company.id, merge_id, assignments=assignments, reason=reason
        )
        return CommandResult(200, {"merge": merges.merge_payload(found)})

    payload = {"merge_id": str(merge_id), "assignments": assignments, "reason": reason}
    return run_and_respond(request, f"{card_type}.split", actor, payload, handler)


# --- History (§ 6.1.14, § 8.1.2) -----------------------------------------------------------------


@require_GET
def client_history(request: HttpRequest, cmp: str, client_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if not _has(actor, *VIEW["client"]):
        raise errors.permission_denied("client.find")
    return JsonResponse(history.client_history(actor, context.company.id, client_id))


@require_GET
def counterparty_history(request: HttpRequest, cmp: str, counterparty_id: UUID) -> JsonResponse:
    """What the contractor shares with this company as its client (§ 6.1.14)."""
    context, actor = _actor(request, cmp)
    _require(actor, "counterparty.view")
    card = counterparties.get(context.company.id, counterparty_id)
    return JsonResponse({"orders": history.contractor_history(context.company.id, card)})


# --- Fresh data from a linked profile (§ 6.4.4) --------------------------------------------------


@require_http_methods(["GET", "POST"])
def link_data_requests(request: HttpRequest, cmp: str, link_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        link = ProfileLink.objects.filter(company_id=context.company.id, pk=link_id).first()
        if link is None:
            raise errors.not_found()
        card_type = links.card_type_of(link)
        if not _has(actor, *VIEW[card_type]):
            raise errors.permission_denied(VIEW[card_type][0])
        now = timezone.now()
        items = [
            data_requests.payload(r, now)
            for r in data_requests.of_link(context.company.id, link_id)
        ]
        return JsonResponse({"requests": items})

    _own_link(context.company.id, link_id, actor)
    occasion = get_str(parse_json(request), "occasion", max_length=500)

    def handler(ctx: CommandContext) -> CommandResult:
        found = data_requests.request(ctx, context.company.id, link_id, occasion)
        return CommandResult(201, {"request": data_requests.payload(found, timezone.now())})

    payload = {"link_id": str(link_id), "occasion": occasion}
    return run_and_respond(request, "profile_data_request.send", actor, payload, handler)


@require_POST
def data_request_cancel(request: HttpRequest, cmp: str, request_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    found = ProfileDataRequest.objects.filter(company_id=context.company.id, pk=request_id).first()
    if found is None:
        raise errors.not_found()
    _own_link(context.company.id, found.link_id, actor)

    def handler(ctx: CommandContext) -> CommandResult:
        item = data_requests.cancel(ctx, context.company.id, request_id)
        return CommandResult(200, {"request": data_requests.payload(item, timezone.now())})

    return run_and_respond(
        request, "profile_data_request.cancel", actor, {"request_id": str(request_id)}, handler
    )


@require_GET
def incoming_data_requests(request: HttpRequest, cmp: str) -> JsonResponse:
    context, _a = _answerer(request, cmp)
    now = timezone.now()
    available = companies.shareable_profile(context.company)
    items = [
        data_requests.incoming_payload(r, now, available)
        for r in data_requests.for_company(context.company.id).order_by("-created_at")[:100]
    ]
    return JsonResponse({"requests": items})


@require_POST
def incoming_data_accept(request: HttpRequest, cmp: str, request_id: UUID) -> JsonResponse:
    context, actor = _answerer(request, cmp)
    fields = _fields(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        item = data_requests.accept_as_company(ctx, context.company, request_id, fields)
        available = companies.shareable_profile(context.company)
        return CommandResult(
            200, {"request": data_requests.incoming_payload(item, timezone.now(), available)}
        )

    payload = {"request_id": str(request_id), "fields": fields}
    return run_and_respond(request, "profile_data_request.accept", actor, payload, handler)


@require_POST
def incoming_data_decline(request: HttpRequest, cmp: str, request_id: UUID) -> JsonResponse:
    context, actor = _answerer(request, cmp)

    def handler(ctx: CommandContext) -> CommandResult:
        item = data_requests.decline_as_company(ctx, context.company, request_id)
        available = companies.shareable_profile(context.company)
        return CommandResult(
            200, {"request": data_requests.incoming_payload(item, timezone.now(), available)}
        )

    return run_and_respond(
        request, "profile_data_request.decline", actor, {"request_id": str(request_id)}, handler
    )


@require_GET
def my_data_requests(request: HttpRequest) -> JsonResponse:
    user, _session = require_user(request)
    now = timezone.now()
    available = accounts.shareable_profile(user)
    items = [
        data_requests.incoming_payload(r, now, available)
        for r in data_requests.for_user(user).order_by("-created_at")[:100]
    ]
    return JsonResponse({"requests": items})


@require_POST
def my_data_accept(request: HttpRequest, request_id: UUID) -> JsonResponse:
    user, session = require_user(request)
    fields = _fields(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        item = data_requests.accept_as_user(ctx, user, request_id, fields)
        available = accounts.shareable_profile(user)
        return CommandResult(
            200, {"request": data_requests.incoming_payload(item, timezone.now(), available)}
        )

    actor = Actor(user_id=user.id, session_id=session.id)
    payload = {"request_id": str(request_id), "fields": fields}
    return run_and_respond(request, "profile_data_request.accept", actor, payload, handler)


@require_POST
def my_data_decline(request: HttpRequest, request_id: UUID) -> JsonResponse:
    user, session = require_user(request)

    def handler(ctx: CommandContext) -> CommandResult:
        item = data_requests.decline_as_user(ctx, user, request_id)
        available = accounts.shareable_profile(user)
        return CommandResult(
            200, {"request": data_requests.incoming_payload(item, timezone.now(), available)}
        )

    actor = Actor(user_id=user.id, session_id=session.id)
    return run_and_respond(
        request, "profile_data_request.decline", actor, {"request_id": str(request_id)}, handler
    )
