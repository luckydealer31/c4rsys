"""HTTP API of inventories (ТЗ § 11.2.1, § 11.6, § 23.7.3).

Counting needs «Count» in the warehouse; confirming or rejecting the adjustment needs the separate
«Confirm adjustments» there (§ 11.6.3). The author may confirm their own count with that right;
the top-authority director passes any check.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts import services as accounts
from c4rsys.companies.views import run_and_respond
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json
from c4rsys.warehouse import catalog, inventory
from c4rsys.warehouse.models import InventoryCount, InventoryLine
from c4rsys.warehouse.operations_views import _context_from, _uuids
from c4rsys.warehouse.stock_views import (
    COMMENT_MAX,
    _actor,
    _item_brief,
    _require,
    _require_in,
    _uuid,
    _visible,
)

LIST_LIMIT = 200


def _line_payload(line: InventoryLine) -> dict[str, Any]:
    counted = line.counted_quantity
    book = line.book_quantity
    return {
        "id": str(line.id),
        "kind": line.kind,
        "item": _item_brief(line.item) if line.item else None,
        "asset": line.asset.public_id if line.asset else None,
        "lot": line.lot.public_id if line.lot else None,
        "packaging_quantity": str(line.packaging.quantity) if line.packaging else None,
        "opened_package_id": str(line.opened_package_id) if line.opened_package_id else None,
        "book_quantity": str(book) if book is not None else None,
        "counted_quantity": str(counted) if counted is not None else None,
        "discrepancy": str(counted - book) if counted is not None and book is not None else None,
        "counted_at": line.counted_at.isoformat() if line.counted_at else None,
        "description": line.description,
        "identifiers": line.identifiers,
        "applied_delta": str(line.applied_delta) if line.applied_delta is not None else None,
        "created_asset": line.created_asset.public_id if line.created_asset else None,
    }


def _payload(count: InventoryCount) -> dict[str, Any]:
    lines = sorted(count.lines.all(), key=lambda line: line.position)
    users = {
        u
        for u in [
            count.created_by_user_id,
            count.submitted_by_user_id,
            count.decided_by_user_id,
            *[line.counted_by_user_id for line in lines],
        ]
        if u
    }
    names = accounts.names(users)
    counters = sorted(
        {names.get(line.counted_by_user_id, "") for line in lines if line.counted_by_user_id}
    )
    return {
        "id": str(count.id),
        "number": count.number,
        "version": count.version,
        "state": count.state,
        "context": {"id": str(count.context_id), "location_name": count.context.location.name},
        "comment": count.comment,
        "created_at": count.created_at.isoformat(),
        "created_by": names.get(count.created_by_user_id, ""),
        "counted_by": counters,
        "submitted_at": count.submitted_at.isoformat() if count.submitted_at else None,
        "decided_at": count.decided_at.isoformat() if count.decided_at else None,
        "decided_by": names.get(count.decided_by_user_id, "") if count.decided_by_user_id else None,
        "decision_reason": count.decision_reason,
        "lines": [_line_payload(line) for line in lines],
    }


def _result(status: int, company_id: UUID, count_id: UUID) -> CommandResult:
    return CommandResult(status, {"inventory": _payload(inventory.get_count(company_id, count_id))})


@require_http_methods(["GET", "POST"])
def count_list(request: HttpRequest, cmp: str) -> JsonResponse:
    """The history shows confirmed and unconfirmed inventories with their state (§ 11.6.3)."""
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require(actor, "warehouse.view")
        visible = set(_visible(context.company.id, actor))
        counts = list(
            inventory.counts_of(context.company.id).filter(context_id__in=visible)[:LIST_LIMIT]
        )
        return JsonResponse({"inventories": [_payload(c) for c in counts]})
    data = parse_json(request)
    warehouse = _context_from(context.company.id, data)
    _require_in(actor, "warehouse.count", warehouse)
    comment = get_str(data, "comment", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        count = inventory.create(ctx, context.company, warehouse.id, comment)
        return _result(201, context.company.id, count.id)

    payload = {"context_id": str(warehouse.id), "comment": comment}
    return run_and_respond(request, "warehouse.create_inventory", actor, payload, handler)


def _count_for(
    request: HttpRequest, cmp: str, count_id: UUID, permission: str
) -> tuple[Any, Any, InventoryCount]:
    context, actor = _actor(request, cmp)
    count = inventory.get_count(context.company.id, count_id)
    _require_in(actor, permission, count.context)
    return context, actor, count


@require_GET
def count_detail(request: HttpRequest, cmp: str, count_id: UUID) -> JsonResponse:
    context, _, count = _count_for(request, cmp, count_id, "warehouse.view")
    return JsonResponse({"inventory": _payload(count)})


@require_POST
def count_items(request: HttpRequest, cmp: str, count_id: UUID) -> JsonResponse:
    context, actor, _ = _count_for(request, cmp, count_id, "warehouse.count")
    data = parse_json(request)
    item_ids = None if data.get("all") is True else _uuids(data.get("item_ids"), "item_ids")

    def handler(ctx: CommandContext) -> CommandResult:
        inventory.add_items(ctx, context.company.id, count_id, item_ids)
        return _result(200, context.company.id, count_id)

    payload = {
        "count_id": str(count_id),
        "item_ids": [str(i) for i in item_ids] if item_ids is not None else None,
    }
    return run_and_respond(request, "warehouse.inventory_items", actor, payload, handler)


@require_POST
def count_counted(request: HttpRequest, cmp: str, count_id: UUID) -> JsonResponse:
    context, actor, _ = _count_for(request, cmp, count_id, "warehouse.count")
    lines = parse_json(request).get("lines")
    if not isinstance(lines, list):
        raise errors.validation_error("lines")
    entries = []
    for index, entry in enumerate(lines):
        if not isinstance(entry, dict):
            raise errors.validation_error(f"lines[{index}]")
        line_id = _uuid(entry.get("line_id"), f"lines[{index}].line_id")
        if line_id is None:
            raise errors.validation_error(f"lines[{index}].line_id", code="FIELD_REQUIRED")
        value = entry.get("quantity")
        quantity = (
            Decimal("0.00")
            if value in (0, "0")
            else catalog.quantity(value, f"lines[{index}].quantity")
        )
        entries.append(inventory.Counted(line_id, quantity))

    def handler(ctx: CommandContext) -> CommandResult:
        inventory.set_counted(ctx, context.company.id, count_id, tuple(entries))
        return _result(200, context.company.id, count_id)

    payload = {"count_id": str(count_id), "lines": [e.as_dict() for e in entries]}
    return run_and_respond(request, "warehouse.inventory_counted", actor, payload, handler)


@require_POST
def count_found(request: HttpRequest, cmp: str, count_id: UUID) -> JsonResponse:
    context, actor, _ = _count_for(request, cmp, count_id, "warehouse.count")
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=20)
    item_id = _uuid(data.get("item_id"), "item_id")
    asset_public_id = get_str(data, "asset_public_id", max_length=40, required=False)
    description = get_str(data, "description", max_length=500, required=False)
    identifiers = get_str(data, "identifiers", max_length=200, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        inventory.add_found(
            ctx,
            context.company.id,
            count_id,
            kind,
            item_id,
            asset_public_id,
            description,
            identifiers,
        )
        return _result(200, context.company.id, count_id)

    payload = {
        "count_id": str(count_id),
        "kind": kind,
        "item_id": str(item_id) if item_id else None,
        "asset_public_id": asset_public_id,
        "description": description,
        "identifiers": identifiers,
    }
    return run_and_respond(request, "warehouse.inventory_found", actor, payload, handler)


@require_http_methods(["DELETE"])
def count_line_remove(
    request: HttpRequest, cmp: str, count_id: UUID, line_id: UUID
) -> JsonResponse:
    context, actor, _ = _count_for(request, cmp, count_id, "warehouse.count")

    def handler(ctx: CommandContext) -> CommandResult:
        inventory.remove_line(ctx, context.company.id, count_id, line_id)
        return _result(200, context.company.id, count_id)

    payload = {"count_id": str(count_id), "line_id": str(line_id)}
    return run_and_respond(request, "warehouse.inventory_remove_line", actor, payload, handler)


@require_POST
def count_submit(request: HttpRequest, cmp: str, count_id: UUID) -> JsonResponse:
    context, actor, _ = _count_for(request, cmp, count_id, "warehouse.count")

    def handler(ctx: CommandContext) -> CommandResult:
        inventory.submit(ctx, context.company.id, count_id)
        return _result(200, context.company.id, count_id)

    return run_and_respond(
        request, "warehouse.inventory_submit", actor, {"count_id": str(count_id)}, handler
    )


def _decision(request: HttpRequest, cmp: str, count_id: UUID, confirming: bool) -> JsonResponse:
    context, actor, _ = _count_for(request, cmp, count_id, "warehouse.adjust.confirm")
    reason = get_str(parse_json(request), "reason", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        if confirming:
            inventory.confirm(ctx, context.company, count_id, reason)
        else:
            inventory.reject(ctx, context.company.id, count_id, reason)
        return _result(200, context.company.id, count_id)

    name = "warehouse.inventory_confirm" if confirming else "warehouse.inventory_reject"
    return run_and_respond(
        request, name, actor, {"count_id": str(count_id), "reason": reason}, handler
    )


@require_POST
def count_confirm(request: HttpRequest, cmp: str, count_id: UUID) -> JsonResponse:
    return _decision(request, cmp, count_id, confirming=True)


@require_POST
def count_reject(request: HttpRequest, cmp: str, count_id: UUID) -> JsonResponse:
    return _decision(request, cmp, count_id, confirming=False)
