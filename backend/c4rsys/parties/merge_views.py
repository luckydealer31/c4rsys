"""HTTP API of the merge and split of duplicate client cards and counterparties (ТЗ § 6.2.3,
§ 6.1.13, § 6.5.2–6.5.5, § 23.8.6), of the history in the cards (§ 6.1.14, § 8.1.2) and of the
requests of fresh data from a linked profile (§ 6.4.4)."""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies.views import run_and_respond
from c4rsys.parties import counterparties, data_requests, history, links, lists, merges
from c4rsys.parties.link_views import (
    VIEW,
    _actor,
    _answerer,
    _fields,
    _has,
)
from c4rsys.parties.models import CardMerge, ProfileLink
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json

MERGE = lists.MERGE
SPLIT = lists.SPLIT
REASON_MAX = 2000


def _card_type(value: Any) -> str:
    """The list of the cards: a client, a counterparty or an investor."""
    return lists.parse(value)


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
    for card_id in (primary, duplicate):
        lists.require_card(context.company.id, card_type, card_id)
    stored = lists.stored(card_type)
    return JsonResponse(
        {"comparison": merges.preview(context.company.id, stored, primary, duplicate)}
    )


@require_http_methods(["GET", "POST"])
def merge_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        card_type = _card_type(request.GET.get("card_type"))
        if not _has(actor, *VIEW[card_type]):
            raise errors.permission_denied(VIEW[card_type][0])
        card_id = _uuid(request.GET.get("card_id"), "card_id")
        lists.require_card(context.company.id, card_type, card_id)
        found = merges.merges_of(context.company.id, lists.stored(card_type), card_id)
        return JsonResponse({"merges": [merges.merge_payload(m) for m in found]})

    data = parse_json(request)
    card_type = _card_type(data.get("card_type"))
    _require(actor, MERGE[card_type])
    primary = _uuid(data.get("primary_id"), "primary_id")
    duplicate = _uuid(data.get("duplicate_id"), "duplicate_id")
    choices = _strings(data, "choices")
    reason = get_str(data, "reason", max_length=REASON_MAX, required=False)
    for card_id in (primary, duplicate):
        lists.require_card(context.company.id, card_type, card_id)

    def handler(ctx: CommandContext) -> CommandResult:
        found = merges.merge(
            ctx,
            context.company,
            lists.stored(card_type),
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
    return lists.of_merge(found)


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
def counterparty_history(
    request: HttpRequest, cmp: str, counterparty_id: UUID, role: str = lists.COUNTERPARTY
) -> JsonResponse:
    """What the contractor shares with this company as its client (§ 6.1.14)."""
    context, actor = _actor(request, cmp)
    _require(actor, f"{role}.view")
    card = counterparties.get(context.company.id, counterparty_id, role)
    return JsonResponse({"orders": history.contractor_history(context.company.id, card)})


# --- Fresh data of a linked profile (§ 6.4.4) ----------------------------------------------------


@require_GET
def link_data_requests(request: HttpRequest, cmp: str, link_id: UUID) -> JsonResponse:
    """The data the holder of the profile gave after the link was confirmed."""
    context, actor = _actor(request, cmp)
    link = ProfileLink.objects.filter(company_id=context.company.id, pk=link_id).first()
    if link is None:
        raise errors.not_found()
    card_type = links.list_of(link)
    if not _has(actor, *VIEW[card_type]):
        raise errors.permission_denied(VIEW[card_type][0])
    now = timezone.now()
    items = [
        data_requests.payload(r, now) for r in data_requests.of_link(context.company.id, link_id)
    ]
    return JsonResponse({"requests": items})


@require_POST
def incoming_data_give(request: HttpRequest, cmp: str, link_id: UUID) -> JsonResponse:
    """A company gives the fresh data of its profile to a company it is linked with."""
    context, actor = _answerer(request, cmp)
    fields = _fields(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        item = data_requests.give_as_company(ctx, context.company, link_id, fields)
        return CommandResult(201, {"given": data_requests.given_payload(item)})

    payload = {"link_id": str(link_id), "fields": fields}
    return run_and_respond(request, "profile_data.give", actor, payload, handler)


@require_POST
def my_data_give(request: HttpRequest, link_id: UUID) -> JsonResponse:
    """A person gives the fresh data of the profile to a linked company (personal cabinet)."""
    user, session = require_user(request)
    fields = _fields(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        item = data_requests.give_as_user(ctx, user, link_id, fields)
        return CommandResult(201, {"given": data_requests.given_payload(item)})

    actor = Actor(user_id=user.id, session_id=session.id)
    payload = {"link_id": str(link_id), "fields": fields}
    return run_and_respond(request, "profile_data.give", actor, payload, handler)
