"""HTTP API of a rental and its order: proposals of a preliminary order, annulment and transfer
(ТЗ § 12.1.7, § 12.2.2, § 12.3.7; customer's decisions З2, З5, З6 of 28.10.2026). A proposal
needs «Book» in its location; annulment and transfer need their own rights in the issue location
and access to the orders concerned."""

from datetime import UTC
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.companies import access
from c4rsys.companies.views import run_and_respond
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.platform.money import round_money
from c4rsys.rentals import billing, bookings, changes, order_links, rentals
from c4rsys.rentals.models import Rental
from c4rsys.rentals.views import _actor, _moment, _optional_uuid, _rental_payload, _uuid


def _require(actor: Actor, permission: str, location_id: UUID | None) -> None:
    if not access.RightsResolver().has_permission(actor, permission, location_id=location_id):
        raise errors.permission_denied(permission)


def _visible_order(actor: Actor, company_id: UUID, order_id: UUID) -> None:
    if not orders.can_view(actor, orders.get_order(company_id, order_id)):
        raise errors.not_found()


def _utc(moment: Any) -> str | None:
    return moment.astimezone(UTC).isoformat() if moment else None


def proposal_payload(rental: Rental) -> dict[str, Any]:
    """A proposal with its estimated cost and the operations of the vehicle it would overlap —
    a warning: it holds nothing until the order is formed."""
    found = billing.base_calculation(rental)
    estimate = None
    if found is not None:
        calc = found[1]
        places = int((rental.tariff_snapshot or {}).get("currency_minor_units", 2))
        estimate = {"amount": str(round_money(calc.base_amount, places)), "currency": calc.currency}
    start, end = rental.planned_start, rental.planned_end
    return {
        "id": str(rental.id),
        "vehicle_card_id": str(rental.vehicle_card_id),
        "tariff_id": str(rental.tariff_id) if rental.tariff_id else None,
        "tariff_name": (rental.tariff_snapshot or {}).get("name"),
        "issue_location_id": str(rental.issue_location_id) if rental.issue_location_id else None,
        "planned_return_location_id": str(rental.planned_return_location_id)
        if rental.planned_return_location_id
        else None,
        "calc_timezone": rental.calc_timezone,
        "planned_start": _utc(start),
        "planned_end": _utc(end),
        "estimate": estimate,
        "conflicts": bookings.conflicts(rental.vehicle_card_id, start, end, exclude=rental.id)
        if start and end
        else [],
        "version": rental.version,
    }


@require_http_methods(["GET", "POST"])
def proposals(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _visible_order(actor, context.company.id, order_id)
    if request.method == "GET":
        items = order_links.proposals_of(context.company.id, order_id)
        return JsonResponse({"proposals": [proposal_payload(r) for r in items]})
    data = parse_json(request)
    location_id = _optional_uuid(data, "issue_location_id")
    _require(actor, order_links.BOOK, location_id)
    start = _moment(data, "planned_start", required=True)
    end = _moment(data, "planned_end", required=True)
    if start is None or end is None:
        raise errors.validation_error("planned_start", code="FIELD_REQUIRED")
    new = order_links.ProposalData(
        vehicle_card_id=_uuid(data, "vehicle_card_id"),
        tariff_id=_uuid(data, "tariff_id", "TARIFF_REQUIRED"),
        planned_start=start,
        planned_end=end,
        issue_location_id=location_id,
        planned_return_location_id=_optional_uuid(data, "planned_return_location_id"),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        rental = order_links.add_proposal(ctx, context.company.id, order_id, new)
        return CommandResult(201, {"proposal": proposal_payload(rental)})

    payload = {k: str(v) if v is not None else None for k, v in new.__dict__.items()}
    return run_and_respond(
        request, "rental.propose", actor, {"order_id": str(order_id), **payload}, handler
    )


@require_POST
def withdraw(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    rental = rentals.get_rental(context.company.id, rental_id)
    _require(actor, order_links.BOOK, rental.issue_location_id)
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        order_links.withdraw_proposal(ctx, context.company.id, rental_id, expected_version)
        return CommandResult(200, {"ok": True})

    payload = {"rental_id": str(rental_id), "expected_version": expected_version}
    return run_and_respond(request, "rental.withdraw_proposal", actor, payload, handler)


@require_POST
def annul(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    changes.require_right(
        actor, rentals.get_rental(context.company.id, rental_id), order_links.ANNUL
    )
    data = parse_json(request)
    reason = get_str(data, "reason", max_length=500, required=False)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        rental = order_links.annul(ctx, context.company.id, rental_id, reason, expected_version)
        return CommandResult(200, {"rental": _rental_payload(rental)})

    payload = {"rental_id": str(rental_id), "reason": reason, "expected_version": expected_version}
    return run_and_respond(request, "rental.annul", actor, payload, handler)


@require_POST
def transfer(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    """«Transfer the rental» with access to both orders (§ 12.3.7)."""
    context, actor = _actor(request, cmp)
    rental = rentals.get_rental(context.company.id, rental_id)
    changes.require_right(actor, rental, order_links.TRANSFER)
    _visible_order(actor, context.company.id, rental.order_id)
    data = parse_json(request)
    target = _optional_uuid(data, "order_id")
    if target is not None:
        _visible_order(actor, context.company.id, target)
    elif not access.RightsResolver().has_permission(actor, "order.create"):
        raise errors.permission_denied("order.create")
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        moved = order_links.transfer(ctx, context.company.id, rental_id, target, expected_version)
        return CommandResult(200, {"rental": _rental_payload(moved)})

    payload = {
        "rental_id": str(rental_id),
        "order_id": str(target) if target else None,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.transfer", actor, payload, handler)
