"""Sale of goods and vehicles from the warehouse through an order (ТЗ § 8.3.9, § 11.1.17,
§ 11.3.2, § 11.7.4–11.7.6, § 11.9; decisions of 25.10.2026).

Adding a line to an open client order reserves the units or the quantity and keeps the snapshot
of the applied price; the charge of the order follows the line. Removing the line releases the
reserve without consumption. Closing the order sells: the reserve is used and the stock goes
down; a vehicle's ownership ends. A wrongly closed sale of goods is cancelled after the order is
reopened, by the separate right and with a reason; the goods return to the warehouse and the
charge is corrected — payments stay and become an overpayment. No discounts in this part.

PROPOSAL: loose sale comes from loose material or an opened package; closed packages are sold
whole (to pour from a closed package it is first repacked).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import Company
from c4rsys.finance import charges
from c4rsys.fleet import services as fleet
from c4rsys.orders import services as orders
from c4rsys.orders.models import Order
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.platform.money import round_money
from c4rsys.warehouse import operations, prices, reserves
from c4rsys.warehouse.models import (
    Asset,
    Item,
    Lot,
    LotStock,
    OpenedPackage,
    OperationLine,
    Packaging,
    SaleLine,
    StockMovement,
    StockOperation,
)
from c4rsys.warehouse.stock import ZERO, company_currency, expired

SOURCE = "sale_line"


@dataclass(frozen=True)
class SaleData:
    """What is sold: units of an item, a quantity of a lot from one form, or a vehicle."""

    context_id: UUID | None
    item_id: UUID | None = None
    asset_ids: tuple[UUID, ...] = ()
    lot_id: UUID | None = None
    packaging_id: UUID | None = None
    opened_package_id: UUID | None = None
    quantity: Decimal | None = None
    vehicle_id: UUID | None = None
    confirm_expired: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_id": str(self.context_id) if self.context_id else None,
            "item_id": str(self.item_id) if self.item_id else None,
            "asset_ids": [str(a) for a in self.asset_ids],
            "lot_id": str(self.lot_id) if self.lot_id else None,
            "packaging_id": str(self.packaging_id) if self.packaging_id else None,
            "opened_package_id": str(self.opened_package_id) if self.opened_package_id else None,
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "vehicle_id": str(self.vehicle_id) if self.vehicle_id else None,
            "confirm_expired": self.confirm_expired,
        }


def start_order(
    ctx: CommandContext, company: Company, client_id: UUID, employment_id: UUID | None
) -> Order:
    """A new client order for a sale from the warehouse (decision of 25.10.2026)."""
    from c4rsys.parties import services as clients

    clients.get_client(company.id, client_id)
    return orders.create_order(
        ctx, company_id=company.id, client_id=client_id, responsible_employment_id=employment_id
    )


def _charge(
    ctx: CommandContext, order: Order, line: SaleLine, amount: Decimal, reason: str
) -> None:
    if order.client_id is None:
        raise errors.business_rule("ORDER_WITHOUT_CLIENT")
    charges.set_charge(
        ctx,
        company_id=order.company_id,
        order_id=order.id,
        client_id=order.client_id,
        source_type=SOURCE,
        source_id=line.id,
        amount=amount,
        currency=line.currency,
        reason=reason,
        calculation={
            "quantity": str(line.quantity),
            "unit_price": str(line.unit_price),
            "amount": str(line.amount),
        },
    )


def _price(value: Decimal | None) -> Decimal:
    if value is None:
        # § 11.7.6: the price is set first by an employee with the right to set prices.
        raise errors.business_rule("PRICE_NOT_SET")
    return value


def add(ctx: CommandContext, company: Company, order_id: UUID, data: SaleData) -> list[SaleLine]:
    """§ 11.9.1: the line reserves at once, without a term while the order is open, and keeps the
    price snapshot (§ 11.3.2). Nothing beyond the free quantity is reserved (§ 11.8.1)."""
    order = orders.open_order_of_client(company.id, order_id)
    if data.context_id is None:
        raise errors.validation_error("context_id", code="FIELD_REQUIRED")
    context = locations.storage_context(company.id, data.context_id)
    if context is None:
        raise errors.validation_error("context_id", code="WAREHOUSE_NOT_USABLE")
    currency = company_currency(company)
    minor = currency.minor_units

    def line(units: Decimal, **fields: Any) -> SaleLine:
        """`units` — what the price is for: packages of a packaging, or base units."""
        created = SaleLine(
            company=company,
            order=order,
            context=context,
            currency=currency.code,
            created_by_user_id=ctx.actor.user_id,
            **fields,
        )
        created.amount = round_money(created.unit_price * units, minor)
        created.save(force_insert=True)
        _charge(ctx, order, created, created.amount, "sale")
        return created

    created: list[SaleLine] = []
    if data.vehicle_id is not None:
        card = fleet.stored_card(company.id, data.vehicle_id)
        if card is None or card.storage_context_id != context.id:
            raise errors.validation_error("vehicle_id", code="VEHICLE_NOT_IN_WAREHOUSE")
        if reserves.vehicle_reserved(card.id):
            raise errors.business_rule("VEHICLE_RESERVED")
        price = _price(prices.vehicle_price(company.id, card.id))
        created.append(line(Decimal(1), vehicle_id=card.id, quantity=Decimal(1), unit_price=price))
    else:
        item = Item.objects.filter(company_id=company.id, pk=data.item_id or UUID(int=0)).first()
        if item is None:
            raise errors.validation_error("item_id", code="ITEM_UNKNOWN")
        if item.accounting == Item.Accounting.INDIVIDUAL:
            if not data.asset_ids:
                raise errors.validation_error("asset_ids", code="FIELD_REQUIRED")
            for asset in Asset.objects.select_for_update().filter(
                company_id=company.id, item=item, pk__in=data.asset_ids
            ):
                if (
                    asset.context_id != context.id
                    or asset.state != Asset.State.IN_STOCK
                    or reserves.asset_reserved(asset.id)
                ):
                    raise errors.business_rule("ASSET_NOT_AVAILABLE", asset=asset.public_id)
                price = _price(prices.price_for(item, asset=asset))
                created.append(
                    line(Decimal(1), item=item, asset=asset, quantity=Decimal(1), unit_price=price)
                )
            if len(created) != len(set(data.asset_ids)):
                raise errors.validation_error("asset_ids", code="ASSET_UNKNOWN")
        else:
            created.append(_add_lot(company.id, item, context.id, data, line))
    ctx.audit(
        "warehouse.sale_added",
        object_type="order",
        object_id=order.id,
        after={"lines": [str(c.id) for c in created], **data.as_dict()},
    )
    return created


def _add_lot(company_id: UUID, item: Item, context_id: UUID, data: SaleData, line: Any) -> SaleLine:
    if data.quantity is None:
        raise errors.validation_error("quantity", code="FIELD_REQUIRED")
    lot = (
        Lot.objects.select_for_update()
        .filter(
            company_id=company_id, pk=data.lot_id or UUID(int=0), item=item, context_id=context_id
        )
        .first()
    )
    if lot is None:
        raise errors.validation_error("lot_id", code="LOT_UNKNOWN")
    if expired(lot):
        # PROPOSAL: the policy for expired lots (§ 11.2.15) applies to a sale as to an issue.
        operations._check_expired(company_id, lot, data.confirm_expired, "lot_id")
    amount = data.quantity
    if data.opened_package_id is not None:
        opened = (
            OpenedPackage.objects.select_for_update()
            .filter(pk=data.opened_package_id, lot=lot)
            .first()
        )
        if opened is None:
            raise errors.validation_error("opened_package_id", code="OPENED_PACKAGE_UNKNOWN")
        free = opened.remaining - reserves.reserved_in_form(lot.id, opened.packaging_id, opened.id)
        if amount > free:
            raise errors.business_rule("STOCK_NOT_ENOUGH", field="quantity", available=str(free))
        price = _price(prices.price_for(item, lot=lot))
        result: SaleLine = line(
            amount,
            item=item,
            lot=lot,
            packaging_id=opened.packaging_id,
            opened_package=opened,
            quantity=amount,
            unit_price=price,
        )
        return result
    packaging = None
    if data.packaging_id is not None:
        packaging = Packaging.objects.filter(item=item, pk=data.packaging_id).first()
        if packaging is None:
            raise errors.validation_error("packaging_id", code="PACKAGING_UNKNOWN")
        if amount % packaging.quantity:
            raise errors.validation_error("quantity", code="QUANTITY_NOT_WHOLE_PACKS")
    row = LotStock.objects.select_for_update().filter(lot=lot, packaging=packaging).first()
    free = (row.quantity if row else ZERO) - reserves.reserved_in_form(
        lot.id, packaging.id if packaging else None, None
    )
    if amount > free:
        raise errors.business_rule("STOCK_NOT_ENOUGH", field="quantity", available=str(free))
    price = _price(prices.price_for(item, lot=lot, packaging=packaging))
    units = amount / packaging.quantity if packaging is not None else amount
    result = line(units, item=item, lot=lot, packaging=packaging, quantity=amount, unit_price=price)
    return result


def _lock_line(company_id: UUID, line_id: UUID) -> SaleLine:
    line = SaleLine.objects.select_for_update().filter(company_id=company_id, pk=line_id).first()
    if line is None:
        raise errors.not_found()
    return line


def remove(ctx: CommandContext, company_id: UUID, line_id: UUID) -> SaleLine:
    """The line leaves the open order: the reserve is released without consumption and the
    charge goes back to zero (§ 11.3.2, § 11.9.1)."""
    line = _lock_line(company_id, line_id)
    order = orders.open_order_of_client(company_id, line.order_id)
    if line.state != SaleLine.State.RESERVED:
        raise errors.business_rule("SALE_LINE_STATE", state=line.state)
    line.state = SaleLine.State.REMOVED
    line.removed_at = timezone.now()
    line.save(update_fields=["state", "removed_at"])
    _charge(ctx, order, line, ZERO, "sale_removed")
    ctx.audit(
        "warehouse.sale_removed",
        object_type="order",
        object_id=order.id,
        after={"line": str(line.id)},
    )
    return line


def on_order_close(ctx: CommandContext, company_id: UUID, order_id: UUID) -> None:
    """Closing the order uses the reserve: the goods leave the stock — the earlier receipts
    first — and a vehicle's ownership ends (§ 8.3.9, § 11.9.1)."""
    lines = list(
        SaleLine.objects.select_for_update(of=("self",))
        .select_related("lot", "packaging", "opened_package", "context")
        .filter(company_id=company_id, order_id=order_id, state=SaleLine.State.RESERVED)
        .order_by("created_at")
    )
    if not lines:
        return
    company = Company.objects.get(pk=company_id)
    by_context: dict[UUID, StockOperation] = {}
    now = timezone.now()
    for line in lines:
        if line.vehicle_id is not None:
            fleet.record_sale(ctx, company_id, line.vehicle_id, order_id)
        else:
            operation = by_context.get(line.context_id)
            if operation is None:
                operation = StockOperation.objects.create(
                    company=company,
                    number=next_number(company_id, "warehouse_operation"),
                    kind=StockOperation.Kind.SALE,
                    context=line.context,
                    comment=f"order {Order.objects.get(pk=order_id).number}",
                    created_by_user_id=ctx.actor.user_id,
                )
                by_context[line.context_id] = operation
            line.operation_line = _sell(ctx, operation, line)
        line.state = SaleLine.State.SOLD
        line.sold_at = now
        line.save(update_fields=["state", "sold_at", "operation_line"])
    ctx.audit(
        "warehouse.sold",
        object_type="order",
        object_id=order_id,
        after={
            "lines": [str(line.id) for line in lines],
            "operations": [o.number for o in by_context.values()],
        },
    )


def _sell(ctx: CommandContext, operation: StockOperation, line: SaleLine) -> OperationLine:
    if line.item is None:
        raise errors.business_rule("ITEM_UNKNOWN")
    op_line = OperationLine.objects.create(
        company_id=line.company_id,
        operation=operation,
        item=line.item,
        asset_id=line.asset_id,
        lot_id=line.lot_id,
        packaging_id=line.packaging_id,
        opened_package_id=line.opened_package_id,
        quantity=line.quantity,
    )
    if line.asset_id is not None:
        asset = Asset.objects.select_for_update().get(pk=line.asset_id)
        if asset.state != Asset.State.IN_STOCK:
            raise errors.business_rule("SALE_STOCK_MISSING", asset=asset.public_id)
        asset.state = Asset.State.SOLD
        asset.save(update_fields=["state"])
        operations._move(
            ctx, StockMovement.Kind.SALE, operation.context, op_line, Decimal(-1), asset=asset
        )
        return op_line
    lot = line.lot
    if lot is None:
        raise errors.business_rule("LOT_UNKNOWN")
    if line.opened_package_id is not None:
        holder: Any = OpenedPackage.objects.select_for_update().get(pk=line.opened_package_id)
        field = "remaining"
        piece = operations.Piece(line.quantity, opened=holder)
    else:
        holder = (
            LotStock.objects.select_for_update().filter(lot=lot, packaging=line.packaging).first()
        )
        field = "quantity"
        piece = operations.Piece(line.quantity, packaging=line.packaging)
    if holder is None or getattr(holder, field) < line.quantity:
        raise errors.business_rule("SALE_STOCK_MISSING", lot=lot.public_id)
    setattr(holder, field, getattr(holder, field) - line.quantity)
    holder.save(update_fields=[field])
    operations._fifo(op_line, lot, line.quantity)
    operations._move(
        ctx,
        StockMovement.Kind.SALE,
        operation.context,
        op_line,
        -line.quantity,
        lot=lot,
        piece=piece,
    )
    return op_line


def cancel(ctx: CommandContext, company_id: UUID, line_id: UUID, reason: str) -> SaleLine:
    """§ 11.9.5: after the closing of the order is cancelled, the wrongly closed sale is cancelled
    with a reason; the goods return to the available stock of their warehouse, the charge is
    corrected and payments stay as an overpayment. The record stays. A vehicle sale needs the
    new owner's confirmation and is cancelled later (decision of 25.10.2026)."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    line = _lock_line(company_id, line_id)
    order = orders.open_order_of_client(company_id, line.order_id)
    if line.state != SaleLine.State.SOLD:
        raise errors.business_rule("SALE_LINE_STATE", state=line.state)
    if line.vehicle_id is not None:
        raise errors.business_rule("VEHICLE_SALE_CORRECTION_LATER")
    op_line = line.operation_line
    if op_line is None:
        raise errors.business_rule("SALE_LINE_STATE", state=line.state)
    context = op_line.operation.context
    if line.asset_id is not None:
        asset = Asset.objects.select_for_update().get(pk=line.asset_id)
        asset.state = Asset.State.IN_STOCK
        asset.save(update_fields=["state"])
        operations._move(
            ctx, StockMovement.Kind.SALE_CANCEL, context, op_line, Decimal(1), asset=asset
        )
    else:
        lot = operations._lot_of(op_line)
        operations._restore_layers(op_line, line.quantity)
        piece = operations._put_back(lot, line.packaging, line.opened_package, line.quantity)
        operations._move(
            ctx,
            StockMovement.Kind.SALE_CANCEL,
            context,
            op_line,
            line.quantity,
            lot=lot,
            piece=piece,
        )
    line.state = SaleLine.State.CANCELLED
    line.cancelled_at = timezone.now()
    line.cancel_reason = reason
    line.save(update_fields=["state", "cancelled_at", "cancel_reason"])
    _charge(ctx, order, line, ZERO, "sale_cancelled")
    ctx.audit(
        "warehouse.sale_cancelled",
        object_type="order",
        object_id=order.id,
        after={"line": str(line.id)},
        reason=reason,
    )
    return line


# --- Providers for orders ---------------------------------------------------------------------


def on_order_cancel(ctx: CommandContext, company_id: UUID, order_id: UUID, reason: str) -> None:
    """Handler for orders: with the cancelled order its reserved goods are released (§ 12.2.1); a
    sold line is corrected by § 11.9.5 and keeps the order from cancelling."""
    for line in SaleLine.objects.select_for_update().filter(
        company_id=company_id, order_id=order_id
    ):
        if line.state == SaleLine.State.RESERVED:
            remove(ctx, company_id, line.id)
        elif line.state == SaleLine.State.SOLD:
            raise errors.business_rule("ORDER_HAS_STARTED_OPERATIONS", line_id=str(line.id))


def operations_for_order(company_id: UUID, order_id: UUID) -> list[orders.OperationSummary]:
    """Goods lines as operations of the order: always ready to close; closing needs the right of
    sale (§ 11.9.4)."""
    return [
        orders.OperationSummary(
            type="sale",
            id=line.id,
            state=line.state,
            location_id=line.context.location_id,
            finished=True,
        )
        for line in SaleLine.objects.select_related("context").filter(
            company_id=company_id,
            order_id=order_id,
            state__in=[SaleLine.State.RESERVED, SaleLine.State.SOLD],
        )
    ]


def history_for_order(company_id: UUID, order_id: UUID) -> list[tuple[str, str]]:
    return [("order", str(order_id))]


def lines_of_order(company_id: UUID, order_id: UUID) -> list[SaleLine]:
    return list(
        SaleLine.objects.select_related(
            "item", "asset", "lot", "packaging", "context__location", "vehicle"
        )
        .filter(company_id=company_id, order_id=order_id)
        .order_by("created_at")
    )


def client_view_for_order(company_id: UUID, order_id: UUID) -> list[dict[str, Any]]:
    """Purchases of an order for the client: the goods, the quantity and the amount (decision
    Б-1). A removed line was never sold and is not shown."""
    result: list[dict[str, Any]] = []
    for line in (
        SaleLine.objects.select_related("item", "vehicle")
        .filter(company_id=company_id, order_id=order_id)
        .exclude(state=SaleLine.State.REMOVED)
        .order_by("created_at")
    ):
        name = line.item.name if line.item else (line.vehicle.name if line.vehicle else "")
        result.append(
            {
                "type": "sale",
                "id": str(line.id),
                "state": line.state,
                "title": name,
                "quantity": str(line.quantity),
                "amount": str(line.amount),
                "currency": line.currency,
            }
        )
    return result
