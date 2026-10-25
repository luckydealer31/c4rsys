"""HTTP API of sales from the warehouse through orders (ТЗ § 11.9, § 23.7.3).

Adding goods needs «Sell» in the warehouse they come from (§ 11.9.4); a new order also needs
«Create orders». Cancelling a wrongly closed sale needs the separate «Correct a closed sale».
"""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.companies import access
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import services as fleet
from c4rsys.orders import services as orders
from c4rsys.orders.models import Order
from c4rsys.parties import services as clients
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json
from c4rsys.warehouse import catalog, sales, stock
from c4rsys.warehouse.models import SaleLine
from c4rsys.warehouse.operations_views import _uuids
from c4rsys.warehouse.stock_views import (
    COMMENT_MAX,
    _actor,
    _context_of,
    _has,
    _item_brief,
    _require,
    _require_in,
    _uuid,
)


def _line_payload(line: SaleLine, vehicles: dict[UUID, str]) -> dict[str, Any]:
    return {
        "id": str(line.id),
        "state": line.state,
        "context": {"id": str(line.context_id), "location_name": line.context.location.name},
        "item": _item_brief(line.item) if line.item else None,
        "vehicle": {"id": str(line.vehicle_id), "name": vehicles.get(line.vehicle_id, "")}
        if line.vehicle_id
        else None,
        "asset": line.asset.public_id if line.asset else None,
        "lot": line.lot.public_id if line.lot else None,
        "packaging_quantity": str(line.packaging.quantity)
        if line.packaging and not line.opened_package_id
        else None,
        "opened": line.opened_package_id is not None,
        "quantity": str(line.quantity),
        "unit_price": stock.money_text(line.unit_price, line.currency),
        "amount": stock.money_text(line.amount, line.currency),
        "currency": line.currency,
        "created_at": line.created_at.isoformat(),
        "sold_at": line.sold_at.isoformat() if line.sold_at else None,
        "cancel_reason": line.cancel_reason,
    }


def _lines(company_id: UUID, order_id: UUID) -> dict[str, Any]:
    lines = sales.lines_of_order(company_id, order_id)
    vehicles = fleet.card_names(company_id, {line.vehicle_id for line in lines if line.vehicle_id})
    return {"lines": [_line_payload(line, vehicles) for line in lines]}


def _order_for(request: HttpRequest, cmp: str, order_id: UUID) -> tuple[Any, Any, Order]:
    context, actor = _actor(request, cmp)
    order = orders.get_order(context.company.id, order_id)
    return context, actor, order


@require_POST
def start(request: HttpRequest, cmp: str) -> JsonResponse:
    """A new order for a sale from the warehouse to the chosen client (decision of 25.10.2026)."""
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.sell")
    _require(actor, "order.create")
    client_id = _uuid(parse_json(request).get("client_id"), "client_id")
    if client_id is None:
        raise errors.validation_error("client_id", code="FIELD_REQUIRED")
    clients.get_client(context.company.id, client_id)

    def handler(ctx: CommandContext) -> CommandResult:
        order = sales.start_order(ctx, context.company, client_id, actor.employment_id)
        return CommandResult(201, {"order": {"id": str(order.id), "number": order.number}})

    return run_and_respond(
        request, "warehouse.start_sale", actor, {"client_id": str(client_id)}, handler
    )


@require_GET
def open_orders(request: HttpRequest, cmp: str) -> JsonResponse:
    """Open client orders goods can be added to."""
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.sell")
    found = (
        orders.orders_of(context.company.id)
        .filter(state=Order.State.OPEN, client__isnull=False)
        .select_related("client")[:100]
    )
    return JsonResponse(
        {
            "orders": [
                {
                    "id": str(o.id),
                    "number": o.number,
                    "client": clients.display_name(o.client) if o.client else "",
                }
                for o in found
            ]
        }
    )


@require_http_methods(["GET", "POST"])
def order_lines(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    context, actor, order = _order_for(request, cmp, order_id)
    if request.method == "GET":
        if not (_has_any(actor, "order.view") or _has_any(actor, "warehouse.view")):
            raise errors.permission_denied("order.view")
        return JsonResponse(_lines(context.company.id, order.id))
    data = parse_json(request)
    context_id = _uuid(data.get("context_id"), "context_id")
    if context_id is None:
        raise errors.validation_error("context_id", code="FIELD_REQUIRED")
    _require_in(actor, "warehouse.sell", _context_of(context.company.id, context_id))
    value = data.get("quantity")
    sale = sales.SaleData(
        context_id=context_id,
        item_id=_uuid(data.get("item_id"), "item_id"),
        asset_ids=_uuids(data.get("asset_ids"), "asset_ids"),
        lot_id=_uuid(data.get("lot_id"), "lot_id"),
        packaging_id=_uuid(data.get("packaging_id"), "packaging_id"),
        opened_package_id=_uuid(data.get("opened_package_id"), "opened_package_id"),
        quantity=None if value in (None, "") else catalog.quantity(value, "quantity"),
        vehicle_id=_uuid(data.get("vehicle_id"), "vehicle_id"),
        confirm_expired=data.get("confirm_expired") is True,
    )

    def handler(ctx: CommandContext) -> CommandResult:
        sales.add(ctx, context.company, order.id, sale)
        return CommandResult(201, _lines(context.company.id, order.id))

    payload = {"order_id": str(order.id), **sale.as_dict()}
    return run_and_respond(request, "warehouse.add_sale", actor, payload, handler)


def _has_any(actor: Any, permission: str) -> bool:
    return access.RightsResolver().has_permission(actor, permission)


def _line_for(context: Any, line_id: UUID, order: Order) -> SaleLine:
    line = (
        SaleLine.objects.select_related("context")
        .filter(company_id=context.company.id, pk=line_id, order=order)
        .first()
    )
    if line is None:
        raise errors.not_found()
    return line


@require_http_methods(["DELETE"])
def order_line_remove(
    request: HttpRequest, cmp: str, order_id: UUID, line_id: UUID
) -> JsonResponse:
    context, actor, order = _order_for(request, cmp, order_id)
    line = _line_for(context, line_id, order)
    if not _has(actor, "warehouse.sell", line.context):
        raise errors.permission_denied("warehouse.sell")

    def handler(ctx: CommandContext) -> CommandResult:
        sales.remove(ctx, context.company.id, line_id)
        return CommandResult(200, _lines(context.company.id, order.id))

    return run_and_respond(
        request, "warehouse.remove_sale", actor, {"line_id": str(line_id)}, handler
    )


@require_POST
def order_line_cancel(
    request: HttpRequest, cmp: str, order_id: UUID, line_id: UUID
) -> JsonResponse:
    context, actor, order = _order_for(request, cmp, order_id)
    line = _line_for(context, line_id, order)
    if not _has(actor, "warehouse.sale.correct", line.context):
        raise errors.permission_denied("warehouse.sale.correct")
    reason = get_str(parse_json(request), "reason", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        sales.cancel(ctx, context.company.id, line_id, reason)
        return CommandResult(200, _lines(context.company.id, order.id))

    return run_and_respond(
        request,
        "warehouse.cancel_sale",
        actor,
        {"line_id": str(line_id), "reason": reason},
        handler,
    )
