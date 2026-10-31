"""HTTP API of the company for the identity of its vehicles (ТЗ § 8.2.1, § 8.6.5–8.6.6): the merge
of duplicate cards with a repeated one-time code, the split of a merge, the proposal of a
correction of a confirmed characteristic. The conflicts themselves are shown in the card."""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from c4rsys.companies import access
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import confirmations, merges, services
from c4rsys.fleet.models import VehicleCard
from c4rsys.fleet.views import _actor, _allowed, _payload
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json

REASON_MAX = 2000


def _card(request: HttpRequest, cmp: str, card_id: UUID) -> tuple[Any, Actor, VehicleCard]:
    context, actor = _actor(request, cmp)
    card = services.get_card(context.company.id, card_id)
    if not _allowed(actor, "vehicle.view", card):
        raise errors.not_found()
    return context, actor, card


def _require(actor: Actor, permission: str, *cards: VehicleCard) -> None:
    for card in cards:
        if not access.RightsResolver().has_permission(
            actor, permission, location_id=card.accounting_location_id
        ):
            raise errors.permission_denied(permission)


def _uuid(value: Any, field: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(field) from exc


def _subject(primary: UUID, duplicate: UUID) -> str:
    return f"{primary}:{duplicate}"


@require_GET
def merge_preview(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """The comparison of the main and the duplicate card before the merge (§ 8.6.6)."""
    context, actor, card = _card(request, cmp, card_id)
    duplicate = services.get_card(
        context.company.id, _uuid(request.GET.get("duplicate"), "duplicate")
    )
    _require(actor, "vehicle.merge", card, duplicate)
    return JsonResponse({"comparison": merges.preview(context.company.id, card.id, duplicate.id)})


@require_POST
def merge_code(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """The repeated one-time code of the merge, to the employee's confirmed email (§ 8.6.6)."""
    context, actor, card = _card(request, cmp, card_id)
    data = parse_json(request)
    duplicate = services.get_card(
        context.company.id, _uuid(data.get("duplicate_id"), "duplicate_id")
    )
    _require(actor, "vehicle.merge", card, duplicate)

    def handler(ctx: CommandContext) -> CommandResult:
        merges.request_code(ctx, context.company.id, _subject(card.id, duplicate.id))
        return CommandResult(200, {"sent": True})

    payload = {"card_id": str(card.id), "duplicate_id": str(duplicate.id)}
    return run_and_respond(request, "vehicle.merge_code", actor, payload, handler)


@require_POST
def merge(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, actor, card = _card(request, cmp, card_id)
    data = parse_json(request)
    duplicate = services.get_card(
        context.company.id, _uuid(data.get("duplicate_id"), "duplicate_id")
    )
    _require(actor, "vehicle.merge", card, duplicate)
    code = get_str(data, "code", max_length=12)
    reason = get_str(data, "reason", max_length=REASON_MAX, required=False)
    raw = data.get("choices") or {}
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
    ):
        raise errors.validation_error("choices")
    choices: dict[str, str] = dict(raw)

    def handler(ctx: CommandContext) -> CommandResult:
        failed = merges.check_code(ctx, context.company.id, _subject(card.id, duplicate.id), code)
        if failed is not None:
            ctx.audit(
                "vehicle.merge_code_rejected",
                object_type="vehicle_card",
                object_id=card.id,
                after={"error": failed.code},
            )
            return CommandResult(failed.status, failed.payload(ctx.request_id))
        record = merges.merge(
            ctx, context.company, card.id, duplicate.id, choices=choices, reason=reason
        )
        return CommandResult(
            201,
            {
                "merge": merges.merge_payload(record),
                "vehicle": _payload(services.get_card(context.company.id, card.id), detail=True),
            },
        )

    payload = {
        "card_id": str(card.id),
        "duplicate_id": str(duplicate.id),
        "choices": choices,
        "reason": reason,
        "code": code,
    }
    return run_and_respond(request, "vehicle.merge", actor, payload, handler)


@require_GET
def merge_list(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, _actor_, card = _card(request, cmp, card_id)
    return JsonResponse(
        {"merges": [merges.merge_payload(m) for m in merges.merges_of(context.company.id, card.id)]}
    )


@require_GET
def split_preview(request: HttpRequest, cmp: str, card_id: UUID, merge_id: UUID) -> JsonResponse:
    """The records made since the merge, to assign each to one of the two cards (§ 8.6.5)."""
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.split", card)
    found = merges.split_preview(context.company.id, merge_id)
    if found["merge"]["primary"]["id"] != str(card.id):
        raise errors.not_found()
    return JsonResponse(found)


@require_POST
def split(request: HttpRequest, cmp: str, card_id: UUID, merge_id: UUID) -> JsonResponse:
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.split", card)
    data = parse_json(request)
    reason = get_str(data, "reason", max_length=REASON_MAX)
    raw = data.get("assignments") or {}
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
    ):
        raise errors.validation_error("assignments")
    assignments: dict[str, str] = dict(raw)
    found = merges.split_preview(context.company.id, merge_id)
    if found["merge"]["primary"]["id"] != str(card.id):
        raise errors.not_found()

    def handler(ctx: CommandContext) -> CommandResult:
        record = merges.split(
            ctx, context.company.id, merge_id, assignments=assignments, reason=reason
        )
        return CommandResult(200, {"merge": merges.merge_payload(record)})

    payload = {
        "card_id": str(card.id),
        "merge_id": str(merge_id),
        "assignments": assignments,
        "reason": reason,
    }
    return run_and_respond(request, "vehicle.split", actor, payload, handler)


@require_POST
def propose(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """A correction of a confirmed value with its basis (§ 8.2.1)."""
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.edit", card)
    data = parse_json(request)
    name = get_str(data, "field", max_length=30)
    reason = get_str(data, "reason", max_length=REASON_MAX)
    use_working = data.get("use_working") is True
    values = {k: v for k, v in data.items() if k in (name, f"{name}_other")}

    def handler(ctx: CommandContext) -> CommandResult:
        proposal = confirmations.propose(
            ctx,
            context.company.id,
            card.id,
            name=name,
            data=values,
            reason=reason,
            use_working=use_working,
        )
        return CommandResult(
            201,
            {
                "proposal": confirmations.proposal_payload(proposal),
                "vehicle": _payload(services.get_card(context.company.id, card.id), detail=True),
            },
        )

    payload = {
        "card_id": str(card.id),
        "field": name,
        "values": values,
        "reason": reason,
        "use_working": use_working,
    }
    return run_and_respond(request, "vehicle.propose_characteristic", actor, payload, handler)
