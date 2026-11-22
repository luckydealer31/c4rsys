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

from collections.abc import Callable
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
    LotLayer,
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

# The service module says whether a work is done: goods linked to a done work are installed, and
# their sale is not simply cancelled (customer's decision of 22.11.2026).
_work_done: list[Callable[[UUID, UUID], bool]] = []


def register_work_done(checker: Callable[[UUID, UUID], bool]) -> None:
    _work_done.append(checker)


def installed(line: SaleLine) -> bool:
    """Linked to a work that is done."""
    return line.job_id is not None and any(
        check(line.company_id, line.job_id) for check in _work_done
    )


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
    # In an internal order: the vehicle of the company the goods are used on.
    for_vehicle_id: UUID | None = None

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
            "for_vehicle_id": str(self.for_vehicle_id) if self.for_vehicle_id else None,
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


def start_internal_order(
    ctx: CommandContext, company: Company, employment_id: UUID | None
) -> Order:
    """An internal order for goods used on a vehicle of the company (customer's decision of
    22.11.2026)."""
    return orders.create_internal_order(
        ctx, company_id=company.id, responsible_employment_id=employment_id
    )


def _order(company_id: UUID, order_id: UUID, *, new_operation: bool = False) -> Order:
    """The open order of the goods, locked: of a client or an internal one."""
    order = orders.lock_open_order(company_id, order_id)
    if order.is_internal:
        return order
    return orders.open_order_of_client(company_id, order_id, new_operation=new_operation)


def _charge(
    ctx: CommandContext, order: Order, line: SaleLine, amount: Decimal, reason: str
) -> None:
    if order.is_internal:
        # An internal order has no client and no client charges (§ 12.1.3).
        return
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
    order = _order(company.id, order_id, new_operation=True)
    # Customer's decisions of 22.11.2026: in an internal order the goods are used on a vehicle
    # of the company — on an own one at the purchase cost, on a managed one at the sale price.
    target = None
    if order.is_internal:
        if data.vehicle_id is not None:
            raise errors.business_rule("ORDER_WITHOUT_CLIENT", field="order_id")
        if data.for_vehicle_id is None:
            raise errors.validation_error("for_vehicle_id", code="FIELD_REQUIRED")
        target = fleet.company_vehicle(company.id, data.for_vehicle_id)
        if target is None:
            raise errors.validation_error("for_vehicle_id", code="VEHICLE_NOT_USABLE")
    elif data.for_vehicle_id is not None:
        raise errors.validation_error("for_vehicle_id", code="ORDER_NOT_INTERNAL")
    at_cost = target is not None and target.relation == target.Relation.OWN
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
            for_vehicle=target,
            at_cost=at_cost,
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
                # The purchase cost of a unit is known now; an unknown one counts as zero and
                # the costs of the vehicle are marked incomplete (§ 11.2.9).
                price = (
                    (asset.purchase_cost or ZERO)
                    if at_cost
                    else _price(prices.price_for(item, asset=asset))
                )
                created.append(
                    line(Decimal(1), item=item, asset=asset, quantity=Decimal(1), unit_price=price)
                )
            if len(created) != len(set(data.asset_ids)):
                raise errors.validation_error("asset_ids", code="ASSET_UNKNOWN")
        else:
            created.append(_add_lot(company.id, item, context.id, data, line, at_cost))
    ctx.audit(
        "warehouse.sale_added",
        object_type="order",
        object_id=order.id,
        after={"lines": [str(c.id) for c in created], **data.as_dict()},
    )
    from c4rsys.orders import approvals

    for c in created:
        if order.is_internal:
            break
        name = c.item.name if c.item_id and c.item else (c.vehicle.name if c.vehicle else "")
        approvals.on_added(ctx, order.id, "sale", c.id, name)
    return created


def _add_lot(
    company_id: UUID, item: Item, context_id: UUID, data: SaleData, line: Any, at_cost: bool
) -> SaleLine:
    """`at_cost`: the cost of a material is known when it is used — the earlier receipts first —
    so the line holds zero until the order is closed."""

    def priced(**where: Any) -> Decimal:
        return ZERO if at_cost else _price(prices.price_for(item, **where))

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
        price = priced(lot=lot)
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
    price = priced(lot=lot, packaging=packaging)
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
    order = _order(company_id, line.order_id)
    if line.state != SaleLine.State.RESERVED:
        raise errors.business_rule("SALE_LINE_STATE", state=line.state)
    line.state = SaleLine.State.REMOVED
    line.removed_at = timezone.now()
    line.save(update_fields=["state", "removed_at"])
    _charge(ctx, order, line, ZERO, "sale_removed")
    from c4rsys.orders import approvals

    if not order.is_internal:
        approvals.withdraw(ctx, company_id, "sale", line.id, "removed")
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
    by_context: dict[tuple[UUID, UUID | None], StockOperation] = {}
    now = timezone.now()
    number = Order.objects.get(pk=order_id).number
    for line in lines:
        if line.vehicle_id is not None:
            from c4rsys.fleet import lifecycle

            lifecycle.record_sale(
                ctx, company_id, line.vehicle_id, order_id, _buyer_profile(company_id, order_id)
            )
        else:
            # Goods of an internal order are used on their vehicle: a consumption, which counts
            # in the costs of the vehicle (customer's decision of 22.11.2026).
            used = line.for_vehicle_id is not None
            operation = by_context.get((line.context_id, line.for_vehicle_id))
            if operation is None:
                operation = StockOperation.objects.create(
                    company=company,
                    number=next_number(company_id, "warehouse_operation"),
                    kind=StockOperation.Kind.CONSUME if used else StockOperation.Kind.SALE,
                    context=line.context,
                    vehicle_id=line.for_vehicle_id,
                    comment=f"order {number}",
                    created_by_user_id=ctx.actor.user_id,
                )
                by_context[(line.context_id, line.for_vehicle_id)] = operation
            line.operation_line = _sell(ctx, operation, line)
            if line.at_cost:
                # The purchase cost of what was actually taken — the earlier receipts first.
                from c4rsys.warehouse import costs

                found = costs.line_cost(costs._lines(company_id).get(pk=line.operation_line.id))
                minor = company_currency(company).minor_units
                line.amount = round_money(found.cost, minor)
                line.unit_price = line.amount / line.quantity
        line.state = SaleLine.State.SOLD
        line.sold_at = now
        line.save(update_fields=["state", "sold_at", "operation_line", "amount", "unit_price"])
    ctx.audit(
        "warehouse.sold",
        object_type="order",
        object_id=order_id,
        after={
            "lines": [str(line.id) for line in lines],
            "operations": [o.number for o in by_context.values()],
        },
    )


def _buyer_profile(company_id: UUID, order_id: UUID) -> UUID | None:
    """A buyer — a person — linked with a confirmed personal profile gets the vehicle in the
    personal garage (§ 7.1.2, § 8.3.9)."""
    from c4rsys.parties import services as clients

    order = Order.objects.get(pk=order_id)
    if order.client_id is None:
        return None
    client = clients.get_client(company_id, order.client_id)
    return client.linked_user_id if client.kind == "person" else None


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
    used = operation.kind == StockOperation.Kind.CONSUME
    movement = StockMovement.Kind.CONSUME if used else StockMovement.Kind.SALE
    if line.asset_id is not None:
        asset = Asset.objects.select_for_update().get(pk=line.asset_id)
        if asset.state != Asset.State.IN_STOCK:
            raise errors.business_rule("SALE_STOCK_MISSING", asset=asset.public_id)
        asset.state = Asset.State.CONSUMED if used else Asset.State.SOLD
        asset.vehicle_id = operation.vehicle_id
        asset.save(update_fields=["state", "vehicle"])
        operations._move(ctx, movement, operation.context, op_line, Decimal(-1), asset=asset)
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
        ctx, movement, operation.context, op_line, -line.quantity, lot=lot, piece=piece
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
    order = _order(company_id, line.order_id)
    if line.state != SaleLine.State.SOLD:
        raise errors.business_rule("SALE_LINE_STATE", state=line.state)
    if installed(line):
        # Customer's decision of 22.11.2026: what a done work used is on the vehicle; it comes
        # back only by a removal and a return with a valuation, not by a cancellation.
        raise errors.business_rule("SALE_LINE_INSTALLED")
    if line.vehicle_id is not None:
        return _cancel_vehicle(ctx, order, line, reason)
    op_line = line.operation_line
    if op_line is None:
        raise errors.business_rule("SALE_LINE_STATE", state=line.state)
    context = op_line.operation.context
    if line.asset_id is not None:
        asset = Asset.objects.select_for_update().get(pk=line.asset_id)
        asset.state = Asset.State.IN_STOCK
        asset.vehicle_id = None
        asset.save(update_fields=["state", "vehicle"])
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
    if line.for_vehicle_id is not None:
        # Taken back: it is no cost of the vehicle any more.
        op_line.cancelled_quantity = op_line.quantity
        op_line.save(update_fields=["cancelled_quantity"])
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


def take_off(ctx: CommandContext, company_id: UUID, line_id: UUID) -> SaleLine:
    """Customer's decision of 22.11.2026: goods a done work used are taken off the vehicle by
    the service before they are returned."""
    line = _lock_line(company_id, line_id)
    order = _order(company_id, line.order_id)
    if line.state != SaleLine.State.SOLD or not installed(line) or line.taken_off_at is not None:
        raise errors.business_rule("SALE_LINE_STATE", state=line.state)
    line.taken_off_at = timezone.now()
    line.save(update_fields=["taken_off_at"])
    ctx.audit(
        "warehouse.sale_taken_off",
        object_type="order",
        object_id=order.id,
        after={"line": str(line.id), "job": str(line.job_id)},
    )
    return line


def return_goods(
    ctx: CommandContext, company: Company, line_id: UUID, valuation: Decimal, comment: str
) -> SaleLine:
    """Customer's decisions of 22.11.2026: sold goods are taken back with a valuation — the same
    for installed and not installed ones; the installed are taken off the vehicle first. The
    sale stays posted. The valuation is what the client gets back — the charge of the line goes
    down by it, a payment made stays and becomes an overpayment — and the cost at which the
    goods come into the stock of their warehouse."""
    line = _lock_line(company.id, line_id)
    order = _order(company.id, line.order_id)
    if line.state != SaleLine.State.SOLD or line.vehicle_id is not None or order.is_internal:
        raise errors.business_rule("SALE_LINE_STATE", state=line.state)
    if installed(line) and line.taken_off_at is None:
        raise errors.business_rule("SALE_LINE_INSTALLED")
    if valuation < 0 or valuation > line.amount:
        raise errors.validation_error("amount", code="RETURN_AMOUNT_RANGE")
    op_line = line.operation_line
    if op_line is None:
        raise errors.business_rule("SALE_LINE_STATE", state=line.state)
    context = op_line.operation.context
    now = timezone.now()
    if line.asset_id is not None:
        asset = Asset.objects.select_for_update().get(pk=line.asset_id)
        asset.state = Asset.State.IN_STOCK
        asset.vehicle_id = None
        asset.purchase_cost = valuation
        asset.save(update_fields=["state", "vehicle", "purchase_cost"])
        operations._move(
            ctx, StockMovement.Kind.CUSTOMER_RETURN, context, op_line, Decimal(1), asset=asset
        )
    else:
        lot = operations._lot_of(op_line)
        # A new layer of the lot at the valuation: what was sold stays sold at its own cost.
        LotLayer.objects.create(
            company_id=line.company_id,
            lot=lot,
            received_quantity=line.quantity,
            remaining=line.quantity,
            unit_cost=valuation / line.quantity,
            received_at=now,
        )
        piece = operations._put_back(lot, line.packaging, line.opened_package, line.quantity)
        operations._move(
            ctx,
            StockMovement.Kind.CUSTOMER_RETURN,
            context,
            op_line,
            line.quantity,
            lot=lot,
            piece=piece,
        )
    line.state = SaleLine.State.RETURNED
    line.returned_at = now
    line.return_amount = valuation
    line.return_comment = comment
    line.save(update_fields=["state", "returned_at", "return_amount", "return_comment"])
    _charge(ctx, order, line, line.amount - valuation, "sale_returned")
    ctx.audit(
        "warehouse.sale_returned",
        object_type="order",
        object_id=order.id,
        after={"line": str(line.id), "valuation": str(valuation)},
        reason=comment,
    )
    return line


def _cancel_vehicle(ctx: CommandContext, order: Any, line: SaleLine, reason: str) -> SaleLine:
    """§ 11.9.5 for a vehicle: allowed only while the ownership did not pass to the buyer or after
    the new owner confirmed the error (§ 8.3.9); the vehicle returns to the seller's warehouse."""
    from c4rsys.fleet import lifecycle

    if line.vehicle_id is None:
        raise errors.business_rule("SALE_LINE_STATE", state=line.state)
    lifecycle.cancel_sale(ctx, line.company_id, line.vehicle_id, line.order_id, reason)
    line.state = SaleLine.State.CANCELLED
    line.cancelled_at = timezone.now()
    line.cancel_reason = reason
    line.save(update_fields=["state", "cancelled_at", "cancel_reason"])
    _charge(ctx, order, line, ZERO, "sale_cancelled")
    ctx.audit(
        "warehouse.sale_cancelled",
        object_type="order",
        object_id=order.id,
        after={"line": str(line.id), "vehicle": str(line.vehicle_id)},
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
    return operations_for_orders(company_id, [order_id])[order_id]


def operations_for_orders(
    company_id: UUID, order_ids: list[UUID]
) -> dict[UUID, list[orders.OperationSummary]]:
    """The same for many orders in one reading (audit item 14)."""
    result: dict[UUID, list[orders.OperationSummary]] = {order_id: [] for order_id in order_ids}
    for line in SaleLine.objects.select_related("context").filter(
        company_id=company_id,
        order_id__in=order_ids,
        state__in=[SaleLine.State.RESERVED, SaleLine.State.SOLD],
    ):
        result[line.order_id].append(
            orders.OperationSummary(
                type="sale",
                id=line.id,
                state=line.state,
                location_id=line.context.location_id,
                finished=True,
            )
        )
    return result


def history_for_order(company_id: UUID, order_id: UUID) -> list[tuple[str, str]]:
    return [("order", str(order_id))]


def link_to_job(
    ctx: CommandContext, company_id: UUID, line_id: UUID, order_id: UUID, job_id: UUID | None
) -> SaleLine:
    """Customer's decision of 22.11.2026: goods of an order are linked to a work of a service of
    the same order, or unlinked (`job_id` empty). The line, its reserve and its price stay as
    they are — it is the same line, not a return."""
    line = _lock_line(company_id, line_id)
    if line.order_id != order_id:
        raise errors.business_rule("SALE_LINE_OTHER_ORDER", field="line_id")
    order = _order(company_id, line.order_id)
    if line.state != SaleLine.State.RESERVED or line.item_id is None:
        raise errors.business_rule("SALE_LINE_STATE", state=line.state)
    before = line.job_id
    line.job_id = job_id
    line.save(update_fields=["job_id"])
    ctx.audit(
        "warehouse.sale_linked" if job_id else "warehouse.sale_unlinked",
        object_type="order",
        object_id=order.id,
        before={"line": str(line.id), "job": str(before) if before else None},
        after={"line": str(line.id), "job": str(job_id) if job_id else None},
    )
    return line


def get_line(company_id: UUID, line_id: UUID) -> SaleLine:
    line = SaleLine.objects.filter(company_id=company_id, pk=line_id).first()
    if line is None:
        raise errors.not_found()
    return line


def goods_of_order(company_id: UUID, order_id: UUID) -> list[dict[str, Any]]:
    """The goods of an order as a service of it shows them: what, how much, for how much and
    the work they are linked to."""
    from c4rsys.warehouse.stock import money_text

    return [
        {
            "id": str(line.id),
            "name": line.item.name if line.item else "",
            "unit": line.item.unit if line.item else "",
            "what": line.asset.public_id
            if line.asset
            else (line.lot.public_id if line.lot else ""),
            "quantity": str(line.quantity),
            "amount": money_text(line.amount, line.currency),
            "currency": line.currency,
            "state": line.state,
            "job_id": str(line.job_id) if line.job_id else None,
            "installed": installed(line),
            "taken_off_at": line.taken_off_at.isoformat() if line.taken_off_at else None,
        }
        for line in lines_of_order(company_id, order_id)
        if line.item_id is not None and line.state in (SaleLine.State.RESERVED, SaleLine.State.SOLD)
    ]


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


def goods_categories(line_ids: list[UUID]) -> dict[UUID, str]:
    """The category of the catalog of parts of each sold item, for discounts on groups of goods
    (§ 12.5.2; decision В-2)."""
    return {
        line.id: line.item.part_category.code
        for line in SaleLine.objects.select_related("item__part_category").filter(pk__in=line_ids)
        if line.item is not None and line.item.part_category is not None
    }
