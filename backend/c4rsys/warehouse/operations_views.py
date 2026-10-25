"""HTTP API of stock operations (ТЗ § 11.1.17, § 11.2.15, § 11.3–11.6, § 23.7.3).

Rights count in the warehouse of a location: «Issue» covers issue, return, consumption and the
manual choice of a lot (decision of 25.10.2026); «Move» is needed in the sending warehouse to
send and in the receiving one to receive; «Write off», «Repack» and «Correct» in their warehouse.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.companies import access
from c4rsys.companies.models import ActivityContext, Company
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import services as fleet
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.warehouse import catalog, operations, stock
from c4rsys.warehouse.models import StockOperation, WarehouseSettings, WriteOffReason
from c4rsys.warehouse.stock_views import (
    COMMENT_MAX,
    _actor,
    _context_of,
    _has,
    _item_brief,
    _minor_units,
    _require,
    _require_in,
    _uuid,
    _visible,
)

LIST_LIMIT = 200
REASON_MAX = 500


# --- Parsing ------------------------------------------------------------------------------------


def _uuids(value: Any, field: str) -> tuple[UUID, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise errors.validation_error(field)
    found = []
    for entry in value:
        parsed = _uuid(entry, field)
        if parsed is None:
            raise errors.validation_error(field)
        found.append(parsed)
    return tuple(found)


def _takes(data: dict[str, Any]) -> tuple[operations.Take, ...]:
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
        quantity = line.get("quantity")
        parsed.append(
            operations.Take(
                item_id=item_id,
                asset_ids=_uuids(line.get("asset_ids"), f"{where}.asset_ids"),
                lot_id=_uuid(line.get("lot_id"), f"{where}.lot_id"),
                packaging_id=_uuid(line.get("packaging_id"), f"{where}.packaging_id"),
                opened_package_id=_uuid(
                    line.get("opened_package_id"), f"{where}.opened_package_id"
                ),
                quantity=None
                if quantity in (None, "")
                else catalog.quantity(quantity, f"{where}.quantity"),
                confirm_expired=line.get("confirm_expired") is True,
            )
        )
    return tuple(parsed)


def _amounts(data: dict[str, Any]) -> tuple[operations.LineAmount, ...]:
    lines = data.get("lines")
    if not isinstance(lines, list):
        raise errors.validation_error("lines")
    parsed = []
    for index, line in enumerate(lines):
        where = f"lines[{index}]"
        if not isinstance(line, dict):
            raise errors.validation_error(where)
        line_id = _uuid(line.get("line_id"), f"{where}.line_id")
        if line_id is None:
            raise errors.validation_error(f"{where}.line_id", code="FIELD_REQUIRED")
        value = line.get("quantity")
        amount = (
            Decimal("0.00") if value in (0, "0") else catalog.quantity(value, f"{where}.quantity")
        )
        parsed.append(operations.LineAmount(line_id, amount))
    return tuple(parsed)


# --- Payloads -----------------------------------------------------------------------------------


def _reason_payload(reason: WriteOffReason) -> dict[str, Any]:
    return {"id": str(reason.id), "code": reason.code, "name": reason.name, "state": reason.state}


def _line_cost(line: Any, currency: str) -> tuple[str | None, bool]:
    """Purchase cost of what the line took; unknown costs make it incomplete (§ 11.2.9)."""
    if line.asset_id:
        cost = line.asset.purchase_cost
        return stock.money_text(cost, currency), cost is not None
    uses = list(line.layer_uses.all())
    if not uses:
        return None, True
    known = all(use.unit_cost is not None for use in uses)
    total = sum(
        (use.quantity * use.unit_cost for use in uses if use.unit_cost is not None), Decimal(0)
    )
    return stock.money_text(total, currency), known


def _operation_payload(
    operation: StockOperation, vehicles: dict[UUID, str], currency: str
) -> dict[str, Any]:
    lines = []
    for line in sorted(operation.lines.all(), key=lambda entry: entry.position):
        cost, known = _line_cost(line, currency)
        lines.append(
            {
                "id": str(line.id),
                "item": _item_brief(line.item),
                "asset": {"id": str(line.asset.id), "public_id": line.asset.public_id}
                if line.asset
                else None,
                "lot": {"id": str(line.lot.id), "public_id": line.lot.public_id}
                if line.lot
                else None,
                "packaging_quantity": str(line.packaging.quantity) if line.packaging else None,
                "opened_package_id": str(line.opened_package_id)
                if line.opened_package_id
                else None,
                "output": line.output,
                "quantity": str(line.quantity),
                "returned_quantity": str(line.returned_quantity),
                "consumed_quantity": str(line.consumed_quantity),
                "outstanding": str(operations.outstanding(line)),
                "received_quantity": str(line.received_quantity),
                "settled_quantity": str(line.settled_quantity),
                "shortage": str(operations.shortage(line))
                if operation.kind == StockOperation.Kind.MOVE
                else "0",
                "target_lot": line.target_lot.public_id if line.target_lot else None,
                "expired_confirmed": line.expired_confirmed,
                "cost": cost,
                "cost_known": known,
            }
        )
    return {
        "id": str(operation.id),
        "number": operation.number,
        "version": operation.version,
        "kind": operation.kind,
        "state": operation.state,
        "context": {
            "id": str(operation.context_id),
            "location_name": operation.context.location.name,
        },
        "target_context": {
            "id": str(operation.target_context_id),
            "location_name": operation.target_context.location.name,
        }
        if operation.target_context
        else None,
        "vehicle": {"id": str(operation.vehicle_id), "name": vehicles.get(operation.vehicle_id, "")}
        if operation.vehicle_id
        else None,
        "reason": _reason_payload(operation.reason) if operation.reason else None,
        "parent_id": str(operation.parent_id) if operation.parent_id else None,
        "receipt": {"id": str(operation.receipt_id), "number": operation.receipt.number}
        if operation.receipt
        else None,
        "children": [
            {"id": str(c.id), "number": c.number, "kind": c.kind}
            for c in operation.children.order_by("number")
        ],
        "photos": [str(p) for p in operation.photos],
        "currency": currency,
        "comment": operation.comment,
        "created_at": operation.created_at.isoformat(),
        "received_at": operation.received_at.isoformat() if operation.received_at else None,
        "lines": lines,
    }


def _vehicles(company_id: UUID, records: list[StockOperation]) -> dict[UUID, str]:
    return fleet.card_names(company_id, {r.vehicle_id for r in records if r.vehicle_id})


def _result(status: int, company_id: UUID, operation_id: UUID) -> CommandResult:
    operation = operations.get_operation(company_id, operation_id)
    currency = (
        Company.objects.filter(pk=company_id).values_list("main_currency_id", flat=True).first()
    )
    payload = _operation_payload(operation, _vehicles(company_id, [operation]), currency or "")
    return CommandResult(status, {"operation": payload})


def _context_from(
    company_id: UUID, data: dict[str, Any], field: str = "context_id"
) -> ActivityContext:
    context_id = _uuid(data.get(field), field)
    if context_id is None:
        raise errors.validation_error(field, code="FIELD_REQUIRED")
    return _context_of(company_id, context_id)


# --- Reading ------------------------------------------------------------------------------------


@require_GET
def operation_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    visible = set(_visible(context.company.id, actor))
    found = operations.operations_of(context.company.id).filter(
        Q(context_id__in=visible) | Q(target_context_id__in=visible)
    )
    kind = request.GET.get("kind", "")
    if kind:
        if kind not in StockOperation.Kind.values:
            raise errors.validation_error("kind")
        found = found.filter(kind=kind)
    state = request.GET.get("state", "")
    if state:
        if state not in StockOperation.State.values:
            raise errors.validation_error("state")
        found = found.filter(state=state)
    vehicle_id = _uuid(request.GET.get("vehicle_id"), "vehicle_id")
    if vehicle_id:
        found = found.filter(vehicle_id=vehicle_id)
    records = list(found[:LIST_LIMIT])
    vehicles = _vehicles(context.company.id, records)
    currency = context.company.main_currency_id or ""
    return JsonResponse(
        {"operations": [_operation_payload(o, vehicles, currency) for o in records]}
    )


@require_GET
def operation_detail(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    operation = operations.get_operation(context.company.id, operation_id)
    places = [operation.context] + ([operation.target_context] if operation.target_context else [])
    if not any(_has(actor, "warehouse.view", place) for place in places):
        raise errors.permission_denied("warehouse.view")
    return JsonResponse(_result(200, context.company.id, operation_id).body)


@require_GET
def item_sources(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    warehouse = _context_from(context.company.id, request.GET)
    _require_in(actor, "warehouse.view", warehouse)
    return JsonResponse(operations.sources(context.company.id, item_id, warehouse.id))


@require_GET
def item_issued(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    visible = set(_visible(context.company.id, actor))
    rows = operations.issued_of_item(context.company.id, item_id, visible)
    names = fleet.card_names(context.company.id, {UUID(r["vehicle_id"]) for r in rows})
    for row in rows:
        row["vehicle_name"] = names.get(UUID(row["vehicle_id"]), "")
    return JsonResponse({"issued": rows})


@require_GET
def company_vehicles(request: HttpRequest, cmp: str) -> JsonResponse:
    """Vehicles of the company that materials may be issued to (decision of 25.10.2026)."""
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.issue")
    found = [
        card
        for card in fleet.cards_of(context.company.id)
        if fleet.company_vehicle(context.company.id, card.id) is not None
    ]
    return JsonResponse({"vehicles": [{"id": str(c.id), "name": c.name} for c in found]})


# --- Issue, return, consumption -----------------------------------------------------------------


def _issue_command(request: HttpRequest, cmp: str, kind: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    warehouse = _context_from(context.company.id, data)
    _require_in(actor, "warehouse.issue", warehouse)
    vehicle_id = _uuid(data.get("vehicle_id"), "vehicle_id")
    takes = _takes(data)
    comment = get_str(data, "comment", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        operation = operations.issue(
            ctx, context.company, kind, warehouse.id, vehicle_id, takes, comment
        )
        return _result(201, context.company.id, operation.id)

    payload = {
        "kind": kind,
        "context_id": str(warehouse.id),
        "vehicle_id": str(vehicle_id) if vehicle_id else None,
        "lines": [t.as_dict() for t in takes],
        "comment": comment,
    }
    return run_and_respond(request, f"warehouse.{kind}", actor, payload, handler)


@require_POST
def issue(request: HttpRequest, cmp: str) -> JsonResponse:
    return _issue_command(request, cmp, StockOperation.Kind.ISSUE)


@require_POST
def consume(request: HttpRequest, cmp: str) -> JsonResponse:
    return _issue_command(request, cmp, StockOperation.Kind.CONSUME)


def _issued_command(
    request: HttpRequest, cmp: str, operation_id: UUID, returning: bool
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    operation = operations.get_operation(context.company.id, operation_id)
    _require_in(actor, "warehouse.issue", operation.context)
    amounts = _amounts(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        if returning:
            operations.return_issued(ctx, context.company.id, operation_id, amounts)
        else:
            operations.consume_issued(ctx, context.company.id, operation_id, amounts)
        return _result(200, context.company.id, operation_id)

    payload = {"operation_id": str(operation_id), "lines": [a.as_dict() for a in amounts]}
    name = "warehouse.return_issued" if returning else "warehouse.consume_issued"
    return run_and_respond(request, name, actor, payload, handler)


@require_POST
def return_issued(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    return _issued_command(request, cmp, operation_id, returning=True)


@require_POST
def consume_issued(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    return _issued_command(request, cmp, operation_id, returning=False)


# --- Moves ------------------------------------------------------------------------------------


@require_POST
def move(request: HttpRequest, cmp: str) -> JsonResponse:
    """Sent, or sent and received in one action by one person (§ 11.5.2)."""
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    source = _context_from(context.company.id, data)
    target = _context_from(context.company.id, data, "target_context_id")
    _require_in(actor, "warehouse.move", source)
    one_step = data.get("one_step") is True
    if one_step:
        _require_in(actor, "warehouse.move", target)
    takes = _takes(data)
    comment = get_str(data, "comment", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        operation = operations.send(ctx, context.company, source.id, target.id, takes, comment)
        if one_step:
            everything = tuple(
                operations.LineAmount(line.id, line.quantity) for line in operation.lines.all()
            )
            operations.receive(ctx, context.company.id, operation.id, everything)
        return _result(201, context.company.id, operation.id)

    payload = {
        "context_id": str(source.id),
        "target_context_id": str(target.id),
        "one_step": one_step,
        "lines": [t.as_dict() for t in takes],
        "comment": comment,
    }
    return run_and_respond(request, "warehouse.move", actor, payload, handler)


@require_POST
def receive(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    operation = operations.get_operation(context.company.id, operation_id)
    if operation.target_context is None:
        raise errors.not_found()
    _require_in(actor, "warehouse.move", operation.target_context)
    amounts = _amounts(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        operations.receive(ctx, context.company.id, operation_id, amounts)
        return _result(200, context.company.id, operation_id)

    payload = {"operation_id": str(operation_id), "lines": [a.as_dict() for a in amounts]}
    return run_and_respond(request, "warehouse.receive_move", actor, payload, handler)


@require_POST
def settle(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    """A shortage goes back to the sender («Move» there) or is written off («Write off»)."""
    context, actor = _actor(request, cmp)
    operation = operations.get_operation(context.company.id, operation_id)
    data = parse_json(request)
    action = get_str(data, "action", max_length=20)
    if action not in ("return", "write_off"):
        raise errors.validation_error("action")
    _require_in(
        actor, "warehouse.move" if action == "return" else "warehouse.write_off", operation.context
    )
    line_id = _uuid(data.get("line_id"), "line_id")
    if line_id is None:
        raise errors.validation_error("line_id", code="FIELD_REQUIRED")
    reason_id = _uuid(data.get("reason_id"), "reason_id")
    comment = get_str(data, "comment", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        operations.settle_shortage(
            ctx, context.company, operation_id, line_id, action, reason_id, comment
        )
        return _result(200, context.company.id, operation_id)

    payload = {
        "operation_id": str(operation_id),
        "line_id": str(line_id),
        "action": action,
        "reason_id": str(reason_id) if reason_id else None,
        "comment": comment,
    }
    return run_and_respond(request, "warehouse.settle_shortage", actor, payload, handler)


# --- Write-off and repacking ----------------------------------------------------------------------


@require_POST
def write_off(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    warehouse = _context_from(context.company.id, data)
    _require_in(actor, "warehouse.write_off", warehouse)
    reason_id = _uuid(data.get("reason_id"), "reason_id")
    comment = get_str(data, "comment", max_length=COMMENT_MAX, required=False)
    photos = _uuids(data.get("photos"), "photos")
    takes = _takes(data)

    def handler(ctx: CommandContext) -> CommandResult:
        operation = operations.write_off(
            ctx, context.company, warehouse.id, reason_id, comment, photos, takes
        )
        return _result(201, context.company.id, operation.id)

    payload = {
        "context_id": str(warehouse.id),
        "reason_id": str(reason_id) if reason_id else None,
        "comment": comment,
        "photos": [str(p) for p in photos],
        "lines": [t.as_dict() for t in takes],
    }
    return run_and_respond(request, "warehouse.write_off", actor, payload, handler)


@require_POST
def repack(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    warehouse = _context_from(context.company.id, data)
    _require_in(actor, "warehouse.repack", warehouse)
    source = data.get("source")
    if not isinstance(source, dict):
        raise errors.validation_error("source")
    raw_outputs = data.get("outputs")
    if not isinstance(raw_outputs, list):
        raise errors.validation_error("outputs")
    outputs = []
    for index, entry in enumerate(raw_outputs):
        if not isinstance(entry, dict):
            raise errors.validation_error(f"outputs[{index}]")
        outputs.append(
            (
                _uuid(entry.get("packaging_id"), f"outputs[{index}].packaging_id"),
                catalog.quantity(entry.get("quantity"), f"outputs[{index}].quantity"),
            )
        )
    loss = data.get("loss")
    repack_data = operations.RepackData(
        context_id=warehouse.id,
        lot_id=_uuid(data.get("lot_id"), "lot_id"),
        source_packaging_id=_uuid(source.get("packaging_id"), "source.packaging_id"),
        source_opened_package_id=_uuid(source.get("opened_package_id"), "source.opened_package_id"),
        source_quantity=catalog.quantity(source.get("quantity"), "source.quantity"),
        outputs=tuple(outputs),
        loss=Decimal("0.00") if loss in (None, "", 0, "0") else catalog.quantity(loss, "loss"),
        loss_reason_id=_uuid(data.get("loss_reason_id"), "loss_reason_id"),
        comment=get_str(data, "comment", max_length=COMMENT_MAX, required=False),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        operation = operations.repack(ctx, context.company, repack_data)
        return _result(201, context.company.id, operation.id)

    return run_and_respond(request, "warehouse.repack", actor, repack_data.as_dict(), handler)


# --- Correction of a posted receipt -------------------------------------------------------------


@require_POST
def correct_receipt_line(
    request: HttpRequest, cmp: str, receipt_id: UUID, line_id: UUID
) -> JsonResponse:
    from c4rsys.warehouse.stock_views import _receipt_result

    context, actor = _actor(request, cmp)
    receipt = stock.get_receipt(context.company.id, receipt_id)
    _require_in(actor, "warehouse.correct", receipt.context)
    data = parse_json(request)
    value = data.get("quantity")
    quantity = None if value in (None, "") else catalog.quantity(value, "quantity")
    change_cost = "unit_cost" in data
    unit_cost = (
        stock.money(data.get("unit_cost"), "unit_cost", _minor_units(context))
        if change_cost
        else None
    )
    remove = _uuids(data.get("remove_asset_ids"), "remove_asset_ids")
    reason = get_str(data, "reason", max_length=REASON_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        operations.correct_receipt_line(
            ctx,
            context.company,
            receipt_id,
            line_id,
            quantity,
            unit_cost,
            change_cost,
            remove,
            reason,
        )
        return _receipt_result(200, context.company.id, receipt_id)

    payload = {
        "receipt_id": str(receipt_id),
        "line_id": str(line_id),
        "quantity": str(quantity) if quantity is not None else None,
        "unit_cost": str(unit_cost) if unit_cost is not None else None,
        "change_cost": change_cost,
        "remove_asset_ids": [str(a) for a in remove],
        "reason": reason,
    }
    return run_and_respond(request, "warehouse.correct_receipt", actor, payload, handler)


# --- Reasons and settings -----------------------------------------------------------------------


def _manager(actor: Actor) -> None:
    """PROPOSAL: reasons and the expired-lot policy are set with the right of company settings."""
    if not access.RightsResolver().has_permission(actor, "company.settings"):
        raise errors.permission_denied("company.settings")


@require_http_methods(["GET", "POST"])
def reason_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require(actor, "warehouse.view")
        reasons = operations.reasons_of(context.company.id)
        return JsonResponse({"reasons": [_reason_payload(r) for r in reasons]})
    _manager(actor)
    name = get_str(parse_json(request), "name", max_length=100)

    def handler(ctx: CommandContext) -> CommandResult:
        reason = operations.add_reason(ctx, context.company.id, name)
        return CommandResult(201, {"reason": _reason_payload(reason)})

    return run_and_respond(request, "warehouse.add_reason", actor, {"name": name}, handler)


@require_http_methods(["PATCH"])
def reason_detail(request: HttpRequest, cmp: str, reason_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _manager(actor)
    data = parse_json(request)
    name = get_str(data, "name", max_length=100, required=False) if "name" in data else None
    archived = data.get("archived") if isinstance(data.get("archived"), bool) else None
    if name is None and archived is None:
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")

    def handler(ctx: CommandContext) -> CommandResult:
        reason = operations.change_reason(
            ctx, context.company.id, reason_id, name=name, archived=archived
        )
        return CommandResult(200, {"reason": _reason_payload(reason)})

    payload = {"reason_id": str(reason_id), "name": name, "archived": archived}
    return run_and_respond(request, "warehouse.change_reason", actor, payload, handler)


def _settings_payload(found: WarehouseSettings) -> dict[str, Any]:
    return {"expired_policy": found.expired_policy, "version": found.version}


@require_http_methods(["GET", "PUT"])
def settings(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require(actor, "warehouse.view")
        return JsonResponse(
            {"settings": _settings_payload(operations.settings_of(context.company.id))}
        )
    _manager(actor)
    data = parse_json(request)
    policy = get_str(data, "expired_policy", max_length=10)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        found = operations.set_expired_policy(ctx, context.company.id, policy, expected_version)
        return CommandResult(200, {"settings": _settings_payload(found)})

    payload = {"expired_policy": policy, "expected_version": expected_version}
    return run_and_respond(request, "warehouse.change_settings", actor, payload, handler)


# --- Return to the supplier (§ 11.4.2) ----------------------------------------------------------


@require_POST
def return_to_supplier(request: HttpRequest, cmp: str, receipt_id: UUID) -> JsonResponse:
    """PROPOSAL: a return to the supplier needs the right of receiving in that warehouse."""
    context, actor = _actor(request, cmp)
    receipt = stock.get_receipt(context.company.id, receipt_id)
    _require_in(actor, "warehouse.receive", receipt.context)
    data = parse_json(request)
    raw = data.get("lines")
    if not isinstance(raw, list):
        raise errors.validation_error("lines")
    lines = []
    for index, entry in enumerate(raw):
        where = f"lines[{index}]"
        if not isinstance(entry, dict):
            raise errors.validation_error(where)
        line_id = _uuid(entry.get("receipt_line_id"), f"{where}.receipt_line_id")
        if line_id is None:
            raise errors.validation_error(f"{where}.receipt_line_id", code="FIELD_REQUIRED")
        value = entry.get("quantity")
        lines.append(
            operations.ReturnLine(
                receipt_line_id=line_id,
                asset_ids=_uuids(entry.get("asset_ids"), f"{where}.asset_ids"),
                packaging_id=_uuid(entry.get("packaging_id"), f"{where}.packaging_id"),
                opened_package_id=_uuid(
                    entry.get("opened_package_id"), f"{where}.opened_package_id"
                ),
                quantity=None
                if value in (None, "")
                else catalog.quantity(value, f"{where}.quantity"),
            )
        )
    comment = get_str(data, "comment", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        operation = operations.return_to_supplier(
            ctx, context.company, receipt_id, tuple(lines), comment
        )
        return _result(201, context.company.id, operation.id)

    payload = {
        "receipt_id": str(receipt_id),
        "lines": [line.as_dict() for line in lines],
        "comment": comment,
    }
    return run_and_respond(request, "warehouse.return_to_supplier", actor, payload, handler)
