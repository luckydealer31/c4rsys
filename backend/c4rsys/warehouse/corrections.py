"""Correction of posted issues, consumptions, write-offs and moves (ТЗ § 11.6.2).

A posted operation is corrected by a separate action with a reason. The stock never goes below
zero and later movements that prevent the correction are shown; an allowed correction changes the
stock and keeps the original record, the change and its author. The posted quantity of the line
stays; `cancelled_quantity` holds what corrections took back.

PROPOSAL (block 15):
* A correction lowers the quantity of a line, down to zero; more is taken by a new operation.
* Issue: down to what is already returned or consumed; consumption and write-off: what was taken
  comes back into the same lot, form and unit; a move only while it is in transit — the part goes
  back to the sending warehouse; a received move is corrected by a move back.
* Not corrected here: a sale (§ 11.9.5), a return to the supplier, a repacking and the write-offs
  made by a repacking or a settled shortage, a line of a service work (it is corrected in the
  work) and a part installed on a vehicle.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse import operations
from c4rsys.warehouse.models import (
    Asset,
    OperationCorrection,
    OperationLine,
    StockMovement,
    StockOperation,
)

ZERO = Decimal("0.00")
KINDS = (
    StockOperation.Kind.ISSUE,
    StockOperation.Kind.CONSUME,
    StockOperation.Kind.WRITE_OFF,
    StockOperation.Kind.MOVE,
)
# Modules whose records hold an operation line (a material of a service work) register a check:
# line ids -> the ids among them that are not corrected in the warehouse.
LineGuard = Callable[[list[UUID]], set[UUID]]
_guards: list[LineGuard] = []


def register_line_guard(guard: LineGuard) -> None:
    _guards.append(guard)


def guarded(line_ids: list[UUID]) -> set[UUID]:
    found: set[UUID] = set()
    for guard in _guards:
        found |= guard(line_ids)
    return found


def correctable(operation: StockOperation, line: OperationLine) -> Decimal:
    """How much a correction may still take back from the line; zero — nothing."""
    if operation.kind not in KINDS or operation.parent_id is not None or line.output:
        return ZERO
    if operation.kind == StockOperation.Kind.MOVE:
        if operation.state != StockOperation.State.IN_TRANSIT:
            return ZERO
        return operations.effective(line)
    if operation.kind == StockOperation.Kind.ISSUE:
        return operations.outstanding(line)
    return operations.effective(line)


def _blockers(line: OperationLine) -> list[str]:
    """What prevents taking a unit back: its later life."""
    if line.asset_id is None:
        return []
    asset = Asset.objects.select_for_update().get(pk=line.asset_id)
    expected = {
        StockOperation.Kind.ISSUE: Asset.State.ISSUED,
        StockOperation.Kind.CONSUME: Asset.State.CONSUMED,
        StockOperation.Kind.WRITE_OFF: Asset.State.WRITTEN_OFF,
        StockOperation.Kind.MOVE: Asset.State.IN_TRANSIT,
    }[StockOperation.Kind(line.operation.kind)]
    if asset.state != expected:
        return [f"asset:{asset.public_id}:{asset.state}"]
    return []


def correct_line(
    ctx: CommandContext,
    company_id: UUID,
    operation_id: UUID,
    line_id: UUID,
    quantity: Decimal,
    reason: str,
) -> OperationLine:
    """Sets the corrected quantity of a line (lower than now). The difference comes back to the
    stock of the warehouse it was taken from, into the same lot, form and unit."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    operation = (
        StockOperation.objects.select_for_update()
        .select_related("context")
        .filter(company_id=company_id, pk=operation_id)
        .first()
    )
    if operation is None:
        raise errors.not_found()
    line = (
        OperationLine.objects.select_for_update(of=("self",))
        .select_related("lot", "packaging", "opened_package", "operation")
        .filter(operation=operation, pk=line_id)
        .first()
    )
    if line is None:
        raise errors.not_found()
    if line.id in guarded([line.id]):
        raise errors.business_rule("CORRECTION_IN_SERVICE_WORK")
    room = correctable(operation, line)
    if room <= 0:
        raise errors.business_rule(
            "CORRECTION_NOT_SUPPORTED", kind=operation.kind, state=operation.state
        )
    before = operations.effective(line)
    if quantity < 0 or quantity >= before:
        raise errors.validation_error("quantity", code="CORRECTION_MUST_DECREASE")
    amount = before - quantity
    if line.asset_id and amount != 1:
        raise errors.validation_error("quantity", code="QUANTITY_WHOLE_UNITS")
    if amount > room:
        # Returned or consumed parts of an issue are corrected by their own operations.
        raise errors.business_rule(
            "CORRECTION_BLOCKED", available=str(room), operations=[operation.number]
        )
    blockers = _blockers(line)
    if blockers:
        raise errors.business_rule("CORRECTION_BLOCKED", blockers=blockers)
    context = operation.context
    if line.asset_id:
        asset = Asset.objects.select_for_update().get(pk=line.asset_id)
        asset.state = Asset.State.IN_STOCK
        asset.vehicle = None
        asset.save(update_fields=["state", "vehicle"])
        operations._move(ctx, StockMovement.Kind.CORRECTION, context, line, Decimal(1), asset=asset)
    else:
        lot = operations._lot_of(line)
        operations._restore_layers(line, amount)
        piece = operations._put_back(lot, line.packaging, line.opened_package, amount)
        operations._move(
            ctx, StockMovement.Kind.CORRECTION, context, line, amount, lot=lot, piece=piece
        )
    line.cancelled_quantity += amount
    line.save(update_fields=["cancelled_quantity"])
    correction = OperationCorrection.objects.create(
        company_id=company_id,
        line=line,
        quantity_before=before,
        quantity_after=quantity,
        reason=reason,
        user_id=ctx.actor.user_id,
    )
    if operation.kind == StockOperation.Kind.MOVE and all(
        operations.effective(other) <= 0 for other in operation.lines.all()
    ):
        operation.state = StockOperation.State.CANCELLED
        operation.received_at = timezone.now()
    operation.version += 1
    operation.save(update_fields=["state", "received_at", "version"])
    ctx.audit(
        "warehouse.operation_corrected",
        object_type="warehouse_operation",
        object_id=operation.id,
        before={"line": str(line.id), "quantity": str(before)},
        after={
            "line": str(line.id),
            "quantity": str(quantity),
            "correction": str(correction.id),
            "state": operation.state,
        },
        reason=reason,
    )
    return line


def corrections_payload(line: OperationLine) -> list[dict[str, Any]]:
    return [
        {
            "quantity_before": str(c.quantity_before),
            "quantity_after": str(c.quantity_after),
            "reason": c.reason,
            "user_id": str(c.user_id),
            "created_at": c.created_at.isoformat(),
        }
        for c in sorted(line.corrections.all(), key=lambda c: c.created_at)
    ]
