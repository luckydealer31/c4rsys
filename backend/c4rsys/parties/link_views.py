"""HTTP API of linking cards to c4rsys profiles (ТЗ § 6.2.5–6.3, § 6.4.2, § 6.6.2; decision Б-4).

The requesting company works from the card: the right to change clients or counterparties. The
recipient answers: a person in the personal area, a company in its own interface (§ 6.3.8).
"""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts import services as accounts
from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.parties import links
from c4rsys.parties.models import ProfileLink
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json

VIEW = {"client": ("client.find", "client.list"), "counterparty": ("counterparty.view",)}
EDIT = {"client": "client.edit", "counterparty": "counterparty.edit"}


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _has(actor: Actor, *permissions: str) -> bool:
    resolver = access.RightsResolver()
    return any(resolver.has_permission(actor, p) for p in permissions)


def _card_type(value: str) -> str:
    if value not in links.CARD_TYPES:
        raise errors.validation_error("card_type")
    return value


def _own_link(company_id: UUID, link_id: UUID, actor: Actor) -> ProfileLink:
    link = ProfileLink.objects.filter(company_id=company_id, pk=link_id).first()
    if link is None:
        raise errors.not_found()
    permission = EDIT[links.card_type_of(link)]
    if not _has(actor, permission):
        raise errors.permission_denied(permission)
    return link


def _fields(data: dict[str, Any]) -> list[str]:
    value = data.get("fields", [])
    if not isinstance(value, list) or not all(isinstance(f, str) for f in value):
        raise errors.validation_error("fields")
    return value


# --- The requesting company -------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def card_links(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        card_type = _card_type(request.GET.get("card_type", ""))
        if not _has(actor, *VIEW[card_type]):
            raise errors.permission_denied(VIEW[card_type][0])
        try:
            card_id = UUID(request.GET.get("card_id", ""))
        except ValueError as exc:
            raise errors.validation_error("card_id") from exc
        now = timezone.now()
        items = [
            links.payload(link, now)
            for link in links.links_of_card(context.company.id, card_type, card_id)
        ]
        return JsonResponse({"links": items})

    data = parse_json(request)
    card_type = _card_type(get_str(data, "card_type", max_length=20))
    if not _has(actor, EDIT[card_type]):
        raise errors.permission_denied(EDIT[card_type])
    try:
        card_id = UUID(get_str(data, "card_id", max_length=40))
    except ValueError as exc:
        raise errors.validation_error("card_id") from exc
    target_text = get_str(data, "target", max_length=254)

    def handler(ctx: CommandContext) -> CommandResult:
        card = (
            links.card_model(card_type)
            .objects.filter(company_id=context.company.id, pk=card_id)
            .first()
        )
        if card is None:
            raise errors.not_found()
        target = links.resolve_target(card.kind, target_text)
        link = links.request_link(ctx, context.company, card_type, card_id, target)
        return CommandResult(201, {"link": links.payload(link, timezone.now())})

    payload = {"card_type": card_type, "card_id": str(card_id), "target": target_text}
    return run_and_respond(request, "profile_link.request", actor, payload, handler)


@require_POST
def link_cancel(request: HttpRequest, cmp: str, link_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _own_link(context.company.id, link_id, actor)

    def handler(ctx: CommandContext) -> CommandResult:
        link = links.cancel(ctx, context.company.id, link_id)
        return CommandResult(200, {"link": links.payload(link, timezone.now())})

    return run_and_respond(
        request, "profile_link.cancel", actor, {"link_id": str(link_id)}, handler
    )


@require_POST
def link_break(request: HttpRequest, cmp: str, link_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _own_link(context.company.id, link_id, actor)
    initiator = get_str(parse_json(request), "initiator", max_length=10)

    def handler(ctx: CommandContext) -> CommandResult:
        link = links.break_link(ctx, context.company.id, link_id, initiator)
        return CommandResult(200, {"link": links.payload(link, timezone.now())})

    payload = {"link_id": str(link_id), "initiator": initiator}
    return run_and_respond(request, "profile_link.break", actor, payload, handler)


@require_POST
def link_apply(request: HttpRequest, cmp: str, link_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _own_link(context.company.id, link_id, actor)
    data = parse_json(request)
    field = get_str(data, "field", max_length=40)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        card = links.apply_value(ctx, context.company.id, link_id, field, expected_version)
        return CommandResult(200, {"card_id": str(card.id), "version": card.version})

    payload = {"link_id": str(link_id), "field": field, "expected_version": expected_version}
    return run_and_respond(request, "profile_link.apply", actor, payload, handler)


# --- A company that is asked (§ 6.3.8) ----------------------------------------------------------


def _answerer(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    context, actor = _actor(request, cmp)
    if not context.is_top_authority and not _has(actor, "company.link.answer"):
        raise errors.permission_denied("company.link.answer")
    return context, actor


@require_GET
def incoming(request: HttpRequest, cmp: str) -> JsonResponse:
    context, _actor_ = _answerer(request, cmp)
    now = timezone.now()
    available = companies.shareable_profile(context.company)
    items = [
        links.incoming_payload(link, now, available)
        for link in links.for_company(context.company.id)
        .select_related("company")
        .order_by("-created_at")[:100]
    ]
    return JsonResponse({"links": items})


@require_POST
def incoming_accept(request: HttpRequest, cmp: str, link_id: UUID) -> JsonResponse:
    context, actor = _answerer(request, cmp)
    fields = _fields(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        link = links.accept_as_company(ctx, context.company, link_id, fields)
        available = companies.shareable_profile(context.company)
        return CommandResult(200, {"link": links.incoming_payload(link, timezone.now(), available)})

    payload = {"link_id": str(link_id), "fields": fields}
    return run_and_respond(request, "profile_link.accept", actor, payload, handler)


@require_POST
def incoming_decline(request: HttpRequest, cmp: str, link_id: UUID) -> JsonResponse:
    context, actor = _answerer(request, cmp)

    def handler(ctx: CommandContext) -> CommandResult:
        link = links.decline_as_company(ctx, context.company, link_id)
        available = companies.shareable_profile(context.company)
        return CommandResult(200, {"link": links.incoming_payload(link, timezone.now(), available)})

    return run_and_respond(
        request, "profile_link.decline", actor, {"link_id": str(link_id)}, handler
    )


# --- A person (§ 6.3.8, § 7.1.2) --------------------------------------------------------------


@require_GET
def my_links(request: HttpRequest) -> JsonResponse:
    """Pending requests and the links of the person (accepted or broken)."""
    user, _session = require_user(request)
    now = timezone.now()
    available = accounts.shareable_profile(user)
    items = [
        links.incoming_payload(link, now, available)
        for link in links.for_user(user).select_related("company").order_by("-created_at")
        if links.effective_state(link, now)
        in (ProfileLink.State.PENDING, ProfileLink.State.ACCEPTED, ProfileLink.State.BROKEN)
    ]
    return JsonResponse({"links": items})


@require_POST
def my_accept(request: HttpRequest, link_id: UUID) -> JsonResponse:
    user, session = require_user(request)
    fields = _fields(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        link = links.accept_as_user(ctx, user, link_id, fields)
        available = accounts.shareable_profile(user)
        return CommandResult(200, {"link": links.incoming_payload(link, timezone.now(), available)})

    actor = Actor(user_id=user.id, session_id=session.id)
    payload = {"link_id": str(link_id), "fields": fields}
    return run_and_respond(request, "profile_link.accept", actor, payload, handler)


@require_POST
def my_decline(request: HttpRequest, link_id: UUID) -> JsonResponse:
    user, session = require_user(request)

    def handler(ctx: CommandContext) -> CommandResult:
        link = links.decline_as_user(ctx, user, link_id)
        available = accounts.shareable_profile(user)
        return CommandResult(200, {"link": links.incoming_payload(link, timezone.now(), available)})

    actor = Actor(user_id=user.id, session_id=session.id)
    return run_and_respond(
        request, "profile_link.decline", actor, {"link_id": str(link_id)}, handler
    )
