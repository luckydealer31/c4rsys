"""HTTP API of receipts, purchases and stock (ТЗ § 11.1.9–11.1.10, § 11.2, § 23.7.3).

Rights count in the warehouse of a location: receipts need «receive» there, the stock is shown
for the warehouses the employee may view. A purchase is not bound to a warehouse — the
warehouse is chosen at each receipt — so «purchase» is checked for the company.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, locations
from c4rsys.companies import services as companies
from c4rsys.companies.models import ActivityContext
from c4rsys.companies.views import run_and_respond
from c4rsys.parties import counterparties
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.warehouse import catalog, operations, prices, stock
from c4rsys.warehouse.models import Item, LotLayer, PurchaseOrder, Receipt

LIST_LIMIT = 200
COMMENT_MAX = 2000


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _has(actor: Actor, permission: str, context: ActivityContext) -> bool:
    return access.RightsResolver().has_permission(
        actor, permission, location_id=context.location_id, context_id=context.id
    )


def _require_in(actor: Actor, permission: str, context: ActivityContext) -> None:
    if not _has(actor, permission, context):
        raise errors.permission_denied(permission)


def _require(actor: Actor, permission: str) -> None:
    if not access.RightsResolver().has_permission(actor, permission):
        raise errors.permission_denied(permission)


def _visible(company_id: UUID, actor: Actor) -> dict[UUID, ActivityContext]:
    """Warehouses whose stock the employee may see (§ 11.1.1)."""
    return {
        c.id: c for c in locations.storage_contexts(company_id) if _has(actor, "warehouse.view", c)
    }


def visible_warehouses(company_id: UUID, actor: Actor) -> dict[UUID, ActivityContext]:
    """For the reports (§ 21.6.1): the same warehouses as the dashboard of the module."""
    return _visible(company_id, actor)


def _context_of(company_id: UUID, context_id: UUID) -> ActivityContext:
    context = locations.storage_contexts(company_id).filter(pk=context_id).first()
    if context is None:
        raise errors.not_found()
    return context


# --- Parsing ------------------------------------------------------------------------------------


def _uuid(value: Any, field: str) -> UUID | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise errors.validation_error(field)
    try:
        return UUID(value)
    except ValueError as exc:
        raise errors.validation_error(field) from exc


def _date(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise errors.validation_error(field)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise errors.validation_error(field, code="DATE_INVALID") from exc


def _moment(value: Any, field: str) -> datetime | None:
    """A moment with its time zone (stored in UTC)."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise errors.validation_error(field)
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise errors.validation_error(field, code="DATE_INVALID") from exc
    if moment.tzinfo is None:
        raise errors.validation_error(field, code="TIMEZONE_REQUIRED")
    return moment


def _quantity(value: Any, field: str) -> Decimal | None:
    if value in (None, ""):
        return None
    return catalog.quantity(value, field)


def _expected(value: Any, field: str) -> Decimal | None:
    """Expected may be zero: nothing was expected, everything received is a discrepancy."""
    if value in (None, "", 0, "0"):
        return None if value in (None, "") else Decimal("0.00")
    return catalog.quantity(value, field)


def _packs(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise errors.validation_error(field, code="QUANTITY_NOT_POSITIVE")
    return value


def _serials(value: Any, field: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise errors.validation_error(field)
    found = []
    for entry in value:
        if not isinstance(entry, str) or len(entry.strip()) > stock.SERIAL_MAX:
            raise errors.validation_error(field)
        found.append(entry.strip())
    return tuple(found)


def _minor_units(context: companies.CompanyContext) -> int:
    return stock.company_currency(context.company).minor_units


def _lines(data: dict[str, Any], minor_units: int) -> tuple[stock.LineData, ...]:
    lines = data.get("lines")
    if not isinstance(lines, list):
        raise errors.validation_error("lines")
    parsed = []
    for index, line in enumerate(lines):
        where = f"lines[{index}]"
        if not isinstance(line, dict):
            raise errors.validation_error(where)
        item_id = _uuid(line.get("item_id"), f"{where}.item_id")
        if item_id is None:
            raise errors.validation_error(f"{where}.item_id", code="FIELD_REQUIRED")
        parsed.append(
            stock.LineData(
                item_id=item_id,
                quantity=_quantity(line.get("quantity"), f"{where}.quantity"),
                packaging_id=_uuid(line.get("packaging_id"), f"{where}.packaging_id"),
                packs=_packs(line.get("packs"), f"{where}.packs"),
                expected_quantity=_expected(
                    line.get("expected_quantity"), f"{where}.expected_quantity"
                ),
                unit_cost=stock.money(line.get("unit_cost"), f"{where}.unit_cost", minor_units),
                tags=tuple(stock.tags(line.get("tags"), f"{where}.tags")),
                serial_numbers=_serials(line.get("serial_numbers"), f"{where}.serial_numbers"),
                lot_id=_uuid(line.get("lot_id"), f"{where}.lot_id"),
                lot_external_number=get_str(
                    line, "lot_external_number", max_length=100, required=False
                ),
                lot_production_date=_date(
                    line.get("lot_production_date"), f"{where}.lot_production_date"
                ),
                lot_expiry_date=_date(line.get("lot_expiry_date"), f"{where}.lot_expiry_date"),
                purchase_line_id=_uuid(line.get("purchase_line_id"), f"{where}.purchase_line_id"),
            )
        )
    return tuple(parsed)


def _receipt_data(data: dict[str, Any], minor_units: int) -> stock.ReceiptData:
    return stock.ReceiptData(
        context_id=_uuid(data.get("context_id"), "context_id"),
        source=get_str(data, "source", max_length=20),
        supplier_id=_uuid(data.get("supplier_id"), "supplier_id"),
        purchase_id=_uuid(data.get("purchase_id"), "purchase_id"),
        document_id=_uuid(data.get("document_id"), "document_id"),
        received_at=_moment(data.get("received_at"), "received_at"),
        comment=get_str(data, "comment", max_length=COMMENT_MAX, required=False),
        lines=_lines(data, minor_units),
    )


# --- Payloads -----------------------------------------------------------------------------------


def _item_brief(item: Item) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "name": item.name,
        "unit": item.unit,
        "accounting": item.accounting,
        "article": item.article,
    }


def _decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _receipt_payload(receipt: Receipt, suppliers: dict[UUID, str]) -> dict[str, Any]:
    lines = []
    total = Decimal(0)
    incomplete = False
    all_lines = list(receipt.lines.all())
    returned = operations.returned_by_line([line.id for line in all_lines])
    layers = {
        layer.receipt_line_id: layer.remaining
        for layer in LotLayer.objects.filter(receipt_line__in=all_lines)
    }
    for line in all_lines:
        cost = None
        if line.unit_cost is None:
            incomplete = True
        else:
            cost = line.unit_cost * (
                line.corrected_quantity if line.corrected_quantity is not None else line.quantity
            )
            total += cost
        lines.append(
            {
                "id": str(line.id),
                "item": _item_brief(line.item),
                # After a correction (§ 11.6.2) the corrected quantity; the posted one stays.
                "quantity": str(
                    line.corrected_quantity
                    if line.corrected_quantity is not None
                    else line.quantity
                ),
                "posted_quantity": str(line.quantity),
                "packaging_id": str(line.packaging_id) if line.packaging_id else None,
                "packaging_quantity": _decimal(line.packaging.quantity) if line.packaging else None,
                "packs": line.packs,
                "expected_quantity": _decimal(line.expected_quantity),
                # § 11.2.3: a difference from the expected is shown for a further decision.
                "discrepancy": _decimal(line.quantity - line.expected_quantity)
                if line.expected_quantity is not None
                else None,
                "unit_cost": stock.money_text(line.unit_cost, receipt.currency),
                "total_cost": stock.money_text(cost, receipt.currency),
                "tags": line.tags,
                "serial_numbers": line.serial_numbers,
                "lot": {"id": str(line.lot.id), "public_id": line.lot.public_id}
                if line.lot
                else None,
                "new_lot": line.new_lot,
                "lot_external_number": line.lot_external_number,
                "lot_production_date": line.lot_production_date.isoformat()
                if line.lot_production_date
                else None,
                "lot_expiry_date": line.lot_expiry_date.isoformat()
                if line.lot_expiry_date
                else None,
                "purchase_line_id": str(line.purchase_line_id) if line.purchase_line_id else None,
                "assets": [
                    a.public_id for a in sorted(line.assets.all(), key=lambda a: a.created_at)
                ],
                "asset_refs": [
                    {"id": str(a.id), "public_id": a.public_id, "state": a.state}
                    for a in sorted(line.assets.all(), key=lambda a: a.created_at)
                ],
                # § 11.4.2: returned to the supplier, and what of this receipt is still here.
                "returned_quantity": str(returned.get(line.id, stock.ZERO)),
                "returnable_quantity": str(
                    layers.get(line.id, stock.ZERO)
                    if line.item.accounting == "batch"
                    else sum(1 for a in line.assets.all() if a.state == "in_stock")
                ),
                "lot_id": str(line.lot_id) if line.lot_id else None,
                "corrections": [
                    {
                        "quantity_before": str(c.quantity_before),
                        "quantity_after": str(c.quantity_after),
                        "unit_cost_before": stock.money_text(c.unit_cost_before, receipt.currency),
                        "unit_cost_after": stock.money_text(c.unit_cost_after, receipt.currency),
                        "reason": c.reason,
                        "created_at": c.created_at.isoformat(),
                    }
                    for c in sorted(line.corrections.all(), key=lambda c: c.created_at)
                ],
            }
        )
    return {
        "id": str(receipt.id),
        "number": receipt.number,
        "version": receipt.version,
        "state": receipt.state,
        "source": receipt.source,
        "context": {
            "id": str(receipt.context_id),
            "location_id": str(receipt.context.location_id),
            "location_name": receipt.context.location.name,
        },
        "supplier": {"id": str(receipt.supplier_id), "name": suppliers.get(receipt.supplier_id, "")}
        if receipt.supplier_id
        else None,
        "purchase": {"id": str(receipt.purchase_id), "number": receipt.purchase.number}
        if receipt.purchase
        else None,
        "document_id": str(receipt.document_id) if receipt.document_id else None,
        "currency": receipt.currency,
        "comment": receipt.comment,
        "received_at": receipt.received_at.isoformat(),
        "created_at": receipt.created_at.isoformat(),
        "posted_at": receipt.posted_at.isoformat() if receipt.posted_at else None,
        "total_cost": stock.money_text(total, receipt.currency),
        # § 11.2.9: with an unknown cost the costs are marked incomplete.
        "cost_incomplete": incomplete,
        "lines": lines,
    }


def _suppliers(company_id: UUID, records: list[Any]) -> dict[UUID, str]:
    return counterparties.names(company_id, {r.supplier_id for r in records if r.supplier_id})


def _receipt_result(status: int, company_id: UUID, receipt_id: UUID) -> CommandResult:
    receipt = stock.get_receipt(company_id, receipt_id)
    return CommandResult(
        status, {"receipt": _receipt_payload(receipt, _suppliers(company_id, [receipt]))}
    )


def _purchase_payload(purchase: PurchaseOrder, suppliers: dict[UUID, str]) -> dict[str, Any]:
    lines = list(purchase.lines.all())
    received = stock.received_by_line([line.id for line in lines])
    payload_lines = []
    waiting = False
    for line in lines:
        got = received.get(line.id, stock.ZERO)
        rest = stock.expected(line, got)
        waiting = waiting or rest > 0
        payload_lines.append(
            {
                "id": str(line.id),
                "item": _item_brief(line.item),
                "ordered_quantity": str(line.ordered_quantity),
                "received_quantity": str(got),
                "cancelled_quantity": str(line.cancelled_quantity),
                "expected_quantity": str(rest),
                "unit_cost": stock.money_text(line.unit_cost, purchase.currency),
            }
        )
    return {
        "id": str(purchase.id),
        "number": purchase.number,
        "version": purchase.version,
        "state": "open" if waiting else "completed",
        "supplier": {
            "id": str(purchase.supplier_id),
            "name": suppliers.get(purchase.supplier_id, ""),
        }
        if purchase.supplier_id
        else None,
        "currency": purchase.currency,
        "comment": purchase.comment,
        "created_at": purchase.created_at.isoformat(),
        "lines": payload_lines,
        "receipts": [
            {"id": str(r.id), "number": r.number, "state": r.state}
            for r in purchase.receipts.order_by("number")
        ],
    }


def _purchase_result(status: int, company_id: UUID, purchase_id: UUID) -> CommandResult:
    purchase = stock.get_purchase(company_id, purchase_id)
    return CommandResult(
        status, {"purchase": _purchase_payload(purchase, _suppliers(company_id, [purchase]))}
    )


# --- Receipts -------------------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def receipt_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        visible = _visible(context.company.id, actor)
        found = stock.receipts_of(context.company.id).filter(context_id__in=visible)
        state = request.GET.get("state", "")
        if state:
            if state not in Receipt.State.values:
                raise errors.validation_error("state")
            found = found.filter(state=state)
        context_id = _uuid(request.GET.get("context_id"), "context_id")
        if context_id:
            found = found.filter(context_id=context_id)
        receipts = list(found[:LIST_LIMIT])
        suppliers = _suppliers(context.company.id, receipts)
        return JsonResponse({"receipts": [_receipt_payload(r, suppliers) for r in receipts]})

    data = parse_json(request)
    receipt_data = _receipt_data(data, _minor_units(context))
    if receipt_data.context_id is None:
        raise errors.validation_error("context_id", code="FIELD_REQUIRED")
    _require_in(
        actor, "warehouse.receive", _context_of(context.company.id, receipt_data.context_id)
    )

    def handler(ctx: CommandContext) -> CommandResult:
        receipt = stock.create_receipt(ctx, context.company, receipt_data)
        return _receipt_result(201, context.company.id, receipt.id)

    return run_and_respond(
        request, "warehouse.create_receipt", actor, receipt_data.as_dict(), handler
    )


@require_http_methods(["GET", "PATCH", "DELETE"])
def receipt_detail(request: HttpRequest, cmp: str, receipt_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    receipt = stock.get_receipt(context.company.id, receipt_id)
    if request.method == "GET":
        _require_in(actor, "warehouse.view", receipt.context)
        payload = _receipt_payload(receipt, _suppliers(context.company.id, [receipt]))
        return JsonResponse({"receipt": payload})

    _require_in(actor, "warehouse.receive", receipt.context)
    if request.method == "DELETE":

        def remove(ctx: CommandContext) -> CommandResult:
            stock.delete_receipt(ctx, context.company.id, receipt_id)
            return CommandResult(200, {"status": "deleted"})

        return run_and_respond(
            request, "warehouse.delete_receipt", actor, {"receipt_id": str(receipt_id)}, remove
        )

    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    receipt_data = _receipt_data(data, _minor_units(context))
    if receipt_data.context_id and receipt_data.context_id != receipt.context_id:
        _require_in(
            actor, "warehouse.receive", _context_of(context.company.id, receipt_data.context_id)
        )

    def handler(ctx: CommandContext) -> CommandResult:
        stock.update_receipt(ctx, context.company, receipt_id, receipt_data, expected_version)
        return _receipt_result(200, context.company.id, receipt_id)

    payload = {
        "receipt_id": str(receipt_id),
        "expected_version": expected_version,
        **receipt_data.as_dict(),
    }
    return run_and_respond(request, "warehouse.update_receipt", actor, payload, handler)


@require_POST
def receipt_post(request: HttpRequest, cmp: str, receipt_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    receipt = stock.get_receipt(context.company.id, receipt_id)
    _require_in(actor, "warehouse.receive", receipt.context)
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        stock.post_receipt(ctx, context.company.id, receipt_id, expected_version)
        return _receipt_result(200, context.company.id, receipt_id)

    payload = {"receipt_id": str(receipt_id), "expected_version": expected_version}
    return run_and_respond(request, "warehouse.post_receipt", actor, payload, handler)


@require_POST
def receipt_line_cost(
    request: HttpRequest, cmp: str, receipt_id: UUID, line_id: UUID
) -> JsonResponse:
    """Entering a cost that was not given (§ 11.2.6). PROPOSAL: the right of receiving in that
    warehouse."""
    context, actor = _actor(request, cmp)
    receipt = stock.get_receipt(context.company.id, receipt_id)
    _require_in(actor, "warehouse.receive", receipt.context)
    unit_cost = stock.money(
        parse_json(request).get("unit_cost"), "unit_cost", _minor_units(context)
    )
    if unit_cost is None:
        raise errors.validation_error("unit_cost", code="FIELD_REQUIRED")

    def handler(ctx: CommandContext) -> CommandResult:
        stock.set_line_cost(ctx, context.company, receipt_id, line_id, unit_cost)
        return _receipt_result(200, context.company.id, receipt_id)

    payload = {"receipt_id": str(receipt_id), "line_id": str(line_id), "unit_cost": str(unit_cost)}
    return run_and_respond(request, "warehouse.enter_receipt_cost", actor, payload, handler)


# --- Purchases ---------------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def purchase_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not (
            access.RightsResolver().has_permission(actor, "warehouse.purchase")
            or access.RightsResolver().has_permission(actor, "warehouse.receive")
        ):
            raise errors.permission_denied("warehouse.purchase")
        purchases = list(stock.purchases_of(context.company.id)[:LIST_LIMIT])
        suppliers = _suppliers(context.company.id, purchases)
        items = [_purchase_payload(p, suppliers) for p in purchases]
        state = request.GET.get("state", "")
        if state:
            items = [p for p in items if p["state"] == state]
        return JsonResponse({"purchases": items})

    _require(actor, "warehouse.purchase")
    data = parse_json(request)
    minor_units = _minor_units(context)
    raw_lines = data.get("lines")
    if not isinstance(raw_lines, list):
        raise errors.validation_error("lines")
    lines = []
    for index, line in enumerate(raw_lines):
        where = f"lines[{index}]"
        if not isinstance(line, dict):
            raise errors.validation_error(where)
        item_id = _uuid(line.get("item_id"), f"{where}.item_id")
        if item_id is None:
            raise errors.validation_error(f"{where}.item_id", code="FIELD_REQUIRED")
        lines.append(
            stock.PurchaseLineData(
                item_id=item_id,
                quantity=catalog.quantity(line.get("quantity"), f"{where}.quantity"),
                unit_cost=stock.money(line.get("unit_cost"), f"{where}.unit_cost", minor_units),
            )
        )
    supplier_id = _uuid(data.get("supplier_id"), "supplier_id")
    comment = get_str(data, "comment", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        purchase = stock.create_purchase(ctx, context.company, supplier_id, comment, tuple(lines))
        return _purchase_result(201, context.company.id, purchase.id)

    payload = {
        "supplier_id": str(supplier_id) if supplier_id else None,
        "comment": comment,
        "lines": [line.as_dict() for line in lines],
    }
    return run_and_respond(request, "warehouse.create_purchase", actor, payload, handler)


@require_GET
def purchase_detail(request: HttpRequest, cmp: str, purchase_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if not (
        access.RightsResolver().has_permission(actor, "warehouse.purchase")
        or access.RightsResolver().has_permission(actor, "warehouse.receive")
    ):
        raise errors.permission_denied("warehouse.purchase")
    return JsonResponse(_purchase_result(200, context.company.id, purchase_id).body)


@require_POST
def purchase_cancel_rest(request: HttpRequest, cmp: str, purchase_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.purchase")
    stock.get_purchase(context.company.id, purchase_id)
    data = parse_json(request)
    raw = data.get("line_ids")
    line_ids: list[UUID] | None = None
    if raw is not None:
        if not isinstance(raw, list):
            raise errors.validation_error("line_ids")
        line_ids = [_uuid(v, "line_ids") or UUID(int=0) for v in raw]

    def handler(ctx: CommandContext) -> CommandResult:
        stock.cancel_rest(ctx, context.company.id, purchase_id, line_ids)
        return _purchase_result(200, context.company.id, purchase_id)

    payload = {
        "purchase_id": str(purchase_id),
        "line_ids": [str(v) for v in line_ids] if line_ids is not None else None,
    }
    return run_and_respond(request, "warehouse.cancel_purchase_rest", actor, payload, handler)


# --- Stock -------------------------------------------------------------------------------------


@require_GET
def stock_list(request: HttpRequest, cmp: str) -> JsonResponse:
    """Items with their stock by warehouse and the joint place tags (§ 11.1.10)."""
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    visible = _visible(context.company.id, actor)
    context_id = _uuid(request.GET.get("context_id"), "context_id")
    if context_id is not None:
        visible = {k: v for k, v in visible.items() if k == context_id}
    found = stock.totals(context.company.id, set(visible))
    # § 11.7.8: an item below its minimum is shown even when none is left.
    thresholds = prices.minimums(context.company.id, set(visible))
    for item_id, context_id in thresholds:
        found.setdefault(item_id, {}).setdefault(
            context_id, {"quantity": stock.ZERO, "tags": set()}
        )
    items = catalog.search(context.company.id, request.GET.get("q", "")[:100], "").filter(
        pk__in=found
    )
    rows = []
    for item in items[:LIST_LIMIT]:
        places = found[item.id]
        quantity = sum((p["quantity"] for p in places.values()), stock.ZERO)
        below = any(
            p["quantity"] < thresholds[(item.id, cid)]
            for cid, p in places.items()
            if (item.id, cid) in thresholds
        )
        rows.append(
            {
                "item": _item_brief(item),
                "quantity": str(quantity),
                "tags": sorted({t for p in places.values() for t in p["tags"]}),
                "below_minimum": below,
                "places": [
                    {
                        "context_id": str(cid),
                        "location_name": visible[cid].location.name,
                        "quantity": str(p["quantity"]),
                        "tags": sorted(p["tags"]),
                        "minimum": str(thresholds[(item.id, cid)])
                        if (item.id, cid) in thresholds
                        else None,
                    }
                    for cid, p in sorted(
                        places.items(), key=lambda kv: visible[kv[0]].location.name
                    )
                ],
            }
        )
    return JsonResponse({"stock": rows})


@require_GET
def item_stock(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    visible = _visible(context.company.id, actor)
    return JsonResponse({"stock": stock.item_stock(context.company.id, item_id, set(visible))})


@require_GET
def item_lots(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    context_id = _uuid(request.GET.get("context_id"), "context_id")
    if context_id is None:
        raise errors.validation_error("context_id", code="FIELD_REQUIRED")
    _require_in(actor, "warehouse.view", _context_of(context.company.id, context_id))
    catalog.get_item(context.company.id, item_id)
    number = request.GET.get("external_number", "")[:100]
    return JsonResponse(
        {"lots": stock.lots_for_receipt(context.company.id, item_id, context_id, number)}
    )


def _tags_command(request: HttpRequest, cmp: str, kind: str, record_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require_in(
        actor, "warehouse.tags.edit", stock.record_context(context.company.id, kind, record_id)
    )
    values = stock.tags(parse_json(request).get("tags"))

    def handler(ctx: CommandContext) -> CommandResult:
        record = stock.set_tags(ctx, context.company.id, kind, record_id, values)
        return CommandResult(200, {"id": str(record.id), "tags": record.tags})

    payload = {"kind": kind, "id": str(record_id), "tags": values}
    return run_and_respond(request, "warehouse.set_tags", actor, payload, handler)


@require_POST
def asset_tags(request: HttpRequest, cmp: str, asset_id: UUID) -> JsonResponse:
    return _tags_command(request, cmp, "asset", asset_id)


@require_POST
def lot_tags(request: HttpRequest, cmp: str, lot_id: UUID) -> JsonResponse:
    return _tags_command(request, cmp, "lot", lot_id)


# --- Supplier articles (§ 11.2.17) -------------------------------------------------------------


def _article_payload(entry: Any, names: dict[UUID, str]) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "supplier": {"id": str(entry.supplier_id), "name": names.get(entry.supplier_id, "")},
        "article": entry.article,
        "supplier_name": entry.supplier_name,
    }


@require_http_methods(["GET", "POST"])
def supplier_articles(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    item = catalog.get_item(context.company.id, item_id)
    if request.method == "GET":
        _require(actor, "warehouse.view")
        entries = list(item.supplier_articles.order_by("article"))
        names = counterparties.names(context.company.id, {e.supplier_id for e in entries})
        return JsonResponse({"articles": [_article_payload(e, names) for e in entries]})

    _require(actor, "warehouse.catalog.manage")
    data = parse_json(request)
    supplier_id = _uuid(data.get("supplier_id"), "supplier_id")
    if supplier_id is None:
        raise errors.validation_error("supplier_id", code="FIELD_REQUIRED")
    article = get_str(data, "article", max_length=100)
    supplier_name = get_str(data, "supplier_name", max_length=200, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        entry = stock.add_supplier_article(
            ctx, context.company.id, item_id, supplier_id, article, supplier_name
        )
        names = counterparties.names(context.company.id, {supplier_id})
        return CommandResult(201, {"article": _article_payload(entry, names)})

    payload = {
        "item_id": str(item_id),
        "supplier_id": str(supplier_id),
        "article": article,
        "supplier_name": supplier_name,
    }
    return run_and_respond(request, "warehouse.add_supplier_article", actor, payload, handler)


@require_http_methods(["DELETE"])
def supplier_article_remove(
    request: HttpRequest, cmp: str, item_id: UUID, entry_id: UUID
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.catalog.manage")

    def handler(ctx: CommandContext) -> CommandResult:
        stock.remove_supplier_article(ctx, context.company.id, item_id, entry_id)
        return CommandResult(200, {"status": "deleted"})

    payload = {"item_id": str(item_id), "entry_id": str(entry_id)}
    return run_and_respond(request, "warehouse.remove_supplier_article", actor, payload, handler)


@require_GET
def supplier_article_lookup(request: HttpRequest, cmp: str) -> JsonResponse:
    """At a purchase the article of the chosen supplier gives the linked item (§ 11.2.17)."""
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    supplier_id = _uuid(request.GET.get("supplier_id"), "supplier_id")
    article = request.GET.get("article", "")[:100]
    if supplier_id is None or not article.strip():
        raise errors.validation_error("article", code="FIELD_REQUIRED")
    entry = stock.find_supplier_article(context.company.id, supplier_id, article)
    if entry is None:
        raise errors.not_found()
    return JsonResponse({"item": _item_brief(entry.item), "supplier_name": entry.supplier_name})
