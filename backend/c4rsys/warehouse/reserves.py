"""Reserves (ТЗ § 11.7.1, § 11.7.7, § 11.8.1, § 11.9.1).

A reserved quantity stays in the warehouse but is unavailable to other operations: issue,
consumption, move, write-off, repacking, sale and new reserves take only what is free. Goods of
an open order are reserved by their sale line without a term. A separate warehouse reserve
(StockReserve) is for a definite operation: a manual one may have a term, past it an unused
reserve is released automatically; a reserve of a planned service work has none. An issue for
the purpose uses the reserve; a cancel releases it. A reserve never exceeds the free stock; what
is issued or consumed is not free.

PROPOSAL (block 15): a reserve in closed packages holds whole packages; a reserve is made by the
right «Reserve» in its warehouse; a manual reserve has a comment saying what it is for.
"""

from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import F, Q, QuerySet, Sum
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import Company
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.platform.models import AuditEvent
from c4rsys.warehouse.models import (
    Asset,
    Item,
    Lot,
    LotStock,
    OpenedPackage,
    Packaging,
    SaleLine,
    StockReserve,
)

ZERO = Decimal("0.00")


def active_reserves(now: datetime | None = None) -> QuerySet[StockReserve]:
    """Active and not past their term: an expired reserve no longer holds the stock even before
    the periodic check marks it."""
    moment = now or timezone.now()
    return StockReserve.objects.filter(state=StockReserve.State.ACTIVE).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=moment)
    )


def _in_form(
    found: Any, lot_id: UUID, packaging_id: UUID | None, opened_package_id: UUID | None
) -> Any:
    found = found.filter(lot_id=lot_id)
    if opened_package_id is not None:
        return found.filter(opened_package_id=opened_package_id)
    found = found.filter(opened_package__isnull=True)
    return (
        found.filter(packaging__isnull=True)
        if packaging_id is None
        else found.filter(packaging_id=packaging_id)
    )


def reserved_in_form(
    lot_id: UUID,
    packaging_id: UUID | None,
    opened_package_id: UUID | None,
    exclude_line: UUID | None = None,
    exclude_reserve: UUID | None = None,
) -> Decimal:
    """Reserved in one form of a lot: closed packages of a packaging, loose material or an
    opened package — by sale lines of open orders and by warehouse reserves."""
    lines = _in_form(
        SaleLine.objects.filter(state=SaleLine.State.RESERVED),
        lot_id,
        packaging_id,
        opened_package_id,
    )
    if exclude_line is not None:
        lines = lines.exclude(pk=exclude_line)
    total = lines.aggregate(total=Sum("quantity"))["total"] or ZERO
    held = _in_form(active_reserves(), lot_id, packaging_id, opened_package_id)
    if exclude_reserve is not None:
        held = held.exclude(pk=exclude_reserve)
    total += held.aggregate(total=Sum(F("quantity") - F("used_quantity")))["total"] or ZERO
    return Decimal(total)


def asset_reserved(asset_id: UUID, exclude_reserve: UUID | None = None) -> bool:
    if SaleLine.objects.filter(state=SaleLine.State.RESERVED, asset_id=asset_id).exists():
        return True
    held = active_reserves().filter(asset_id=asset_id)
    if exclude_reserve is not None:
        held = held.exclude(pk=exclude_reserve)
    return held.exists()


def vehicle_reserved(card_id: UUID) -> bool:
    return SaleLine.objects.filter(state=SaleLine.State.RESERVED, vehicle_id=card_id).exists()


def reserved_totals(company_id: UUID, context_ids: set[UUID]) -> dict[tuple[UUID, UUID], Decimal]:
    """(item, warehouse) -> reserved quantity, by open orders and by warehouse reserves."""
    result: dict[tuple[UUID, UUID], Decimal] = {}
    for row in (
        SaleLine.objects.filter(
            company_id=company_id,
            state=SaleLine.State.RESERVED,
            context_id__in=context_ids,
            item__isnull=False,
        )
        .values("item_id", "context_id")
        .annotate(total=Sum("quantity"))
    ):
        key = (row["item_id"], row["context_id"])
        result[key] = result.get(key, ZERO) + row["total"]
    for row in (
        active_reserves()
        .filter(company_id=company_id, context_id__in=context_ids)
        .values("item_id", "context_id")
        .annotate(total=Sum(F("quantity") - F("used_quantity")))
    ):
        key = (row["item_id"], row["context_id"])
        result[key] = result.get(key, ZERO) + row["total"]
    return result


# --- Commands -------------------------------------------------------------------------------------


def snapshot(reserve: StockReserve) -> dict[str, Any]:
    return {
        "number": reserve.number,
        "purpose": reserve.purpose,
        "source": str(reserve.source_id) if reserve.source_id else None,
        "context": str(reserve.context_id),
        "item": str(reserve.item_id),
        "asset": str(reserve.asset_id) if reserve.asset_id else None,
        "lot": str(reserve.lot_id) if reserve.lot_id else None,
        "packaging": str(reserve.packaging_id) if reserve.packaging_id else None,
        "opened_package": str(reserve.opened_package_id) if reserve.opened_package_id else None,
        "quantity": str(reserve.quantity),
        "used": str(reserve.used_quantity),
        "state": reserve.state,
        "expires_at": reserve.expires_at.isoformat() if reserve.expires_at else None,
        "comment": reserve.comment,
    }


def create(
    ctx: CommandContext,
    company: Company,
    context_id: UUID,
    *,
    item_id: UUID,
    asset_id: UUID | None = None,
    lot_id: UUID | None = None,
    packaging_id: UUID | None = None,
    opened_package_id: UUID | None = None,
    quantity: Decimal | None = None,
    purpose: str = StockReserve.Purpose.MANUAL,
    source_id: UUID | None = None,
    expires_at: datetime | None = None,
    comment: str = "",
) -> StockReserve:
    """Reserves a free unit or a free quantity of one form (§ 11.7.1, § 11.8.1). The rows are
    locked and the free stock is counted on fresh data."""
    context = locations.storage_context(company.id, context_id)
    if context is None:
        raise errors.validation_error("context_id", code="WAREHOUSE_NOT_USABLE")
    if purpose == StockReserve.Purpose.MANUAL:
        if not comment.strip():
            raise errors.validation_error("comment", code="FIELD_REQUIRED")
        if expires_at is not None and expires_at <= timezone.now():
            raise errors.validation_error("expires_at", code="RESERVE_TERM_PAST")
    elif expires_at is not None:
        raise errors.validation_error("expires_at", code="FIELD_NOT_APPLICABLE")
    item = Item.objects.filter(company_id=company.id, pk=item_id).first()
    if item is None:
        raise errors.validation_error("item_id", code="ITEM_UNKNOWN")
    reserve = StockReserve(
        company=company,
        purpose=purpose,
        source_id=source_id,
        context=context,
        item=item,
        expires_at=expires_at,
        comment=comment,
        created_by_user_id=ctx.actor.user_id,
    )
    if item.accounting == Item.Accounting.INDIVIDUAL:
        if asset_id is None or lot_id is not None:
            raise errors.validation_error("asset_id", code="FIELD_REQUIRED")
        asset = (
            Asset.objects.select_for_update()
            .filter(company_id=company.id, pk=asset_id, item=item)
            .first()
        )
        if asset is None:
            raise errors.validation_error("asset_id", code="ASSET_UNKNOWN")
        if (
            asset.context_id != context.id
            or asset.state != Asset.State.IN_STOCK
            or asset_reserved(asset.id)
        ):
            raise errors.business_rule("ASSET_NOT_AVAILABLE", asset=asset.public_id)
        reserve.asset = asset
        reserve.quantity = Decimal(1)
    else:
        if lot_id is None or asset_id is not None:
            raise errors.validation_error("lot_id", code="FIELD_REQUIRED")
        if quantity is None:
            raise errors.validation_error("quantity", code="FIELD_REQUIRED")
        lot = (
            Lot.objects.select_for_update()
            .filter(company_id=company.id, pk=lot_id, item=item, context=context)
            .first()
        )
        if lot is None:
            raise errors.validation_error("lot_id", code="LOT_UNKNOWN")
        reserve.lot = lot
        if opened_package_id is not None:
            opened = (
                OpenedPackage.objects.select_for_update()
                .filter(company_id=company.id, pk=opened_package_id, lot=lot)
                .first()
            )
            if opened is None:
                raise errors.validation_error("opened_package_id", code="OPENED_PACKAGE_UNKNOWN")
            physical = opened.remaining
            reserve.opened_package = opened
            reserve.packaging_id = opened.packaging_id
            needed = quantity
        else:
            packaging = None
            if packaging_id is not None:
                packaging = Packaging.objects.filter(item=item, pk=packaging_id).first()
                if packaging is None:
                    raise errors.validation_error("packaging_id", code="PACKAGING_UNKNOWN")
            row = LotStock.objects.select_for_update().filter(lot=lot, packaging=packaging).first()
            physical = row.quantity if row else ZERO
            reserve.packaging = packaging
            needed = quantity
            if packaging is not None:
                # Closed packages are held whole: a part of a package opens it only at the issue.
                size = packaging.quantity
                needed = size * (quantity / size).to_integral_value(rounding=ROUND_CEILING)
        free = physical - reserved_in_form(lot.id, reserve.packaging_id, opened_package_id)
        if needed > free:
            raise errors.business_rule(
                "STOCK_NOT_ENOUGH", field="quantity", available=str(max(free, ZERO))
            )
        reserve.quantity = needed
    reserve.number = next_number(company.id, "warehouse_reserve")
    reserve.save(force_insert=True)
    ctx.audit(
        "warehouse.reserved",
        object_type="warehouse_reserve",
        object_id=reserve.id,
        after=snapshot(reserve),
        reason=comment,
    )
    return reserve


def lock(company_id: UUID, reserve_id: UUID) -> StockReserve:
    reserve = (
        StockReserve.objects.select_for_update()
        .filter(company_id=company_id, pk=reserve_id)
        .first()
    )
    if reserve is None:
        raise errors.not_found()
    return reserve


def release(ctx: CommandContext, company_id: UUID, reserve_id: UUID, reason: str) -> StockReserve:
    """A cancel frees the reserve without any consumption (§ 11.7.1)."""
    reserve = lock(company_id, reserve_id)
    if reserve.state != StockReserve.State.ACTIVE:
        raise errors.business_rule("RESERVE_NOT_ACTIVE", state=reserve.state)
    before = snapshot(reserve)
    reserve.state = StockReserve.State.RELEASED
    reserve.ended_at = timezone.now()
    reserve.ended_by_user_id = ctx.actor.user_id
    reserve.end_reason = reason
    reserve.version += 1
    reserve.save()
    ctx.audit(
        "warehouse.reserve_released",
        object_type="warehouse_reserve",
        object_id=reserve.id,
        before={"state": before["state"]},
        after={"state": reserve.state},
        reason=reason,
    )
    return reserve


def use(ctx: CommandContext, reserve: StockReserve, amount: Decimal) -> None:
    """The issue for the purpose takes from its reserve; the reserve is used when all is taken.
    A smaller issue of closed packages leaves the rest of the reserve held (PROPOSAL)."""
    reserve.used_quantity = min(reserve.quantity, reserve.used_quantity + amount)
    if reserve.used_quantity >= reserve.quantity:
        reserve.state = StockReserve.State.USED
        reserve.ended_at = timezone.now()
        reserve.ended_by_user_id = ctx.actor.user_id
    reserve.version += 1
    reserve.save()
    ctx.audit(
        "warehouse.reserve_used",
        object_type="warehouse_reserve",
        object_id=reserve.id,
        after={"used": str(reserve.used_quantity), "state": reserve.state},
    )


def check_take(reserve: StockReserve, context_id: UUID, item_id: UUID) -> None:
    if reserve.state != StockReserve.State.ACTIVE or (
        reserve.expires_at is not None and reserve.expires_at <= timezone.now()
    ):
        raise errors.business_rule("RESERVE_NOT_ACTIVE", state=reserve.state)
    if reserve.context_id != context_id or reserve.item_id != item_id:
        raise errors.validation_error("reserve_id", code="RESERVE_NOT_FOR_THIS")


def expire(now: datetime) -> int:
    """§ 11.7.1: a separate reserve past its term is released automatically (a system process
    of the notification checks). Each is journaled in the same transaction."""
    changed = 0
    with transaction.atomic():
        changed = _expire(now)
    return changed


def _expire(now: datetime) -> int:
    changed = 0
    for reserve in StockReserve.objects.select_for_update().filter(
        state=StockReserve.State.ACTIVE, expires_at__isnull=False, expires_at__lte=now
    ):
        reserve.state = StockReserve.State.EXPIRED
        reserve.ended_at = now
        reserve.version += 1
        reserve.save()
        AuditEvent.objects.create(
            company_id=reserve.company_id,
            system_process="warehouse_reserve_expiry",
            action="warehouse.reserve_expired",
            object_type="warehouse_reserve",
            object_id=str(reserve.id),
            before={"state": StockReserve.State.ACTIVE},
            after={"state": reserve.state, "expires_at": reserve.expires_at},
        )
        changed += 1
    return changed
