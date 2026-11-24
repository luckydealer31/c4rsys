"""HTTP API of the request to the owner of a vehicle (ТЗ § 8.3.1, § 8.3.13–8.3.14; decision Б-4).

The company works from the vehicle card with the right to change vehicles in its location. The
owner answers: a person in the personal area, a company in its own interface (§ 6.3.8).
"""

from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import document_transfer, owner_requests, owners
from c4rsys.fleet.models import VehicleCard
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import parse_json


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _card(company_id: UUID, card_id: UUID, actor: Actor, permission: str) -> VehicleCard:
    card = VehicleCard.objects.filter(company_id=company_id, pk=card_id).first()
    if card is None:
        raise errors.not_found()
    if not access.RightsResolver().has_permission(
        actor, permission, location_id=card.accounting_location_id
    ):
        raise errors.permission_denied(permission)
    return card


@require_http_methods(["GET", "POST"])
def card_requests(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _card(context.company.id, card_id, actor, "vehicle.view")
        now = timezone.now()
        items = [
            owner_requests.request_payload(r, now)
            for r in owner_requests.requests_of_card(context.company.id, card_id)
        ]
        return JsonResponse({"requests": items})

    card = _card(context.company.id, card_id, actor, "vehicle.edit")
    # The owner may be named here by his USR ID, email, phone or CMP ID (customer's remark of
    # 25.11.2026): he is declared and asked in one command.
    body = parse_json(request)
    lookup = body.get("owner_lookup")
    named = (
        owners.parse({"owner_kind": "profile", "owner_lookup": lookup}, card.relation)
        if lookup not in (None, "")
        else None
    )

    def handler(ctx: CommandContext) -> CommandResult:
        if named is not None:
            locked = VehicleCard.objects.select_for_update().get(
                company_id=context.company.id, pk=card_id
            )
            if locked.relation != VehicleCard.Relation.MANAGED:
                raise errors.business_rule("OWNER_REQUEST_MANAGED_ONLY")
            # The name, the contact and the address given before stay with the profile.
            earlier = locked.owner_claims.order_by("-created_at").first()
            keep = owners.Owner(
                kind=named.kind,
                lookup=named.lookup,
                name=named.name or (earlier.manual_name if earlier else ""),
                contact=named.contact or (earlier.manual_contact if earlier else ""),
                address=named.address or (earlier.manual_address if earlier else ""),
            )
            if not owners.is_current(locked, keep):
                owners.declare(ctx, context.company, locked, keep)
        sent = owner_requests.send(ctx, context.company, card_id)
        return CommandResult(201, {"request": owner_requests.request_payload(sent, timezone.now())})

    payload = {"card_id": str(card_id), "owner": named.payload() if named else None}
    return run_and_respond(request, "vehicle.owner_request", actor, payload, handler)


@require_POST
def cancel(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _card(context.company.id, card_id, actor, "vehicle.edit")

    def handler(ctx: CommandContext) -> CommandResult:
        done = owner_requests.cancel(ctx, context.company.id, card_id)
        return CommandResult(200, {"request": owner_requests.request_payload(done, timezone.now())})

    return run_and_respond(
        request, "vehicle.owner_request_cancel", actor, {"card_id": str(card_id)}, handler
    )


def _answerer(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    context, actor = _actor(request, cmp)
    # The same holders as those notified of the request (companies.link_answerers).
    if not context.is_top_authority and not access.RightsResolver().has_permission(
        actor, "company.link.answer", whole_company=True
    ):
        raise errors.permission_denied("company.link.answer")
    return context, actor


@require_GET
def incoming(request: HttpRequest, cmp: str) -> JsonResponse:
    context, _ = _answerer(request, cmp)
    now = timezone.now()
    items = [
        owner_requests.incoming_payload(r, now, company_id=context.company.id)
        for r in owner_requests.for_company(context.company.id)
        .select_related("company", "vehicle")
        .prefetch_related("answers")
        .order_by("-created_at")[:100]
    ]
    return JsonResponse({"requests": items})


@require_POST
def incoming_answer(request: HttpRequest, cmp: str, request_id: UUID) -> JsonResponse:
    context, actor = _answerer(request, cmp)
    body = parse_json(request)
    given = body.get("answers")
    documents = document_transfer.parse_ids(body.get("document_ids"))

    def handler(ctx: CommandContext) -> CommandResult:
        done = owner_requests.answer_as_company(ctx, context.company, request_id, given, documents)
        return CommandResult(
            200,
            {
                "request": owner_requests.incoming_payload(
                    done, timezone.now(), company_id=context.company.id
                )
            },
        )

    payload = {
        "request_id": str(request_id),
        "answers": given,
        "document_ids": sorted(str(d) for d in documents),
    }
    return run_and_respond(request, "vehicle.owner_answer", actor, payload, handler)


@require_GET
def mine(request: HttpRequest) -> JsonResponse:
    """Requests to the person as the owner of vehicles."""
    user, _session = require_user(request)
    now = timezone.now()
    items = [
        owner_requests.incoming_payload(r, now, user_id=user.id)
        for r in owner_requests.for_user(user)
        .select_related("company", "vehicle")
        .prefetch_related("answers")
        .order_by("-created_at")[:100]
    ]
    return JsonResponse({"requests": items})


@require_POST
def my_answer(request: HttpRequest, request_id: UUID) -> JsonResponse:
    user, session = require_user(request)
    body = parse_json(request)
    given = body.get("answers")
    documents = document_transfer.parse_ids(body.get("document_ids"))

    def handler(ctx: CommandContext) -> CommandResult:
        done = owner_requests.answer_as_user(ctx, user, request_id, given, documents)
        return CommandResult(
            200,
            {"request": owner_requests.incoming_payload(done, timezone.now(), user_id=user.id)},
        )

    actor = Actor(user_id=user.id, session_id=session.id)
    payload = {
        "request_id": str(request_id),
        "answers": given,
        "document_ids": sorted(str(d) for d in documents),
    }
    return run_and_respond(request, "vehicle.owner_answer", actor, payload, handler)
