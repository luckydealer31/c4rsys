"""HTTP API of selling prices and minimum stock (ТЗ § 11.7.2, § 11.7.4–11.7.6, § 11.7.8,
§ 23.7.4).

Prices are set with «Set selling prices»; the price mode of an item changes with «Price policy».
PROPOSAL: a minimum stock is set with «Manage the catalog».
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.companies import locations
from c4rsys.companies.views import run_and_respond
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json
from c4rsys.warehouse import catalog, prices, stock
from c4rsys.warehouse import dashboard as dashboard_service
from c4rsys.warehouse.stock_views import _actor, _minor_units, _require, _uuid, _visible


def _amount(data: dict[str, Any], context: Any, field: str = "amount") -> Decimal | None:
    return stock.money(data.get(field), field, _minor_units(context))


@require_GET
def item_prices(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    visible = set(_visible(context.company.id, actor))
    return JsonResponse({"prices": prices.item_prices(context.company, item_id, visible)})


@require_http_methods(["PUT"])
def set_price(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.price.set")
    data = parse_json(request)
    lot_id = _uuid(data.get("lot_id"), "lot_id")
    packaging_id = _uuid(data.get("packaging_id"), "packaging_id")
    amount = _amount(data, context)
    visible = set(_visible(context.company.id, actor))

    def handler(ctx: CommandContext) -> CommandResult:
        prices.set_price(ctx, context.company, item_id, lot_id, packaging_id, amount)
        return CommandResult(200, {"prices": prices.item_prices(context.company, item_id, visible)})

    payload = {
        "item_id": str(item_id),
        "lot_id": str(lot_id) if lot_id else None,
        "packaging_id": str(packaging_id) if packaging_id else None,
        "amount": str(amount) if amount is not None else None,
    }
    return run_and_respond(request, "warehouse.set_price", actor, payload, handler)


@require_POST
def set_mode(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.price.policy")
    data = parse_json(request)
    mode = get_str(data, "mode", max_length=10)
    raw = data.get("prices", [])
    if not isinstance(raw, list):
        raise errors.validation_error("prices")
    given = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise errors.validation_error(f"prices[{index}]")
        amount = _amount(entry, context)
        if amount is None:
            raise errors.validation_error(f"prices[{index}].amount", code="FIELD_REQUIRED")
        given.append((_uuid(entry.get("packaging_id"), f"prices[{index}].packaging_id"), amount))
    visible = set(_visible(context.company.id, actor))

    def handler(ctx: CommandContext) -> CommandResult:
        prices.set_mode(ctx, context.company, item_id, mode, tuple(given))
        return CommandResult(200, {"prices": prices.item_prices(context.company, item_id, visible)})

    payload = {
        "item_id": str(item_id),
        "mode": mode,
        "prices": [[str(p) if p else None, str(a)] for p, a in given],
    }
    return run_and_respond(request, "warehouse.set_price_mode", actor, payload, handler)


@require_http_methods(["PUT"])
def asset_price(request: HttpRequest, cmp: str, asset_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.price.set")
    amount = _amount(parse_json(request), context)

    def handler(ctx: CommandContext) -> CommandResult:
        asset = prices.set_asset_price(ctx, context.company, asset_id, amount)
        return CommandResult(
            200,
            {
                "id": str(asset.id),
                "sale_price": str(asset.sale_price) if asset.sale_price is not None else None,
            },
        )

    payload = {"asset_id": str(asset_id), "amount": str(amount) if amount is not None else None}
    return run_and_respond(request, "warehouse.set_asset_price", actor, payload, handler)


@require_http_methods(["PUT"])
def vehicle_price(request: HttpRequest, cmp: str, vehicle_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.price.set")
    amount = _amount(parse_json(request), context)

    def handler(ctx: CommandContext) -> CommandResult:
        prices.set_vehicle_price(ctx, context.company, vehicle_id, amount)
        return CommandResult(
            200,
            {
                "vehicle_id": str(vehicle_id),
                "sale_price": str(amount) if amount is not None else None,
            },
        )

    payload = {"vehicle_id": str(vehicle_id), "amount": str(amount) if amount is not None else None}
    return run_and_respond(request, "warehouse.set_vehicle_price", actor, payload, handler)


@require_GET
def vehicle_prices(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    found = prices.vehicle_prices(context.company.id)
    return JsonResponse({"prices": {str(k): v[0] for k, v in found.items()}})


@require_http_methods(["PUT"])
def minimum(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.catalog.manage")
    data = parse_json(request)
    context_id = _uuid(data.get("context_id"), "context_id")
    if context_id is None:
        raise errors.validation_error("context_id", code="FIELD_REQUIRED")
    value = data.get("quantity")
    quantity = None if value in (None, "") else catalog.quantity(value, "quantity")
    visible = set(_visible(context.company.id, actor))

    def handler(ctx: CommandContext) -> CommandResult:
        prices.set_minimum(ctx, context.company.id, item_id, context_id, quantity)
        return CommandResult(200, {"prices": prices.item_prices(context.company, item_id, visible)})

    payload = {
        "item_id": str(item_id),
        "context_id": str(context_id),
        "quantity": str(quantity) if quantity is not None else None,
    }
    return run_and_respond(request, "warehouse.set_minimum", actor, payload, handler)


@require_GET
def low_stock(request: HttpRequest, cmp: str) -> JsonResponse:
    """Items below their minimum (§ 11.7.8)."""
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    visible = set(_visible(context.company.id, actor))
    return JsonResponse({"low_stock": prices.low_stock(context.company.id, visible)})


@require_GET
def dashboard(request: HttpRequest, cmp: str) -> JsonResponse:
    """The starter dashboard «Warehouse» (§ 21.6.1) over the warehouses the employee may see
    (§ 21.1.1). PROPOSAL: the default period is the current month up to today."""
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    visible = _visible(context.company.id, actor)
    all_count = locations.storage_contexts(context.company.id).count()
    today = timezone.localdate()

    def day(name: str, default: date) -> date:
        value = request.GET.get(name, "")
        if not value:
            return default
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise errors.validation_error(name, code="DATE_INVALID") from exc

    filters = dashboard_service.Filters(
        date_from=day("from", today.replace(day=1)),
        date_to=day("to", today),
        context_id=_uuid(request.GET.get("context_id"), "context_id"),
        category=request.GET.get("category", "")[:100].strip(),
        item_id=_uuid(request.GET.get("item_id"), "item_id"),
        lot_id=_uuid(request.GET.get("lot_id"), "lot_id"),
        counterparty_id=_uuid(request.GET.get("counterparty_id"), "counterparty_id"),
    )
    if filters.date_to < filters.date_from:
        raise errors.validation_error("to", code="PERIOD_INVALID")
    return JsonResponse(
        {"dashboard": dashboard_service.build(context.company, visible, all_count, filters)}
    )
