"""HTTP API of warehouse reserves, removed parts and costs of materials on a vehicle (ТЗ § 11.2.2,
§ 11.2.9, § 11.4.4, § 11.4.7, § 11.7.1, § 23.7.3).

Rights count in the warehouse of a location: «Reserve» makes and releases a reserve (PROPOSAL);
a removed part comes into a warehouse by «Receive» there and is written off by «Write off» in
the warehouse it was issued from (PROPOSAL); the parts and the costs of materials of a vehicle
are seen with «View stock» in some warehouse (PROPOSAL).
"""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import services as fleet
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json
from c4rsys.warehouse import catalog, costs, parts, reserves, stock
from c4rsys.warehouse.models import Asset, PartInstallation, StockReserve
from c4rsys.warehouse.stock_views import (
    COMMENT_MAX,
    _actor,
    _context_of,
    _item_brief,
    _minor_units,
    _moment,
    _require,
    _require_in,
    _uuid,
    _visible,
)

LIST_LIMIT = 200


def _reserve_payload(reserve: StockReserve) -> dict[str, Any]:
    return {
        "id": str(reserve.id),
        "version": reserve.version,
        "number": reserve.number,
        "purpose": reserve.purpose,
        "source_id": str(reserve.source_id) if reserve.source_id else None,
        "context": {
            "id": str(reserve.context_id),
            "location_name": reserve.context.location.name,
        },
        "item": _item_brief(reserve.item),
        "asset": {"id": str(reserve.asset_id), "public_id": reserve.asset.public_id}
        if reserve.asset
        else None,
        "lot": {"id": str(reserve.lot_id), "public_id": reserve.lot.public_id}
        if reserve.lot
        else None,
        "packaging_quantity": str(reserve.packaging.quantity) if reserve.packaging else None,
        "opened_package_id": str(reserve.opened_package_id) if reserve.opened_package_id else None,
        "quantity": str(reserve.quantity),
        "used_quantity": str(reserve.used_quantity),
        "state": reserve.state,
        "expires_at": reserve.expires_at.isoformat() if reserve.expires_at else None,
        "comment": reserve.comment,
        "created_at": reserve.created_at.isoformat(),
        "ended_at": reserve.ended_at.isoformat() if reserve.ended_at else None,
        "end_reason": reserve.end_reason,
    }


def _reserves(company_id: UUID) -> Any:
    return StockReserve.objects.filter(company_id=company_id).select_related(
        "context__location", "item", "asset", "lot", "packaging"
    )


def _reserve_result(status: int, company_id: UUID, reserve_id: UUID) -> CommandResult:
    found = _reserves(company_id).get(pk=reserve_id)
    return CommandResult(status, {"reserve": _reserve_payload(found)})


@require_http_methods(["GET", "POST"])
def reserve_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require(actor, "warehouse.view")
        found = _reserves(context.company.id).filter(
            context_id__in=set(_visible(context.company.id, actor))
        )
        state = request.GET.get("state", "")
        if state:
            if state not in StockReserve.State.values:
                raise errors.validation_error("state")
            found = found.filter(state=state)
        item_id = _uuid(request.GET.get("item_id"), "item_id")
        if item_id:
            found = found.filter(item_id=item_id)
        records = list(found.order_by("-number")[:LIST_LIMIT])
        return JsonResponse({"reserves": [_reserve_payload(r) for r in records]})
    data = parse_json(request)
    context_id = _uuid(data.get("context_id"), "context_id")
    if context_id is None:
        raise errors.validation_error("context_id", code="FIELD_REQUIRED")
    _require_in(actor, "warehouse.reserve", _context_of(context.company.id, context_id))
    item_id = _uuid(data.get("item_id"), "item_id")
    if item_id is None:
        raise errors.validation_error("item_id", code="FIELD_REQUIRED")
    value = data.get("quantity")
    quantity = None if value in (None, "") else catalog.quantity(value, "quantity")
    asset_id = _uuid(data.get("asset_id"), "asset_id")
    many = data.get("asset_ids")
    if asset_id is None and isinstance(many, list) and many:
        # One unit per reserve; the picker of a form sends a list.
        if len(many) != 1:
            raise errors.validation_error("asset_ids", code="RESERVE_ONE_UNIT")
        asset_id = _uuid(many[0], "asset_ids")
    lot_id = _uuid(data.get("lot_id"), "lot_id")
    packaging_id = _uuid(data.get("packaging_id"), "packaging_id")
    opened_id = _uuid(data.get("opened_package_id"), "opened_package_id")
    expires_at = _moment(data.get("expires_at"), "expires_at")
    comment = get_str(data, "comment", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        reserve = reserves.create(
            ctx,
            context.company,
            context_id,
            item_id=item_id,
            asset_id=asset_id,
            lot_id=lot_id,
            packaging_id=packaging_id,
            opened_package_id=opened_id,
            quantity=quantity,
            expires_at=expires_at,
            comment=comment,
        )
        return _reserve_result(201, context.company.id, reserve.id)

    payload = {
        "context_id": str(context_id),
        "item_id": str(item_id),
        "asset_id": str(asset_id) if asset_id else None,
        "lot_id": str(lot_id) if lot_id else None,
        "packaging_id": str(packaging_id) if packaging_id else None,
        "opened_package_id": str(opened_id) if opened_id else None,
        "quantity": str(quantity) if quantity is not None else None,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "comment": comment,
    }
    return run_and_respond(request, "warehouse.reserve", actor, payload, handler)


@require_POST
def reserve_release(request: HttpRequest, cmp: str, reserve_id: UUID) -> JsonResponse:
    """A reserve of a work is released in the work (it is a part of the work's material)."""
    context, actor = _actor(request, cmp)
    found = _reserves(context.company.id).filter(pk=reserve_id).first()
    if found is None:
        raise errors.not_found()
    _require_in(actor, "warehouse.reserve", found.context)
    if found.purpose == StockReserve.Purpose.WORK:
        raise errors.business_rule("RESERVE_OF_WORK")
    reason = get_str(parse_json(request), "reason", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        reserves.release(ctx, context.company.id, reserve_id, reason)
        return _reserve_result(200, context.company.id, reserve_id)

    payload = {"reserve_id": str(reserve_id), "reason": reason}
    return run_and_respond(request, "warehouse.release_reserve", actor, payload, handler)


# --- Parts of vehicles (§ 11.4.3, § 11.4.7) -------------------------------------------------------


def _parts_payload(company_id: UUID, found: list[PartInstallation]) -> list[dict[str, Any]]:
    names = fleet.card_names(company_id, {i.vehicle_id for i in found})
    return [parts.payload(i, names) for i in found]


@require_GET
def vehicle_parts(request: HttpRequest, cmp: str, vehicle_id: UUID) -> JsonResponse:
    """The installed parts of a vehicle and the periods of earlier installations."""
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    fleet.get_card(context.company.id, vehicle_id)
    found = parts.of_vehicle(context.company.id, vehicle_id)
    return JsonResponse({"installations": _parts_payload(context.company.id, found)})


@require_GET
def asset_installations(request: HttpRequest, cmp: str, asset_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    if not Asset.objects.filter(company_id=context.company.id, pk=asset_id).exists():
        raise errors.not_found()
    found = parts.of_asset(context.company.id, asset_id)
    return JsonResponse({"installations": _parts_payload(context.company.id, found)})


@require_GET
def vehicle_costs(request: HttpRequest, cmp: str, vehicle_id: UUID) -> JsonResponse:
    """§ 11.4.4, § 11.2.9: the cost of materials consumed on the vehicle; «incomplete» while a
    cost is not given."""
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    fleet.get_card(context.company.id, vehicle_id)
    currency = context.company.main_currency_id or ""
    return JsonResponse(costs.of_vehicle(context.company.id, vehicle_id, currency))


@require_GET
def removed_parts(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    found = parts.awaiting(context.company.id)
    return JsonResponse({"installations": _parts_payload(context.company.id, found)})


def _installation(company_id: UUID, installation_id: UUID) -> PartInstallation:
    found = (
        PartInstallation.objects.select_related("asset")
        .filter(company_id=company_id, pk=installation_id)
        .first()
    )
    if found is None:
        raise errors.not_found()
    return found


def _part_result(status: int, company_id: UUID, installation_id: UUID) -> CommandResult:
    found = parts.installations(company_id).get(pk=installation_id)
    return CommandResult(status, {"installation": _parts_payload(company_id, [found])[0]})


@require_POST
def removed_accept(request: HttpRequest, cmp: str, installation_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _installation(context.company.id, installation_id)
    data = parse_json(request)
    context_id = _uuid(data.get("context_id"), "context_id")
    if context_id is None:
        raise errors.validation_error("context_id", code="FIELD_REQUIRED")
    _require_in(actor, "warehouse.receive", _context_of(context.company.id, context_id))
    cost_mode = get_str(data, "cost_mode", max_length=10)
    cost = stock.money(data.get("cost"), "cost", _minor_units(context))
    comment = get_str(data, "comment", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        parts.accept(
            ctx,
            context.company.id,
            installation_id,
            context_id=context_id,
            cost_mode=cost_mode,
            cost=cost,
            comment=comment,
        )
        return _part_result(200, context.company.id, installation_id)

    payload = {
        "installation_id": str(installation_id),
        "context_id": str(context_id),
        "cost_mode": cost_mode,
        "cost": str(cost) if cost is not None else None,
        "comment": comment,
    }
    return run_and_respond(request, "warehouse.accept_removed_part", actor, payload, handler)


@require_POST
def removed_write_off(request: HttpRequest, cmp: str, installation_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    found = _installation(context.company.id, installation_id)
    _require_in(
        actor, "warehouse.write_off", _context_of(context.company.id, found.asset.context_id)
    )
    data = parse_json(request)
    reason_id = _uuid(data.get("reason_id"), "reason_id")
    comment = get_str(data, "comment", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        parts.write_off(ctx, context.company, installation_id, reason_id=reason_id, comment=comment)
        return _part_result(200, context.company.id, installation_id)

    payload = {
        "installation_id": str(installation_id),
        "reason_id": str(reason_id) if reason_id else None,
        "comment": comment,
    }
    return run_and_respond(request, "warehouse.write_off_removed_part", actor, payload, handler)
