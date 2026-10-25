"""Operations with the stock (ТЗ § 11.1.17, § 11.2.5, § 11.2.15, § 11.3–11.6; customer's
decisions of 25.10.2026).

* Issue with a return possible and actual consumption are kept apart (§ 11.4.1). Until service
  works exist both go to a vehicle of the company. A return goes into the same lot (§ 11.4.5).
* A move between warehouses goes «sent → in transit → received» or in one action (§ 11.5). A
  received part of a lot becomes a child lot in the receiving warehouse (§ 11.3.3). A shortage
  stays until it is written off or returned to the sender.
* A write-off needs a reason (§ 11.3.4); a repacking keeps the lot and accounts for every unit
  of the source in packages, the rest and an explicit write-off of losses (§ 11.3.3).
* An outgoing quantity of a lot uses the earlier receipt layers first (§ 11.2.13); a partial use
  of a closed package opens one package with its own rest (§ 11.1.17).
* The stock never goes below zero (§ 11.6.2, § 11.8.1).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet, Sum
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import ActivityContext, Company
from c4rsys.files import services as files
from c4rsys.files.models import StoredFile
from c4rsys.fleet import services as fleet
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.platform.registry import allocate_public_id
from c4rsys.warehouse import reserves
from c4rsys.warehouse.models import (
    Asset,
    Item,
    LayerUse,
    Lot,
    LotLayer,
    LotStock,
    OpenedPackage,
    OperationLine,
    Packaging,
    Receipt,
    ReceiptCorrection,
    ReceiptLine,
    StockMovement,
    StockOperation,
    WarehouseSettings,
    WriteOffReason,
)
from c4rsys.warehouse.stock import ZERO, expired, propagate_line_cost

MAX_LINES = 200
MAX_PHOTOS = 10
# Starter reasons of a write-off (§ 11.3.4); the company adds, renames and archives reasons.
STARTER_REASONS = ["damage", "expiry", "loss", "handling_loss", "other"]
OTHER = "other"


# --- Settings and reasons -------------------------------------------------------------------------


def settings_of(company_id: UUID) -> WarehouseSettings:
    found, _ = WarehouseSettings.objects.get_or_create(company_id=company_id)
    return found


def set_expired_policy(
    ctx: CommandContext, company_id: UUID, policy: str, expected_version: int
) -> WarehouseSettings:
    """Warning with an explicit confirmation (default) or a full ban (§ 11.2.15)."""
    if policy not in WarehouseSettings.ExpiredPolicy.values:
        raise errors.validation_error("expired_policy")
    current = settings_of(company_id)
    current = WarehouseSettings.objects.select_for_update().get(pk=current.pk)
    if current.version != expected_version:
        raise errors.version_conflict()
    before = current.expired_policy
    current.expired_policy = policy
    current.version += 1
    current.save()
    ctx.audit(
        "warehouse.settings_changed",
        object_type="warehouse_settings",
        object_id=company_id,
        before={"expired_policy": before},
        after={"expired_policy": policy},
    )
    return current


def reasons_of(company_id: UUID) -> QuerySet[WriteOffReason]:
    """The starter reasons appear for a company on first use."""
    existing = set(
        WriteOffReason.objects.filter(company_id=company_id)
        .exclude(code="")
        .values_list("code", flat=True)
    )
    WriteOffReason.objects.bulk_create(
        [
            WriteOffReason(company_id=company_id, code=code)
            for code in STARTER_REASONS
            if code not in existing
        ],
        ignore_conflicts=True,
    )
    return WriteOffReason.objects.filter(company_id=company_id).order_by("created_at", "code")


def add_reason(ctx: CommandContext, company_id: UUID, name: str) -> WriteOffReason:
    reason = WriteOffReason.objects.create(company_id=company_id, name=name)
    ctx.audit(
        "warehouse.reason_added",
        object_type="warehouse_write_off_reason",
        object_id=reason.id,
        after={"name": name},
    )
    return reason


def change_reason(
    ctx: CommandContext,
    company_id: UUID,
    reason_id: UUID,
    *,
    name: str | None = None,
    archived: bool | None = None,
) -> WriteOffReason:
    """Renaming keeps the history; a renamed starter reason shows its new name as entered."""
    reason = (
        WriteOffReason.objects.select_for_update()
        .filter(company_id=company_id, pk=reason_id)
        .first()
    )
    if reason is None:
        raise errors.not_found()
    before = {"name": reason.name, "state": reason.state}
    if name is not None:
        reason.name = name
    if archived is not None:
        reason.state = WriteOffReason.State.ARCHIVED if archived else WriteOffReason.State.ACTIVE
    if not reason.code and not reason.name:
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    reason.save()
    ctx.audit(
        "warehouse.reason_changed",
        object_type="warehouse_write_off_reason",
        object_id=reason.id,
        before=before,
        after={"name": reason.name, "state": reason.state},
    )
    return reason


# --- Taking from the stock ------------------------------------------------------------------------


@dataclass(frozen=True)
class Take:
    """What is taken: units of an individual item, or a quantity of a lot from one form —
    closed packages of a packaging, an opened package or loose material."""

    item_id: UUID
    asset_ids: tuple[UUID, ...] = ()
    lot_id: UUID | None = None
    packaging_id: UUID | None = None
    opened_package_id: UUID | None = None
    quantity: Decimal | None = None
    confirm_expired: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": str(self.item_id),
            "asset_ids": [str(a) for a in self.asset_ids],
            "lot_id": str(self.lot_id) if self.lot_id else None,
            "packaging_id": str(self.packaging_id) if self.packaging_id else None,
            "opened_package_id": str(self.opened_package_id) if self.opened_package_id else None,
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "confirm_expired": self.confirm_expired,
        }


@dataclass
class Piece:
    """A part of a taken quantity in one physical form, for the movement journal."""

    quantity: Decimal
    packaging: Packaging | None = None
    opened: OpenedPackage | None = None


@dataclass
class Taken:
    item: Item
    assets: list[Asset] = field(default_factory=list)
    lot: Lot | None = None
    packaging: Packaging | None = None
    opened: OpenedPackage | None = None
    quantity: Decimal = ZERO
    pieces: list[Piece] = field(default_factory=list)
    confirmed: bool = False


def _warehouse(
    company_id: UUID, context_id: UUID | None, field_name: str = "context_id"
) -> ActivityContext:
    if context_id is None:
        raise errors.validation_error(field_name, code="FIELD_REQUIRED")
    context = locations.storage_context(company_id, context_id)
    if context is None:
        raise errors.validation_error(field_name, code="WAREHOUSE_NOT_USABLE")
    return context


def _check_expired(company_id: UUID, lot: Lot, confirmed: bool, where: str) -> None:
    """§ 11.2.15: past the expiry date a lot is issued and used only after an explicit
    confirmation, or not at all when the company forbids it. Opened packages keep the expiry
    of their lot (§ 11.1.17)."""
    if not expired(lot):
        return
    if settings_of(company_id).expired_policy == WarehouseSettings.ExpiredPolicy.FORBID:
        raise errors.business_rule("EXPIRED_LOT_FORBIDDEN", field=where, lot=lot.public_id)
    if not confirmed:
        raise errors.business_rule("EXPIRED_LOT_CONFIRM", field=where, lot=lot.public_id)


def _stock_row(lot: Lot, packaging: Packaging | None) -> LotStock:
    row, _ = LotStock.objects.select_for_update().get_or_create(
        company_id=lot.company_id, lot=lot, packaging=packaging
    )
    return row


def _take_one(
    ctx: CommandContext,
    company_id: UUID,
    context: ActivityContext,
    take: Take,
    where: str,
    *,
    allow_open: bool,
    check_expiry: bool,
) -> Taken:
    item = Item.objects.filter(company_id=company_id, pk=take.item_id).first()
    if item is None:
        raise errors.validation_error(f"{where}.item_id", code="ITEM_UNKNOWN")
    if item.accounting == Item.Accounting.INDIVIDUAL:
        if not take.asset_ids or take.lot_id or take.quantity is not None:
            raise errors.validation_error(f"{where}.asset_ids", code="FIELD_REQUIRED")
        if len(set(take.asset_ids)) != len(take.asset_ids):
            raise errors.validation_error(f"{where}.asset_ids", code="DUPLICATE_VALUES")
        assets = list(
            Asset.objects.select_for_update().filter(
                company_id=company_id, pk__in=take.asset_ids, item=item
            )
        )
        if len(assets) != len(take.asset_ids):
            raise errors.validation_error(f"{where}.asset_ids", code="ASSET_UNKNOWN")
        for asset in assets:
            if (
                asset.context_id != context.id
                or asset.state != Asset.State.IN_STOCK
                or reserves.asset_reserved(asset.id)
            ):
                # § 11.8.1: nothing beyond what is available in this warehouse.
                raise errors.business_rule(
                    "ASSET_NOT_AVAILABLE", field=f"{where}.asset_ids", asset=asset.public_id
                )
        return Taken(item=item, assets=assets, quantity=Decimal(len(assets)))

    if take.asset_ids or take.lot_id is None:
        raise errors.validation_error(f"{where}.lot_id", code="FIELD_REQUIRED")
    if take.quantity is None:
        raise errors.validation_error(f"{where}.quantity", code="FIELD_REQUIRED")
    lot = (
        Lot.objects.select_for_update()
        .filter(company_id=company_id, pk=take.lot_id, item=item, context=context)
        .first()
    )
    if lot is None:
        raise errors.validation_error(f"{where}.lot_id", code="LOT_UNKNOWN")
    if check_expiry:
        _check_expired(company_id, lot, take.confirm_expired, where)
    amount = take.quantity
    taken = Taken(item=item, lot=lot, quantity=amount, confirmed=take.confirm_expired)
    if take.opened_package_id is not None:
        opened = (
            OpenedPackage.objects.select_for_update()
            .filter(company_id=company_id, pk=take.opened_package_id, lot=lot)
            .first()
        )
        if opened is None:
            raise errors.validation_error(
                f"{where}.opened_package_id", code="OPENED_PACKAGE_UNKNOWN"
            )
        free = opened.remaining - reserves.reserved_in_form(lot.id, opened.packaging_id, opened.id)
        if amount > free:
            raise errors.business_rule(
                "STOCK_NOT_ENOUGH", field=f"{where}.quantity", available=str(free)
            )
        opened.remaining -= amount
        opened.save(update_fields=["remaining"])
        taken.opened = opened
        taken.pieces.append(Piece(amount, opened=opened))
    elif take.packaging_id is not None:
        packaging = Packaging.objects.filter(item=item, pk=take.packaging_id).first()
        if packaging is None:
            raise errors.validation_error(f"{where}.packaging_id", code="PACKAGING_UNKNOWN")
        row = _stock_row(lot, packaging)
        size = packaging.quantity
        whole = int(amount // size)
        rest = amount - size * whole
        if rest and not allow_open:
            raise errors.validation_error(f"{where}.quantity", code="QUANTITY_NOT_WHOLE_PACKS")
        needed = size * (whole + (1 if rest else 0))
        free = row.quantity - reserves.reserved_in_form(lot.id, packaging.id, None)
        if needed > free:
            raise errors.business_rule(
                "STOCK_NOT_ENOUGH", field=f"{where}.quantity", available=str(free)
            )
        row.quantity -= needed
        row.save(update_fields=["quantity"])
        taken.packaging = packaging
        if whole:
            taken.pieces.append(Piece(size * whole, packaging=packaging))
        if rest:
            # § 11.1.17: the first partial use opens one package with its own rest.
            opened = OpenedPackage.objects.create(
                company_id=company_id, lot=lot, packaging=packaging, remaining=size - rest
            )
            StockMovement.objects.create(
                company_id=company_id,
                kind=StockMovement.Kind.OPEN,
                context=context,
                item=item,
                lot=lot,
                packaging=packaging,
                opened_package=opened,
                quantity=size,
                actor_user_id=ctx.actor.user_id,
            )
            taken.pieces.append(Piece(rest, opened=opened))
    else:
        row = _stock_row(lot, None)
        free = row.quantity - reserves.reserved_in_form(lot.id, None, None)
        if amount > free:
            raise errors.business_rule(
                "STOCK_NOT_ENOUGH", field=f"{where}.quantity", available=str(free)
            )
        row.quantity -= amount
        row.save(update_fields=["quantity"])
        taken.pieces.append(Piece(amount))
    return taken


def _take_all(
    ctx: CommandContext,
    company_id: UUID,
    context: ActivityContext,
    takes: tuple[Take, ...],
    *,
    allow_open: bool = False,
    check_expiry: bool = False,
) -> list[Taken]:
    if not takes:
        raise errors.validation_error("lines", code="LINES_REQUIRED")
    if len(takes) > MAX_LINES:
        raise errors.validation_error("lines", code="TOO_MANY_LINES", max=MAX_LINES)
    return [
        _take_one(
            ctx,
            company_id,
            context,
            take,
            f"lines[{index}]",
            allow_open=allow_open,
            check_expiry=check_expiry,
        )
        for index, take in enumerate(takes)
    ]


def _fifo(line: OperationLine, lot: Lot, amount: Decimal) -> None:
    """The earlier receipt layers are used first (§ 11.2.13)."""
    left = amount
    for layer in (
        LotLayer.objects.select_for_update()
        .filter(lot=lot, remaining__gt=0)
        .order_by("received_at", "id")
    ):
        part = min(layer.remaining, left)
        layer.remaining -= part
        layer.save(update_fields=["remaining"])
        LayerUse.objects.create(
            company_id=lot.company_id,
            line=line,
            layer=layer,
            quantity=part,
            unit_cost=layer.unit_cost,
        )
        left -= part
        if left == 0:
            return
    raise errors.business_rule("STOCK_NOT_ENOUGH", field="quantity", available=str(amount - left))


def _restore_layers(line: OperationLine, amount: Decimal) -> None:
    """Material coming back returns to the layers it was taken from, the last ones first."""
    left = amount
    for use in (
        line.layer_uses.select_for_update(of=("self",))
        .select_related("layer")
        .order_by("-layer__received_at", "-id")
    ):
        free = use.quantity - use.received_quantity
        part = min(free, left)
        if part <= 0:
            continue
        use.quantity -= part
        use.save(update_fields=["quantity"])
        layer = LotLayer.objects.select_for_update().get(pk=use.layer_id)
        layer.remaining += part
        layer.save(update_fields=["remaining"])
        left -= part
        if left == 0:
            return
    raise errors.business_rule("QUANTITY_EXCEEDS_TAKEN")


def _put_back(
    lot: Lot, packaging: Packaging | None, opened: OpenedPackage | None, amount: Decimal
) -> Piece:
    """Back into the opened package it came from, into closed packages when whole, otherwise as
    loose material."""
    if opened is not None and opened.lot_id == lot.id:
        opened = OpenedPackage.objects.select_for_update().get(pk=opened.pk)
        opened.remaining += amount
        opened.save(update_fields=["remaining"])
        return Piece(amount, opened=opened)
    if packaging is not None and amount % packaging.quantity == 0:
        row = _stock_row(lot, packaging)
        row.quantity += amount
        row.save(update_fields=["quantity"])
        return Piece(amount, packaging=packaging)
    row = _stock_row(lot, None)
    row.quantity += amount
    row.save(update_fields=["quantity"])
    return Piece(amount)


def _lot_of(line: OperationLine) -> Lot:
    if line.lot is None:
        raise errors.business_rule("LOT_UNKNOWN")
    return line.lot


def _new_operation(
    ctx: CommandContext, company: Company, kind: str, context: ActivityContext, **data: Any
) -> StockOperation:
    return StockOperation.objects.create(
        company=company,
        number=next_number(company.id, "warehouse_operation"),
        kind=kind,
        context=context,
        created_by_user_id=ctx.actor.user_id,
        **data,
    )


def _move(
    ctx: CommandContext,
    kind: str,
    context: ActivityContext,
    line: OperationLine,
    quantity: Decimal,
    *,
    asset: Asset | None = None,
    lot: Lot | None = None,
    piece: Piece | None = None,
) -> None:
    StockMovement.objects.create(
        company_id=line.company_id,
        kind=kind,
        context=context,
        item_id=line.item_id,
        asset=asset,
        lot=lot,
        packaging=piece.packaging if piece else None,
        opened_package=piece.opened if piece else None,
        quantity=quantity,
        operation_line=line,
        actor_user_id=ctx.actor.user_id,
    )


def _write_out(
    ctx: CommandContext,
    operation: StockOperation,
    taken: list[Taken],
    movement: str,
    asset_state: str,
) -> None:
    """Lines of an outgoing operation, their layers and the journal."""
    position = 0
    for entry in taken:
        for asset in entry.assets:
            line = OperationLine.objects.create(
                company_id=operation.company_id,
                operation=operation,
                item=entry.item,
                asset=asset,
                quantity=Decimal(1),
                position=position,
            )
            position += 1
            asset.state = asset_state
            asset.vehicle_id = operation.vehicle_id
            asset.save(update_fields=["state", "vehicle"])
            _move(ctx, movement, operation.context, line, Decimal(-1), asset=asset)
        if entry.lot is None:
            continue
        line = OperationLine.objects.create(
            company_id=operation.company_id,
            operation=operation,
            item=entry.item,
            lot=entry.lot,
            packaging=entry.packaging,
            opened_package=entry.opened or next((p.opened for p in entry.pieces if p.opened), None),
            quantity=entry.quantity,
            expired_confirmed=entry.confirmed and expired(entry.lot),
            position=position,
        )
        position += 1
        _fifo(line, entry.lot, entry.quantity)
        for piece in entry.pieces:
            _move(
                ctx, movement, operation.context, line, -piece.quantity, lot=entry.lot, piece=piece
            )


def snapshot(operation: StockOperation) -> dict[str, Any]:
    return {
        "number": operation.number,
        "kind": operation.kind,
        "state": operation.state,
        "context": str(operation.context_id),
        "target_context": str(operation.target_context_id) if operation.target_context_id else None,
        "vehicle": str(operation.vehicle_id) if operation.vehicle_id else None,
        "reason": str(operation.reason_id) if operation.reason_id else None,
        "comment": operation.comment,
        "lines": [
            {
                "item": str(line.item_id),
                "asset": str(line.asset_id) if line.asset_id else None,
                "lot": str(line.lot_id) if line.lot_id else None,
                "packaging": str(line.packaging_id) if line.packaging_id else None,
                "quantity": str(line.quantity),
                "output": line.output,
            }
            for line in operation.lines.order_by("position")
        ],
    }


# --- Issue, return and consumption (§ 11.4) -------------------------------------------------------


def _vehicle(company_id: UUID, vehicle_id: UUID | None) -> UUID:
    if vehicle_id is None:
        raise errors.validation_error("vehicle_id", code="FIELD_REQUIRED")
    if fleet.company_vehicle(company_id, vehicle_id) is None:
        raise errors.validation_error("vehicle_id", code="VEHICLE_NOT_USABLE")
    return vehicle_id


def issue(
    ctx: CommandContext,
    company: Company,
    kind: str,
    context_id: UUID | None,
    vehicle_id: UUID | None,
    takes: tuple[Take, ...],
    comment: str,
) -> StockOperation:
    """An issue with a return possible, or actual consumption, on a vehicle of the company. A
    partial use of a closed package opens one package (§ 11.1.17)."""
    if kind not in (StockOperation.Kind.ISSUE, StockOperation.Kind.CONSUME):
        raise errors.validation_error("kind")
    context = _warehouse(company.id, context_id)
    vehicle = _vehicle(company.id, vehicle_id)
    taken = _take_all(ctx, company.id, context, takes, allow_open=True, check_expiry=True)
    operation = _new_operation(ctx, company, kind, context, vehicle_id=vehicle, comment=comment)
    if kind == StockOperation.Kind.ISSUE:
        _write_out(ctx, operation, taken, StockMovement.Kind.ISSUE, Asset.State.ISSUED)
    else:
        _write_out(ctx, operation, taken, StockMovement.Kind.CONSUME, Asset.State.CONSUMED)
    ctx.audit(
        f"warehouse.{kind}d" if kind == "issue" else "warehouse.consumed",
        object_type="warehouse_operation",
        object_id=operation.id,
        after=snapshot(operation),
    )
    return operation


@dataclass(frozen=True)
class LineAmount:
    line_id: UUID
    quantity: Decimal

    def as_dict(self) -> dict[str, str]:
        return {"line_id": str(self.line_id), "quantity": str(self.quantity)}


def _lock_operation(company_id: UUID, operation_id: UUID, kind: str) -> StockOperation:
    operation = (
        StockOperation.objects.select_for_update()
        .filter(company_id=company_id, pk=operation_id, kind=kind)
        .first()
    )
    if operation is None:
        raise errors.not_found()
    return operation


def _lines(
    operation: StockOperation, amounts: tuple[LineAmount, ...]
) -> list[tuple[OperationLine, Decimal]]:
    if not amounts:
        raise errors.validation_error("lines", code="LINES_REQUIRED")
    found = {
        line.id: line
        for line in operation.lines.select_for_update().filter(pk__in=[a.line_id for a in amounts])
    }
    result = []
    for index, amount in enumerate(amounts):
        line = found.get(amount.line_id)
        if line is None:
            raise errors.validation_error(f"lines[{index}].line_id", code="OPERATION_LINE_UNKNOWN")
        if line.asset_id and amount.quantity != 1:
            raise errors.validation_error(f"lines[{index}].quantity", code="QUANTITY_WHOLE_UNITS")
        result.append((line, amount.quantity))
    return result


def outstanding(line: OperationLine) -> Decimal:
    """Issued and neither returned nor consumed yet."""
    return line.quantity - line.returned_quantity - line.consumed_quantity


def return_issued(
    ctx: CommandContext, company_id: UUID, operation_id: UUID, amounts: tuple[LineAmount, ...]
) -> StockOperation:
    """Unused material comes back into the same lot and the same unit with its ID (§ 11.4.5)."""
    operation = _lock_operation(company_id, operation_id, StockOperation.Kind.ISSUE)
    done = []
    for line, amount in _lines(operation, amounts):
        if amount > outstanding(line):
            raise errors.business_rule("QUANTITY_EXCEEDS_ISSUED", line=str(line.id))
        line.returned_quantity += amount
        line.save(update_fields=["returned_quantity"])
        if line.asset_id:
            asset = Asset.objects.select_for_update().get(pk=line.asset_id)
            asset.state = Asset.State.IN_STOCK
            asset.vehicle = None
            asset.save(update_fields=["state", "vehicle"])
            _move(ctx, StockMovement.Kind.RETURN, operation.context, line, Decimal(1), asset=asset)
        else:
            lot = _lot_of(line)
            _restore_layers(line, amount)
            piece = _put_back(lot, line.packaging, line.opened_package, amount)
            _move(
                ctx,
                StockMovement.Kind.RETURN,
                operation.context,
                line,
                amount,
                lot=lot,
                piece=piece,
            )
        done.append({"line": str(line.id), "returned": str(amount)})
    operation.version += 1
    operation.save(update_fields=["version"])
    ctx.audit(
        "warehouse.issue_returned",
        object_type="warehouse_operation",
        object_id=operation.id,
        after={"lines": done},
    )
    return operation


def consume_issued(
    ctx: CommandContext, company_id: UUID, operation_id: UUID, amounts: tuple[LineAmount, ...]
) -> StockOperation:
    """Of the issued, this was actually used; the rest may still be returned (§ 11.4.1)."""
    operation = _lock_operation(company_id, operation_id, StockOperation.Kind.ISSUE)
    done = []
    for line, amount in _lines(operation, amounts):
        if amount > outstanding(line):
            raise errors.business_rule("QUANTITY_EXCEEDS_ISSUED", line=str(line.id))
        line.consumed_quantity += amount
        line.save(update_fields=["consumed_quantity"])
        if line.asset_id:
            Asset.objects.filter(pk=line.asset_id).update(state=Asset.State.CONSUMED)
        done.append({"line": str(line.id), "consumed": str(amount)})
    operation.version += 1
    operation.save(update_fields=["version"])
    ctx.audit(
        "warehouse.issue_consumed",
        object_type="warehouse_operation",
        object_id=operation.id,
        after={"lines": done},
    )
    return operation


# --- Moves (§ 11.5, § 11.3.3) ---------------------------------------------------------------------


def send(
    ctx: CommandContext,
    company: Company,
    context_id: UUID | None,
    target_id: UUID | None,
    takes: tuple[Take, ...],
    comment: str,
) -> StockOperation:
    """Sent: unavailable in the sending warehouse, in transit until received (§ 11.5.1)."""
    context = _warehouse(company.id, context_id)
    target = _warehouse(company.id, target_id, "target_context_id")
    if target.id == context.id:
        raise errors.validation_error("target_context_id", code="MOVE_SAME_WAREHOUSE")
    taken = _take_all(ctx, company.id, context, takes)
    operation = _new_operation(
        ctx,
        company,
        StockOperation.Kind.MOVE,
        context,
        target_context=target,
        state=StockOperation.State.IN_TRANSIT,
        comment=comment,
    )
    _write_out(ctx, operation, taken, StockMovement.Kind.MOVE_OUT, Asset.State.IN_TRANSIT)
    ctx.audit(
        "warehouse.move_sent",
        object_type="warehouse_operation",
        object_id=operation.id,
        after=snapshot(operation),
    )
    return operation


def _child_lot(line: OperationLine, target: ActivityContext, amount: Decimal) -> Lot:
    """§ 11.3.3: a part moved to another warehouse becomes a child lot with its own ID and a link
    to the source; its cost and expiry stay, the earlier receipts go first."""
    source = _lot_of(line)
    child = Lot(
        company_id=source.company_id,
        item_id=source.item_id,
        context=target,
        parent=source,
        external_number=source.external_number,
        production_date=source.production_date,
        expiry_date=source.expiry_date,
        currency=source.currency,
    )
    child.public_id = allocate_public_id("LOT", entity_type="warehouse_lot", entity_id=child.id)
    child.save(force_insert=True)
    left = amount
    for use in (
        line.layer_uses.select_for_update(of=("self",))
        .select_related("layer")
        .order_by("layer__received_at", "id")
    ):
        part = min(use.quantity - use.received_quantity, left)
        if part <= 0:
            continue
        LotLayer.objects.create(
            company_id=source.company_id,
            lot=child,
            source_layer=use.layer,
            received_quantity=part,
            remaining=part,
            unit_cost=use.unit_cost,
            received_at=use.layer.received_at,
        )
        use.received_quantity += part
        use.save(update_fields=["received_quantity"])
        left -= part
        if left == 0:
            break
    return child


def receive(
    ctx: CommandContext,
    company_id: UUID,
    operation_id: UUID,
    amounts: tuple[LineAmount, ...],
) -> StockOperation:
    """The receiver records what was actually received (§ 11.5.1). Lines not listed are not
    received; a shortage stays until it is settled (decision of 25.10.2026)."""
    operation = _lock_operation(company_id, operation_id, StockOperation.Kind.MOVE)
    if operation.state != StockOperation.State.IN_TRANSIT:
        raise errors.business_rule("MOVE_ALREADY_RECEIVED")
    target = locations.storage_context(company_id, operation.target_context_id or UUID(int=0))
    if target is None:
        raise errors.business_rule("WAREHOUSE_NOT_USABLE", field="target_context_id")
    given = {a.line_id: a.quantity for a in amounts}
    lines = list(
        operation.lines.select_for_update(of=("self",))
        .select_related("lot", "packaging")
        .order_by("position")
    )
    known = {line.id for line in lines}
    if not set(given) <= known:
        raise errors.validation_error("lines", code="OPERATION_LINE_UNKNOWN")
    short = False
    received = []
    for line in lines:
        amount = given.get(line.id, ZERO)
        if amount > line.quantity or (line.asset_id and amount not in (0, 1)):
            raise errors.validation_error("lines", code="QUANTITY_EXCEEDS_SENT")
        if amount < line.quantity:
            short = True
        if amount <= 0:
            continue
        line.received_quantity = amount
        if line.asset_id:
            asset = Asset.objects.select_for_update().get(pk=line.asset_id)
            asset.context = target
            asset.state = Asset.State.IN_STOCK
            asset.save(update_fields=["context", "state"])
            _move(ctx, StockMovement.Kind.MOVE_IN, target, line, Decimal(1), asset=asset)
        else:
            child = _child_lot(line, target, amount)
            line.target_lot = child
            piece = _put_back(child, line.packaging, None, amount)
            _move(ctx, StockMovement.Kind.MOVE_IN, target, line, amount, lot=child, piece=piece)
        line.save(update_fields=["received_quantity", "target_lot"])
        received.append({"line": str(line.id), "received": str(amount)})
    operation.state = StockOperation.State.DISCREPANCY if short else StockOperation.State.RECEIVED
    operation.received_by_user_id = ctx.actor.user_id
    operation.received_at = timezone.now()
    operation.version += 1
    operation.save(update_fields=["state", "received_by_user_id", "received_at", "version"])
    ctx.audit(
        "warehouse.move_received",
        object_type="warehouse_operation",
        object_id=operation.id,
        after={"state": operation.state, "lines": received},
    )
    return operation


def shortage(line: OperationLine) -> Decimal:
    return line.quantity - line.received_quantity - line.settled_quantity


def settle_shortage(
    ctx: CommandContext,
    company: Company,
    operation_id: UUID,
    line_id: UUID,
    action: str,
    reason_id: UUID | None,
    comment: str,
) -> StockOperation:
    """A shortage of a received move is returned to the sending warehouse or written off
    (decision of 25.10.2026)."""
    operation = _lock_operation(company.id, operation_id, StockOperation.Kind.MOVE)
    if operation.state != StockOperation.State.DISCREPANCY:
        raise errors.business_rule("NO_SHORTAGE")
    line = (
        operation.lines.select_for_update(of=("self",))
        .select_related("lot", "packaging", "opened_package")
        .filter(pk=line_id)
        .first()
    )
    if line is None:
        raise errors.not_found()
    amount = shortage(line)
    if amount <= 0:
        raise errors.business_rule("NO_SHORTAGE")
    if action == "return":
        if line.asset_id:
            asset = Asset.objects.select_for_update().get(pk=line.asset_id)
            asset.state = Asset.State.IN_STOCK
            asset.save(update_fields=["state"])
            _move(
                ctx, StockMovement.Kind.MOVE_BACK, operation.context, line, Decimal(1), asset=asset
            )
        else:
            lot = _lot_of(line)
            _restore_layers(line, amount)
            piece = _put_back(lot, line.packaging, line.opened_package, amount)
            _move(
                ctx,
                StockMovement.Kind.MOVE_BACK,
                operation.context,
                line,
                amount,
                lot=lot,
                piece=piece,
            )
        result: dict[str, Any] = {"line": str(line.id), "returned": str(amount)}
    elif action == "write_off":
        reason = _reason(company.id, reason_id, comment)
        write_off = _new_operation(
            ctx,
            company,
            StockOperation.Kind.WRITE_OFF,
            operation.context,
            reason=reason,
            parent=operation,
            comment=comment,
        )
        OperationLine.objects.create(
            company_id=company.id,
            operation=write_off,
            item_id=line.item_id,
            asset_id=line.asset_id,
            lot_id=line.lot_id,
            packaging_id=line.packaging_id,
            quantity=amount,
        )
        if line.asset_id:
            Asset.objects.filter(pk=line.asset_id).update(state=Asset.State.WRITTEN_OFF)
        result = {"line": str(line.id), "written_off": str(amount), "write_off": write_off.number}
    else:
        raise errors.validation_error("action")
    line.settled_quantity += amount
    line.save(update_fields=["settled_quantity"])
    if all(shortage(other) <= 0 for other in operation.lines.all()):
        operation.state = StockOperation.State.RECEIVED
    operation.version += 1
    operation.save(update_fields=["state", "version"])
    ctx.audit(
        "warehouse.move_shortage_settled",
        object_type="warehouse_operation",
        object_id=operation.id,
        after=result,
        reason=comment,
    )
    return operation


# --- Write-off (§ 11.3.4) and repacking (§ 11.3.3) ------------------------------------------------


def _reason(company_id: UUID, reason_id: UUID | None, comment: str) -> WriteOffReason:
    """The reason is required; the comment is required for «Other» (§ 11.3.4)."""
    if reason_id is None:
        raise errors.validation_error("reason_id", code="FIELD_REQUIRED")
    reason = reasons_of(company_id).filter(pk=reason_id, state=WriteOffReason.State.ACTIVE).first()
    if reason is None:
        raise errors.validation_error("reason_id", code="REASON_UNKNOWN")
    if reason.code == OTHER and not comment.strip():
        raise errors.validation_error("comment", code="COMMENT_REQUIRED_FOR_OTHER")
    return reason


def _photos(ctx: CommandContext, company_id: UUID, photos: tuple[UUID, ...]) -> list[UUID]:
    if len(photos) > MAX_PHOTOS:
        raise errors.validation_error("photos", code="TOO_MANY_FILES", max=MAX_PHOTOS)
    for photo in photos:
        files.usable(
            photo,
            purpose=StoredFile.Purpose.WAREHOUSE_DOCUMENT,
            user_id=ctx.actor.user_id,
            company_id=company_id,
        )
        files.attach_to_company(photo, company_id)
    return list(photos)


def write_off(
    ctx: CommandContext,
    company: Company,
    context_id: UUID | None,
    reason_id: UUID | None,
    comment: str,
    photos: tuple[UUID, ...],
    takes: tuple[Take, ...],
) -> StockOperation:
    """The written-off leaves the stock; the record of the property and its history stay. A
    partial write-off of a closed package opens one package (§ 11.1.17)."""
    context = _warehouse(company.id, context_id)
    reason = _reason(company.id, reason_id, comment)
    taken = _take_all(ctx, company.id, context, takes, allow_open=True)
    operation = _new_operation(
        ctx,
        company,
        StockOperation.Kind.WRITE_OFF,
        context,
        reason=reason,
        comment=comment,
        photos=_photos(ctx, company.id, photos),
    )
    _write_out(ctx, operation, taken, StockMovement.Kind.WRITE_OFF, Asset.State.WRITTEN_OFF)
    ctx.audit(
        "warehouse.written_off",
        object_type="warehouse_operation",
        object_id=operation.id,
        after=snapshot(operation),
        reason=comment,
    )
    return operation


@dataclass(frozen=True)
class RepackData:
    context_id: UUID | None
    lot_id: UUID | None
    source_packaging_id: UUID | None
    source_opened_package_id: UUID | None
    source_quantity: Decimal
    outputs: tuple[tuple[UUID | None, Decimal], ...]
    loss: Decimal = ZERO
    loss_reason_id: UUID | None = None
    comment: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_id": str(self.context_id) if self.context_id else None,
            "lot_id": str(self.lot_id) if self.lot_id else None,
            "source_packaging_id": str(self.source_packaging_id)
            if self.source_packaging_id
            else None,
            "source_opened_package_id": str(self.source_opened_package_id)
            if self.source_opened_package_id
            else None,
            "source_quantity": str(self.source_quantity),
            "outputs": [[str(p) if p else None, str(q)] for p, q in self.outputs],
            "loss": str(self.loss),
            "loss_reason_id": str(self.loss_reason_id) if self.loss_reason_id else None,
            "comment": self.comment,
        }


def repack(ctx: CommandContext, company: Company, data: RepackData) -> StockOperation:
    """§ 11.3.3: inside one warehouse the lot stays; the whole source is accounted for in the
    obtained packages and an explicit write-off of losses with a reason — the difference is not
    written off automatically. The rest not taken stays as it was. The cost stays."""
    context = _warehouse(company.id, data.context_id)
    if not data.outputs:
        raise errors.validation_error("outputs", code="LINES_REQUIRED")
    total = sum((q for _, q in data.outputs), ZERO) + data.loss
    if total != data.source_quantity:
        raise errors.business_rule(
            "REPACK_NOT_BALANCED", source=str(data.source_quantity), accounted=str(total)
        )
    lot = Lot.objects.filter(
        company_id=company.id, pk=data.lot_id or UUID(int=0), context=context
    ).first()
    if lot is None:
        raise errors.validation_error("lot_id", code="LOT_UNKNOWN")
    reason = _reason(company.id, data.loss_reason_id, data.comment) if data.loss > 0 else None
    packagings = {
        p.id: p for p in Packaging.objects.filter(item_id=lot.item_id, state=Packaging.State.ACTIVE)
    }
    for index, (packaging_id, amount) in enumerate(data.outputs):
        if packaging_id is not None:
            packaging = packagings.get(packaging_id)
            if packaging is None:
                raise errors.validation_error(
                    f"outputs[{index}].packaging_id", code="PACKAGING_UNKNOWN"
                )
            if amount % packaging.quantity:
                raise errors.validation_error(
                    f"outputs[{index}].quantity", code="QUANTITY_NOT_WHOLE_PACKS"
                )
        if packaging_id == data.source_packaging_id and data.source_opened_package_id is None:
            raise errors.validation_error(f"outputs[{index}].packaging_id", code="REPACK_SAME_FORM")
    source = _take_one(
        ctx,
        company.id,
        context,
        Take(
            item_id=lot.item_id,
            lot_id=lot.id,
            packaging_id=data.source_packaging_id,
            opened_package_id=data.source_opened_package_id,
            quantity=data.source_quantity,
        ),
        "source",
        allow_open=False,
        check_expiry=False,
    )
    operation = _new_operation(
        ctx, company, StockOperation.Kind.REPACK, context, comment=data.comment
    )
    source_line = OperationLine.objects.create(
        company_id=company.id,
        operation=operation,
        item_id=lot.item_id,
        lot=lot,
        packaging=source.packaging,
        opened_package=source.opened,
        quantity=data.source_quantity,
    )
    kept = data.source_quantity - data.loss
    if kept > 0:
        _move(
            ctx,
            StockMovement.Kind.REPACK,
            context,
            source_line,
            -kept,
            lot=lot,
            piece=source.pieces[0],
        )
    for index, (packaging_id, amount) in enumerate(data.outputs, start=1):
        packaging = packagings.get(packaging_id) if packaging_id else None
        line = OperationLine.objects.create(
            company_id=company.id,
            operation=operation,
            item_id=lot.item_id,
            lot=lot,
            packaging=packaging,
            quantity=amount,
            output=True,
            position=index,
        )
        row = _stock_row(lot, packaging)
        row.quantity += amount
        row.save(update_fields=["quantity"])
        _move(
            ctx,
            StockMovement.Kind.REPACK,
            context,
            line,
            amount,
            lot=lot,
            piece=Piece(amount, packaging=packaging),
        )
    if data.loss > 0:
        losses = _new_operation(
            ctx,
            company,
            StockOperation.Kind.WRITE_OFF,
            context,
            reason=reason,
            parent=operation,
            comment=data.comment,
        )
        line = OperationLine.objects.create(
            company_id=company.id,
            operation=losses,
            item_id=lot.item_id,
            lot=lot,
            packaging=source.packaging,
            quantity=data.loss,
        )
        _fifo(line, lot, data.loss)
        _move(
            ctx,
            StockMovement.Kind.WRITE_OFF,
            context,
            line,
            -data.loss,
            lot=lot,
            piece=source.pieces[0],
        )
    ctx.audit(
        "warehouse.repacked",
        object_type="warehouse_operation",
        object_id=operation.id,
        after={**snapshot(operation), "loss": str(data.loss)},
    )
    return operation


# --- Correction of a posted receipt (§ 11.6.2) ----------------------------------------------------


def effective_quantity(line: ReceiptLine) -> Decimal:
    return line.corrected_quantity if line.corrected_quantity is not None else line.quantity


def _blocking(layer: LotLayer) -> list[int]:
    """Numbers of later operations that took from the layer — they prevent a decrease."""
    return sorted(
        set(
            LayerUse.objects.filter(layer=layer, quantity__gt=0).values_list(
                "line__operation__number", flat=True
            )
        )
    )


def correct_receipt_line(
    ctx: CommandContext,
    company: Company,
    receipt_id: UUID,
    line_id: UUID,
    quantity: Decimal | None,
    unit_cost: Decimal | None,
    change_cost: bool,
    remove_assets: tuple[UUID, ...],
    reason: str,
) -> ReceiptLine:
    """A posted receipt is corrected by a separate action with a reason. The stock never goes
    below zero: later movements that prevent it are shown. The original record, the change and
    the author are kept."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    receipt = (
        Receipt.objects.select_for_update().filter(company_id=company.id, pk=receipt_id).first()
    )
    if receipt is None:
        raise errors.not_found()
    if receipt.state != Receipt.State.POSTED:
        raise errors.business_rule("RECEIPT_NOT_POSTED")
    line = (
        ReceiptLine.objects.select_for_update(of=("self",))
        .select_related("item", "packaging", "lot")
        .filter(receipt=receipt, pk=line_id)
        .first()
    )
    if line is None:
        raise errors.not_found()
    before_quantity = effective_quantity(line)
    before_cost = line.unit_cost
    target = before_quantity if quantity is None else quantity
    delta = target - before_quantity
    if delta == 0 and (not change_cost or unit_cost == before_cost):
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")
    context = receipt.context
    if line.item.accounting == Item.Accounting.INDIVIDUAL:
        if target != target.to_integral_value():
            raise errors.validation_error("quantity", code="QUANTITY_WHOLE_UNITS")
        if delta < 0:
            if len(remove_assets) != -delta:
                raise errors.validation_error(
                    "remove_asset_ids", code="ASSETS_TO_REMOVE_COUNT", count=int(-delta)
                )
            assets = list(
                Asset.objects.select_for_update().filter(receipt_line=line, pk__in=remove_assets)
            )
            if len(assets) != len(remove_assets):
                raise errors.validation_error("remove_asset_ids", code="ASSET_UNKNOWN")
            for asset in assets:
                if asset.state != Asset.State.IN_STOCK:
                    raise errors.business_rule("CORRECTION_BLOCKED", asset=asset.public_id)
                asset.state = Asset.State.CANCELLED
                asset.save(update_fields=["state"])
                StockMovement.objects.create(
                    company_id=company.id,
                    kind=StockMovement.Kind.CORRECTION,
                    context=asset.context,
                    item=line.item,
                    asset=asset,
                    quantity=Decimal(-1),
                    receipt_line=line,
                    actor_user_id=ctx.actor.user_id,
                )
        for _ in range(int(max(delta, ZERO))):
            asset = Asset(
                company_id=company.id,
                item=line.item,
                context=context,
                tags=line.tags,
                purchase_cost=unit_cost if change_cost else line.unit_cost,
                currency=receipt.currency,
                receipt_line=line,
            )
            asset.public_id = allocate_public_id(
                "AST", entity_type="warehouse_asset", entity_id=asset.id
            )
            asset.save(force_insert=True)
            StockMovement.objects.create(
                company_id=company.id,
                kind=StockMovement.Kind.CORRECTION,
                context=context,
                item=line.item,
                asset=asset,
                quantity=Decimal(1),
                receipt_line=line,
                actor_user_id=ctx.actor.user_id,
            )
    elif delta != 0:
        layer = LotLayer.objects.select_for_update().filter(receipt_line=line).first()
        lot = line.lot
        if layer is None or lot is None:
            raise errors.business_rule("CORRECTION_BLOCKED")
        if target <= 0:
            raise errors.validation_error("quantity", code="QUANTITY_NOT_POSITIVE")
        if delta < 0:
            amount = -delta
            row = (
                _stock_row(lot, line.packaging)
                if line.packaging and amount % line.packaging.quantity == 0
                else _stock_row(lot, None)
            )
            if layer.remaining < amount or row.quantity < amount:
                raise errors.business_rule(
                    "CORRECTION_BLOCKED",
                    operations=_blocking(layer),
                    available=str(min(layer.remaining, row.quantity)),
                )
            row.quantity -= amount
            row.save(update_fields=["quantity"])
            piece_packaging = row.packaging
        else:
            piece = _put_back(lot, line.packaging, None, delta)
            piece_packaging = piece.packaging
        layer.received_quantity += delta
        layer.remaining += delta
        layer.save(update_fields=["received_quantity", "remaining"])
        StockMovement.objects.create(
            company_id=company.id,
            kind=StockMovement.Kind.CORRECTION,
            context=context,
            item=line.item,
            lot=lot,
            packaging=piece_packaging,
            quantity=delta,
            receipt_line=line,
            actor_user_id=ctx.actor.user_id,
        )
    if delta != 0:
        line.corrected_quantity = target
    if change_cost and unit_cost != before_cost:
        line.unit_cost = unit_cost
        propagate_line_cost(line, unit_cost)
    line.save(update_fields=["corrected_quantity", "unit_cost"])
    correction = ReceiptCorrection.objects.create(
        company_id=company.id,
        line=line,
        quantity_before=before_quantity,
        quantity_after=target,
        unit_cost_before=before_cost,
        unit_cost_after=line.unit_cost,
        reason=reason,
        user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "warehouse.receipt_corrected",
        object_type="warehouse_receipt",
        object_id=receipt.id,
        before={
            "line": str(line.id),
            "quantity": str(before_quantity),
            "unit_cost": str(before_cost) if before_cost is not None else None,
        },
        after={
            "line": str(line.id),
            "quantity": str(target),
            "unit_cost": str(line.unit_cost) if line.unit_cost is not None else None,
            "correction": str(correction.id),
        },
        reason=reason,
    )
    return line


# --- Reading -----------------------------------------------------------------------------------


def operations_of(company_id: UUID) -> QuerySet[StockOperation]:
    return (
        StockOperation.objects.filter(company_id=company_id)
        .select_related("context__location", "target_context__location", "reason", "receipt")
        .prefetch_related(
            "lines__item",
            "lines__asset",
            "lines__lot",
            "lines__packaging",
            "lines__layer_uses",
            "lines__target_lot",
        )
        .order_by("-number")
    )


def get_operation(company_id: UUID, operation_id: UUID) -> StockOperation:
    operation = operations_of(company_id).filter(pk=operation_id).first()
    if operation is None:
        raise errors.not_found()
    return operation


def sources(company_id: UUID, item_id: UUID, context_id: UUID) -> dict[str, Any]:
    """What can be taken from the warehouse: units in stock, or lots with their closed
    packages, opened packages and loose material. Lots come in the suggested order: the nearest
    expiry first, lots without expiry after them, the earlier receipt first (§ 11.3.1)."""
    item = Item.objects.filter(company_id=company_id, pk=item_id).first()
    if item is None:
        raise errors.not_found()
    if item.accounting == Item.Accounting.INDIVIDUAL:
        assets = Asset.objects.filter(
            company_id=company_id, item=item, context_id=context_id, state=Asset.State.IN_STOCK
        ).order_by("created_at")
        return {
            "assets": [
                {
                    "id": str(a.id),
                    "public_id": a.public_id,
                    "serial_number": a.serial_number,
                    "tags": a.tags,
                }
                for a in assets
            ],
            "lots": [],
        }
    today = timezone.localdate()
    lots = []
    for lot in Lot.objects.filter(
        company_id=company_id, item=item, context_id=context_id
    ).prefetch_related("stock__packaging", "opened_packages__packaging", "layers"):
        forms = [
            {
                "packaging_id": str(row.packaging_id) if row.packaging_id else None,
                "packaging_quantity": str(row.packaging.quantity) if row.packaging else None,
                "quantity": str(row.quantity),
            }
            for row in lot.stock.all()
            if row.quantity > 0
        ]
        opened = [
            {
                "id": str(o.id),
                "packaging_quantity": str(o.packaging.quantity),
                "remaining": str(o.remaining),
                "opened_at": o.opened_at.isoformat(),
                "label": o.label,
            }
            for o in lot.opened_packages.all()
            if o.remaining > 0
        ]
        if not forms and not opened:
            continue
        first = min((layer.received_at for layer in lot.layers.all()), default=lot.created_at)
        lots.append(
            (
                (lot.expiry_date is None, lot.expiry_date or today, first),
                {
                    "id": str(lot.id),
                    "public_id": lot.public_id,
                    "external_number": lot.external_number,
                    "expiry_date": lot.expiry_date.isoformat() if lot.expiry_date else None,
                    "expired": expired(lot, today),
                    "forms": forms,
                    "opened_packages": opened,
                },
            )
        )
    lots.sort(key=lambda entry: entry[0])
    return {"assets": [], "lots": [entry for _, entry in lots]}


def issued_of_item(company_id: UUID, item_id: UUID, context_ids: set[UUID]) -> list[dict[str, Any]]:
    """Issued with a return possible and not yet returned or consumed."""
    lines = (
        OperationLine.objects.filter(
            company_id=company_id,
            item_id=item_id,
            operation__kind=StockOperation.Kind.ISSUE,
            operation__context_id__in=context_ids,
        )
        .select_related("operation", "asset", "lot")
        .order_by("operation__number")
    )
    result = []
    for line in lines:
        left = outstanding(line)
        if left <= 0:
            continue
        result.append(
            {
                "operation_id": str(line.operation_id),
                "number": line.operation.number,
                "vehicle_id": str(line.operation.vehicle_id),
                "asset": line.asset.public_id if line.asset else None,
                "lot": line.lot.public_id if line.lot else None,
                "quantity": str(left),
            }
        )
    return result


def in_transit_count(company_id: UUID, context_ids: set[UUID]) -> int:
    return StockOperation.objects.filter(
        Q(context_id__in=context_ids) | Q(target_context_id__in=context_ids),
        company_id=company_id,
        kind=StockOperation.Kind.MOVE,
        state__in=[StockOperation.State.IN_TRANSIT, StockOperation.State.DISCREPANCY],
    ).count()


# --- Return to the supplier (§ 11.4.2) ----------------------------------------------------------


@dataclass(frozen=True)
class ReturnLine:
    """What goes back by one receipt line: its units, or a quantity of its lot taken from one
    form and counted against what this very receipt brought into the lot."""

    receipt_line_id: UUID
    asset_ids: tuple[UUID, ...] = ()
    packaging_id: UUID | None = None
    opened_package_id: UUID | None = None
    quantity: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "receipt_line_id": str(self.receipt_line_id),
            "asset_ids": [str(a) for a in self.asset_ids],
            "packaging_id": str(self.packaging_id) if self.packaging_id else None,
            "opened_package_id": str(self.opened_package_id) if self.opened_package_id else None,
            "quantity": str(self.quantity) if self.quantity is not None else None,
        }


def return_to_supplier(
    ctx: CommandContext,
    company: Company,
    receipt_id: UUID,
    lines: tuple[ReturnLine, ...],
    comment: str,
) -> StockOperation:
    """A separate operation referring to the original receipt: all or part of what it brought
    goes back, the history stays. The physical return does not return money by itself: a refund,
    a smaller debt or a replacement is settled separately (finance stage). PROPOSAL: goods return
    from the warehouse of the receipt; a lot returns out of what this receipt brought into it."""
    receipt = (
        Receipt.objects.select_for_update().filter(company_id=company.id, pk=receipt_id).first()
    )
    if receipt is None:
        raise errors.not_found()
    if receipt.state != Receipt.State.POSTED or receipt.source != Receipt.Source.PURCHASE:
        raise errors.business_rule("RETURN_PURCHASE_RECEIPTS_ONLY")
    context = _warehouse(company.id, receipt.context_id)
    if not lines:
        raise errors.validation_error("lines", code="LINES_REQUIRED")
    operation = _new_operation(
        ctx, company, StockOperation.Kind.SUPPLIER_RETURN, context, receipt=receipt, comment=comment
    )
    position = 0
    for index, entry in enumerate(lines):
        where = f"lines[{index}]"
        line = (
            ReceiptLine.objects.select_for_update(of=("self",))
            .select_related("item")
            .filter(receipt=receipt, pk=entry.receipt_line_id)
            .first()
        )
        if line is None:
            raise errors.validation_error(f"{where}.receipt_line_id", code="RECEIPT_LINE_UNKNOWN")
        if line.item.accounting == Item.Accounting.INDIVIDUAL:
            if not entry.asset_ids:
                raise errors.validation_error(f"{where}.asset_ids", code="FIELD_REQUIRED")
            if Asset.objects.filter(pk__in=entry.asset_ids).exclude(receipt_line=line).exists():
                raise errors.validation_error(f"{where}.asset_ids", code="ASSET_NOT_OF_RECEIPT")
        take = Take(
            item_id=line.item_id,
            asset_ids=entry.asset_ids,
            lot_id=line.lot_id if line.item.accounting == Item.Accounting.BATCH else None,
            packaging_id=entry.packaging_id,
            opened_package_id=entry.opened_package_id,
            quantity=entry.quantity,
        )
        layer = None
        if line.item.accounting == Item.Accounting.BATCH:
            layer = LotLayer.objects.select_for_update().filter(receipt_line=line).first()
            if layer is None or entry.quantity is None or entry.quantity > layer.remaining:
                raise errors.business_rule(
                    "RETURN_EXCEEDS_RECEIPT",
                    field=f"{where}.quantity",
                    available=str(layer.remaining if layer else ZERO),
                )
        taken = _take_one(
            ctx, company.id, context, take, where, allow_open=True, check_expiry=False
        )
        for asset in taken.assets:
            op_line = OperationLine.objects.create(
                company_id=company.id,
                operation=operation,
                item=line.item,
                asset=asset,
                quantity=Decimal(1),
                receipt_line=line,
                position=position,
            )
            position += 1
            asset.state = Asset.State.RETURNED
            asset.save(update_fields=["state"])
            _move(
                ctx, StockMovement.Kind.SUPPLIER_RETURN, context, op_line, Decimal(-1), asset=asset
            )
        if layer is not None and taken.lot is not None:
            op_line = OperationLine.objects.create(
                company_id=company.id,
                operation=operation,
                item=line.item,
                lot=taken.lot,
                packaging=taken.packaging,
                opened_package=taken.opened
                or next((p.opened for p in taken.pieces if p.opened), None),
                quantity=taken.quantity,
                receipt_line=line,
                position=position,
            )
            position += 1
            # Out of this receipt's own layer, not the earliest one (§ 11.2.13).
            layer.remaining -= taken.quantity
            layer.save(update_fields=["remaining"])
            LayerUse.objects.create(
                company_id=company.id,
                line=op_line,
                layer=layer,
                quantity=taken.quantity,
                unit_cost=layer.unit_cost,
            )
            for piece in taken.pieces:
                _move(
                    ctx,
                    StockMovement.Kind.SUPPLIER_RETURN,
                    context,
                    op_line,
                    -piece.quantity,
                    lot=taken.lot,
                    piece=piece,
                )
    ctx.audit(
        "warehouse.returned_to_supplier",
        object_type="warehouse_receipt",
        object_id=receipt.id,
        after={"operation": operation.number, "lines": [entry.as_dict() for entry in lines]},
        reason=comment,
    )
    return operation


def returned_by_line(line_ids: list[UUID]) -> dict[UUID, Decimal]:
    rows = (
        OperationLine.objects.filter(
            receipt_line_id__in=line_ids, operation__kind=StockOperation.Kind.SUPPLIER_RETURN
        )
        .values("receipt_line_id")
        .annotate(total=Sum("quantity"))
    )
    return {row["receipt_line_id"]: row["total"] for row in rows}
