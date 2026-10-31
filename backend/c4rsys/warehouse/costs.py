"""Costs of materials consumed on a vehicle or in a work (ТЗ § 11.2.5, § 11.2.9, § 11.4.4).

The actual consumption of a material or an installed part links its purchase cost to the vehicle
and to the work; it creates no second payment — the supplier is paid apart. A material without a
known purchase cost may be issued and used: its quantity counts in full and the costs are marked
incomplete until the cost is entered; a cost entered later reaches what was already used.

Only consumption counts: what is issued and not consumed yet, or returned, is no cost. Of an issue
line the consumed part takes the earlier receipt layers first (§ 11.2.13).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from c4rsys.warehouse import operations
from c4rsys.warehouse.models import OperationLine, StockOperation
from c4rsys.warehouse.parts import asset_cost_at

ZERO = Decimal(0)


@dataclass(frozen=True)
class LineCost:
    line_id: UUID
    consumed: Decimal
    cost: Decimal  # of what is known
    known: bool  # every consumed part has a known cost


def consumed_of(line: OperationLine) -> Decimal:
    kind = line.operation.kind
    if kind == StockOperation.Kind.ISSUE:
        return line.consumed_quantity
    if kind == StockOperation.Kind.CONSUME:
        return operations.effective(line)
    return ZERO


def line_cost(line: OperationLine) -> LineCost:
    consumed = consumed_of(line)
    if consumed <= 0:
        return LineCost(line.id, ZERO, ZERO, True)
    if line.asset_id is not None and line.asset is not None:
        cost = asset_cost_at(line.asset, line.operation.created_at)
        return LineCost(line.id, consumed, cost or ZERO, cost is not None)
    left = consumed
    total = ZERO
    known = True
    for use in sorted(line.layer_uses.all(), key=lambda u: (u.layer.received_at, u.id)):
        part = min(use.quantity, left)
        if part <= 0:
            continue
        if use.unit_cost is None:
            known = False
        else:
            total += part * use.unit_cost
        left -= part
        if left <= 0:
            break
    if left > 0:
        known = False
    return LineCost(line.id, consumed, total, known)


def _lines(company_id: UUID) -> Any:
    return (
        OperationLine.objects.filter(company_id=company_id)
        .select_related("operation__context__location", "item", "asset__receipt_line", "lot")
        .prefetch_related("layer_uses__layer", "asset__installations")
    )


def of_lines(company_id: UUID, line_ids: list[UUID]) -> dict[UUID, LineCost]:
    return {line.id: line_cost(line) for line in _lines(company_id).filter(pk__in=line_ids)}


def of_vehicle(company_id: UUID, card_id: UUID, currency: str) -> dict[str, Any]:
    """Every consumption on the vehicle: by warehouse operations and by service works, at the
    purchase costs in the main currency of the company."""
    from c4rsys.warehouse.stock import money_text

    rows = []
    total = ZERO
    incomplete = False
    for line in (
        _lines(company_id)
        .filter(
            operation__vehicle_id=card_id,
            operation__kind__in=(StockOperation.Kind.ISSUE, StockOperation.Kind.CONSUME),
        )
        .order_by("operation__created_at", "position")
    ):
        found = line_cost(line)
        if found.consumed <= 0:
            continue
        total += found.cost
        incomplete = incomplete or not found.known
        rows.append(
            {
                "line_id": str(line.id),
                "operation_id": str(line.operation_id),
                "operation_number": line.operation.number,
                "at": line.operation.created_at.isoformat(),
                "location_name": line.operation.context.location.name,
                "item": {"id": str(line.item_id), "name": line.item.name, "unit": line.item.unit},
                "what": line.asset.public_id
                if line.asset
                else (line.lot.public_id if line.lot else ""),
                "consumed": str(found.consumed),
                "cost": money_text(found.cost, currency),
                "cost_known": found.known,
            }
        )
    return {
        "lines": rows,
        "total": money_text(total, currency),
        "currency": currency,
        "incomplete": incomplete,
    }
