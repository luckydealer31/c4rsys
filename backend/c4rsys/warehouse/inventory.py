"""Inventory (ТЗ § 11.2.1, § 11.6.1, § 11.6.3–11.6.4, § 11.8.1; decisions of 25.10.2026).

An inventory covers chosen items of one warehouse and does not block it. For every position the
moment of the count and the book stock at that moment are kept; the discrepancy is the counted
minus that book stock. The count changes nothing. After submission a user with the separate
right confirms the adjustment with a reason — the author may confirm their own count — and the
discrepancy is applied to the current stock, keeping later movements; or the user rejects it,
the stock stays and the count can be corrected and sent again. The stock never goes below zero.

The movement journal is written at the moment of each movement and never back-dated, so the
book stock at the moment of a count cannot change afterwards; a confirmation that would make
the current stock negative asks for a recount.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import ActivityContext, Company
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.platform.registry import allocate_public_id
from c4rsys.warehouse.models import (
    Asset,
    InventoryCount,
    InventoryLine,
    Item,
    Lot,
    LotLayer,
    LotStock,
    OpenedPackage,
    Receipt,
    ReceiptLine,
    StockMovement,
)
from c4rsys.warehouse.stock import ZERO, company_currency

EDITABLE = (InventoryCount.State.DRAFT, InventoryCount.State.REJECTED)
COUNTED_KINDS = (InventoryLine.Kind.ASSET, InventoryLine.Kind.LOT)


def _warehouse(company_id: UUID, context_id: UUID | None) -> ActivityContext:
    if context_id is None:
        raise errors.validation_error("context_id", code="FIELD_REQUIRED")
    context = locations.storage_context(company_id, context_id)
    if context is None:
        raise errors.validation_error("context_id", code="WAREHOUSE_NOT_USABLE")
    return context


def create(
    ctx: CommandContext, company: Company, context_id: UUID | None, comment: str
) -> InventoryCount:
    context = _warehouse(company.id, context_id)
    count = InventoryCount.objects.create(
        company=company,
        number=next_number(company.id, "warehouse_inventory"),
        context=context,
        comment=comment,
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "warehouse.inventory_created",
        object_type="warehouse_inventory",
        object_id=count.id,
        after={"number": count.number, "context": str(context.id), "comment": comment},
    )
    return count


def _lock(company_id: UUID, count_id: UUID, states: tuple[str, ...]) -> InventoryCount:
    count = (
        InventoryCount.objects.select_for_update()
        .filter(company_id=company_id, pk=count_id)
        .first()
    )
    if count is None:
        raise errors.not_found()
    if count.state not in states:
        raise errors.business_rule("INVENTORY_STATE", state=count.state)
    return count


def _touch(count: InventoryCount) -> None:
    count.version += 1
    count.save(update_fields=["version"])


# --- Lines ---------------------------------------------------------------------------------------


def _next_position(count: InventoryCount) -> int:
    last = count.lines.order_by("-position").values_list("position", flat=True).first()
    return 0 if last is None else last + 1


def add_items(
    ctx: CommandContext, company_id: UUID, count_id: UUID, item_ids: tuple[UUID, ...] | None
) -> InventoryCount:
    """Positions to count: the units in stock of an individual item, or every form of every lot
    of a material — closed packages of each packaging, loose material, each opened package.
    Without items — the whole warehouse."""
    count = _lock(company_id, count_id, EDITABLE)
    items = Item.objects.filter(company_id=company_id)
    if item_ids is not None:
        if not item_ids:
            raise errors.validation_error("item_ids", code="FIELD_REQUIRED")
        items = items.filter(pk__in=item_ids)
        if items.count() != len(set(item_ids)):
            raise errors.validation_error("item_ids", code="ITEM_UNKNOWN")
    item_set = set(items.values_list("id", flat=True))
    existing = {
        (line.asset_id, line.lot_id, line.packaging_id, line.opened_package_id)
        for line in count.lines.filter(kind__in=COUNTED_KINDS)
    }
    position = _next_position(count)
    new: list[InventoryLine] = []

    def add(**data: Any) -> None:
        nonlocal position
        key = (
            data.get("asset_id"),
            data.get("lot_id"),
            data.get("packaging_id"),
            data.get("opened_package_id"),
        )
        if key in existing:
            return
        existing.add(key)
        new.append(InventoryLine(company_id=company_id, count=count, position=position, **data))
        position += 1

    for asset in Asset.objects.filter(
        company_id=company_id,
        context=count.context,
        state=Asset.State.IN_STOCK,
        item_id__in=item_set,
    ).order_by("item__name", "created_at"):
        add(kind=InventoryLine.Kind.ASSET, item_id=asset.item_id, asset_id=asset.id)
    lots = Lot.objects.filter(
        company_id=company_id, context=count.context, item_id__in=item_set
    ).order_by("item__name", "created_at")
    for lot in lots.prefetch_related("stock", "opened_packages"):
        for row in sorted(
            lot.stock.all(), key=lambda r: (r.packaging_id is None, str(r.packaging_id))
        ):
            if row.quantity > 0:
                add(
                    kind=InventoryLine.Kind.LOT,
                    item_id=lot.item_id,
                    lot_id=lot.id,
                    packaging_id=row.packaging_id,
                )
        for opened in lot.opened_packages.all():
            if opened.remaining > 0:
                add(
                    kind=InventoryLine.Kind.LOT,
                    item_id=lot.item_id,
                    lot_id=lot.id,
                    packaging_id=opened.packaging_id,
                    opened_package_id=opened.id,
                )
    InventoryLine.objects.bulk_create(new)
    _touch(count)
    ctx.audit(
        "warehouse.inventory_items_added",
        object_type="warehouse_inventory",
        object_id=count.id,
        after={
            "items": [str(i) for i in item_ids] if item_ids is not None else "all",
            "lines": len(new),
        },
    )
    return count


def book_now(line: InventoryLine, context_id: UUID) -> Decimal:
    """The book stock of the position now."""
    if line.kind == InventoryLine.Kind.ASSET:
        asset = line.asset
        return (
            Decimal(1)
            if asset and asset.state == Asset.State.IN_STOCK and asset.context_id == context_id
            else ZERO
        )
    if line.kind != InventoryLine.Kind.LOT:
        return ZERO
    if line.opened_package_id:
        return OpenedPackage.objects.get(pk=line.opened_package_id).remaining
    row = LotStock.objects.filter(lot=line.lot, packaging=line.packaging).first()
    return row.quantity if row else ZERO


@dataclass(frozen=True)
class Counted:
    line_id: UUID
    quantity: Decimal

    def as_dict(self) -> dict[str, str]:
        return {"line_id": str(self.line_id), "quantity": str(self.quantity)}


def set_counted(
    ctx: CommandContext, company_id: UUID, count_id: UUID, entries: tuple[Counted, ...]
) -> InventoryCount:
    """The counted quantity with the moment of the count and the book stock at that moment
    (§ 11.6.4). A unit is found or not; closed packages are counted whole."""
    count = _lock(company_id, count_id, EDITABLE)
    if not entries:
        raise errors.validation_error("lines", code="LINES_REQUIRED")
    lines = {
        line.id: line
        for line in count.lines.select_for_update(of=("self",))
        .select_related("asset", "packaging")
        .filter(pk__in=[e.line_id for e in entries])
    }
    now = timezone.now()
    for index, entry in enumerate(entries):
        line = lines.get(entry.line_id)
        if line is None or line.kind not in COUNTED_KINDS:
            raise errors.validation_error(f"lines[{index}].line_id", code="INVENTORY_LINE_UNKNOWN")
        if line.kind == InventoryLine.Kind.ASSET and entry.quantity not in (0, 1):
            raise errors.validation_error(f"lines[{index}].quantity", code="QUANTITY_WHOLE_UNITS")
        if (
            line.kind == InventoryLine.Kind.LOT
            and line.packaging is not None
            and line.opened_package_id is None
            and entry.quantity % line.packaging.quantity
        ):
            raise errors.validation_error(
                f"lines[{index}].quantity", code="QUANTITY_NOT_WHOLE_PACKS"
            )
        line.book_quantity = book_now(line, count.context_id)
        line.counted_quantity = entry.quantity
        line.counted_at = now
        line.counted_by_user_id = ctx.actor.user_id
        line.save(
            update_fields=["book_quantity", "counted_quantity", "counted_at", "counted_by_user_id"]
        )
    _touch(count)
    ctx.audit(
        "warehouse.inventory_counted",
        object_type="warehouse_inventory",
        object_id=count.id,
        after={"lines": [e.as_dict() for e in entries]},
    )
    return count


def add_found(
    ctx: CommandContext,
    company_id: UUID,
    count_id: UUID,
    kind: str,
    item_id: UUID | None,
    asset_public_id: str,
    description: str,
    identifiers: str,
) -> InventoryCount:
    """§ 11.2.1: a unit already in the system but registered elsewhere is recorded as a
    discrepancy, no new card; an unaccounted unit (not a vehicle) is described with its
    identifiers and gets a card and a receipt after the confirmed surplus; a found vehicle is
    only recorded — it is created through the garage and transferred to the warehouse."""
    count = _lock(company_id, count_id, EDITABLE)
    now = timezone.now()
    data: dict[str, Any] = {
        "company_id": company_id,
        "count": count,
        "kind": kind,
        "description": description,
        "identifiers": identifiers,
        "book_quantity": ZERO,
        "counted_quantity": Decimal(1),
        "counted_at": now,
        "counted_by_user_id": ctx.actor.user_id,
        "position": _next_position(count),
    }
    if kind == InventoryLine.Kind.FOUND_UNIT:
        item = Item.objects.filter(company_id=company_id, pk=item_id or UUID(int=0)).first()
        if item is None:
            raise errors.validation_error("item_id", code="ITEM_UNKNOWN")
        if item.accounting != Item.Accounting.INDIVIDUAL:
            raise errors.validation_error("item_id", code="FOUND_UNIT_PIECES_ONLY")
        if not description.strip():
            raise errors.validation_error("description", code="FIELD_REQUIRED")
        data["item"] = item
    elif kind == InventoryLine.Kind.FOUND_ELSEWHERE:
        asset = Asset.objects.filter(
            company_id=company_id, public_id=asset_public_id.strip().upper()
        ).first()
        if asset is None:
            raise errors.validation_error("asset_public_id", code="ASSET_UNKNOWN")
        if asset.state == Asset.State.IN_STOCK and asset.context_id == count.context_id:
            raise errors.business_rule("ASSET_IS_HERE", asset=asset.public_id)
        data.update(item_id=asset.item_id, asset=asset)
    elif kind == InventoryLine.Kind.FOUND_VEHICLE:
        if not description.strip():
            raise errors.validation_error("description", code="FIELD_REQUIRED")
    else:
        raise errors.validation_error("kind")
    line = InventoryLine.objects.create(**data)
    _touch(count)
    ctx.audit(
        "warehouse.inventory_found",
        object_type="warehouse_inventory",
        object_id=count.id,
        after={
            "line": str(line.id),
            "kind": kind,
            "description": description,
            "identifiers": identifiers,
        },
    )
    return count


def remove_line(
    ctx: CommandContext, company_id: UUID, count_id: UUID, line_id: UUID
) -> InventoryCount:
    count = _lock(company_id, count_id, EDITABLE)
    line = count.lines.filter(pk=line_id).first()
    if line is None:
        raise errors.not_found()
    line.delete()
    _touch(count)
    ctx.audit(
        "warehouse.inventory_line_removed",
        object_type="warehouse_inventory",
        object_id=count.id,
        before={"line": str(line_id)},
    )
    return count


def submit(ctx: CommandContext, company_id: UUID, count_id: UUID) -> InventoryCount:
    count = _lock(company_id, count_id, EDITABLE)
    lines = list(count.lines.all())
    if not lines:
        raise errors.business_rule("INVENTORY_EMPTY")
    missing = [
        line.position
        for line in lines
        if line.kind in COUNTED_KINDS and line.counted_quantity is None
    ]
    if missing:
        raise errors.business_rule("INVENTORY_NOT_COUNTED", count=len(missing))
    count.state = InventoryCount.State.SUBMITTED
    count.submitted_by_user_id = ctx.actor.user_id
    count.submitted_at = timezone.now()
    count.version += 1
    count.save(update_fields=["state", "submitted_by_user_id", "submitted_at", "version"])
    ctx.audit(
        "warehouse.inventory_submitted",
        object_type="warehouse_inventory",
        object_id=count.id,
        after={"state": count.state},
    )
    return count


def reject(ctx: CommandContext, company_id: UUID, count_id: UUID, reason: str) -> InventoryCount:
    """The stock stays; the data can be corrected and sent again (§ 11.6.3)."""
    count = _lock(company_id, count_id, (InventoryCount.State.SUBMITTED,))
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    count.state = InventoryCount.State.REJECTED
    count.decided_by_user_id = ctx.actor.user_id
    count.decided_at = timezone.now()
    count.decision_reason = reason
    count.version += 1
    count.save(
        update_fields=["state", "decided_by_user_id", "decided_at", "decision_reason", "version"]
    )
    ctx.audit(
        "warehouse.inventory_rejected",
        object_type="warehouse_inventory",
        object_id=count.id,
        after={"state": count.state},
        reason=reason,
    )
    return count


# --- Confirmation ------------------------------------------------------------------------------


def _movement(
    ctx: CommandContext, count: InventoryCount, line: InventoryLine, quantity: Decimal, **data: Any
) -> None:
    if line.item is None:
        raise errors.business_rule("ITEM_UNKNOWN")
    StockMovement.objects.create(
        company_id=count.company_id,
        kind=StockMovement.Kind.ADJUSTMENT,
        context=count.context,
        item=line.item,
        quantity=quantity,
        actor_user_id=ctx.actor.user_id,
        **data,
    )


def _reduce_layers(lot: Lot, amount: Decimal) -> None:
    left = amount
    for layer in (
        LotLayer.objects.select_for_update()
        .filter(lot=lot, remaining__gt=0)
        .order_by("received_at", "id")
    ):
        part = min(layer.remaining, left)
        layer.remaining -= part
        layer.save(update_fields=["remaining"])
        left -= part
        if left == 0:
            return
    raise errors.business_rule("INVENTORY_RECHECK")


def _apply_lot(
    ctx: CommandContext, count: InventoryCount, line: InventoryLine, delta: Decimal
) -> None:
    if line.lot is None:
        raise errors.business_rule("INVENTORY_RECHECK")
    lot = Lot.objects.select_for_update().get(pk=line.lot.pk)
    if line.opened_package_id:
        holder: Any = OpenedPackage.objects.select_for_update().get(pk=line.opened_package_id)
        field = "remaining"
    else:
        holder, _ = LotStock.objects.select_for_update().get_or_create(
            company_id=count.company_id, lot=lot, packaging=line.packaging
        )
        field = "quantity"
    current = getattr(holder, field)
    if current + delta < 0:
        # § 11.6.4: the current stock never goes below zero — the position is recounted.
        raise errors.business_rule(
            "INVENTORY_RECHECK", line=line.position + 1, available=str(current)
        )
    setattr(holder, field, current + delta)
    holder.save(update_fields=[field])
    if delta < 0:
        _reduce_layers(lot, -delta)
    else:
        # A surplus without a known cost (decision of 25.10.2026).
        LotLayer.objects.create(
            company_id=count.company_id,
            lot=lot,
            inventory_line=line,
            received_quantity=delta,
            remaining=delta,
            unit_cost=None,
            received_at=timezone.now(),
        )
    _movement(
        ctx,
        count,
        line,
        delta,
        lot=lot,
        packaging_id=None if line.opened_package_id else line.packaging_id,
        opened_package_id=line.opened_package_id,
    )


def confirm(ctx: CommandContext, company: Company, count_id: UUID, reason: str) -> InventoryCount:
    """The discrepancy of each position is applied to the current stock, not the counted value
    in place of it (§ 11.6.4). Before and after, the reason, the counter, the confirming user and
    the time are kept (§ 11.6.3). Unaccounted units get a card and a receipt."""
    count = _lock(company.id, count_id, (InventoryCount.State.SUBMITTED,))
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    applied = []
    found_units: list[InventoryLine] = []
    lines = (
        count.lines.select_for_update(of=("self",))
        .select_related("asset", "item")
        .order_by("position")
    )
    for line in lines:
        delta = ZERO
        if (
            line.kind == InventoryLine.Kind.ASSET
            and line.counted_quantity is not None
            and line.book_quantity is not None
        ):
            delta = line.counted_quantity - line.book_quantity
            if delta < 0:
                if line.asset is None:
                    raise errors.business_rule("INVENTORY_RECHECK")
                asset = Asset.objects.select_for_update().get(pk=line.asset.pk)
                if asset.state != Asset.State.IN_STOCK or asset.context_id != count.context_id:
                    raise errors.business_rule("INVENTORY_RECHECK", line=line.position + 1)
                asset.state = Asset.State.WRITTEN_OFF
                asset.save(update_fields=["state"])
                _movement(ctx, count, line, Decimal(-1), asset=asset)
            else:
                # A unit not in stock at the count but found: a discrepancy for a check, then a
                # move or a correction of the movement (§ 11.2.1).
                delta = ZERO
        elif (
            line.kind == InventoryLine.Kind.LOT
            and line.counted_quantity is not None
            and line.book_quantity is not None
        ):
            delta = line.counted_quantity - line.book_quantity
            if delta != 0:
                _apply_lot(ctx, count, line, delta)
        elif line.kind == InventoryLine.Kind.FOUND_UNIT:
            found_units.append(line)
            delta = Decimal(1)
        line.applied_delta = delta
        line.save(update_fields=["applied_delta"])
        if delta != 0:
            applied.append({"line": str(line.id), "delta": str(delta)})
    if found_units:
        _receive_found(ctx, company, count, found_units)
    count.state = InventoryCount.State.CONFIRMED
    count.decided_by_user_id = ctx.actor.user_id
    count.decided_at = timezone.now()
    count.decision_reason = reason
    count.version += 1
    count.save(
        update_fields=["state", "decided_by_user_id", "decided_at", "decision_reason", "version"]
    )
    ctx.audit(
        "warehouse.inventory_confirmed",
        object_type="warehouse_inventory",
        object_id=count.id,
        after={"state": count.state, "applied": applied},
        reason=reason,
    )
    return count


def _receive_found(
    ctx: CommandContext, company: Company, count: InventoryCount, found: list[InventoryLine]
) -> None:
    """A confirmed surplus of unaccounted units: a card for each and a posted receipt with the
    inventory as its source (§ 11.2.1–11.2.2), the cost not given."""
    now = timezone.now()
    receipt = Receipt.objects.create(
        company=company,
        number=next_number(company.id, "warehouse_receipt"),
        context=count.context,
        source=Receipt.Source.INVENTORY,
        currency=company_currency(company).code,
        comment=f"inventory {count.number}",
        state=Receipt.State.POSTED,
        received_at=now,
        created_by_user_id=ctx.actor.user_id,
        posted_by_user_id=ctx.actor.user_id,
        posted_at=now,
    )
    for index, line in enumerate(found):
        if line.item is None:
            raise errors.business_rule("ITEM_UNKNOWN")
        receipt_line = ReceiptLine.objects.create(
            company_id=company.id,
            receipt=receipt,
            item=line.item,
            quantity=Decimal(1),
            serial_numbers=[line.identifiers] if line.identifiers else [],
            position=index,
        )
        asset = Asset(
            company_id=company.id,
            item=line.item,
            context=count.context,
            serial_number=line.identifiers,
            currency=receipt.currency,
            receipt_line=receipt_line,
        )
        asset.public_id = allocate_public_id(
            "AST", entity_type="warehouse_asset", entity_id=asset.id
        )
        asset.save(force_insert=True)
        StockMovement.objects.create(
            company_id=company.id,
            kind=StockMovement.Kind.RECEIPT,
            context=count.context,
            item=line.item,
            asset=asset,
            quantity=Decimal(1),
            receipt_line=receipt_line,
            actor_user_id=ctx.actor.user_id,
        )
        line.created_asset = asset
        line.save(update_fields=["created_asset"])


# --- Reading ------------------------------------------------------------------------------------


def counts_of(company_id: UUID) -> QuerySet[InventoryCount]:
    return (
        InventoryCount.objects.filter(company_id=company_id)
        .select_related("context__location")
        .prefetch_related(
            "lines__item", "lines__asset", "lines__lot", "lines__packaging", "lines__created_asset"
        )
        .order_by("-number")
    )


def get_count(company_id: UUID, count_id: UUID) -> InventoryCount:
    count = counts_of(company_id).filter(pk=count_id).first()
    if count is None:
        raise errors.not_found()
    return count
