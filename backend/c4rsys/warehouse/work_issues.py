"""Materials issued to a service work (ТЗ § 11.4, § 16.5.1–16.5.2; customer's decision of
25.10.2026: the service has no storage of its own and takes from a warehouse).

The service module calls these functions; it does not read the warehouse tables. An issue to a
work is an ordinary issue with a return possible, on the vehicle of the service operation — own,
managed or a client's. Assigning a part is not its use: it may be returned unused into the same
lot (§ 11.4.5) until it is consumed. The selling price is the one a sale would apply (§ 11.7.5);
unknown — the employee with the right sets it in the order.
"""

from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, Decimal
from typing import Any
from uuid import UUID

from c4rsys.companies.models import Company
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse import costs, operations, prices, reserves
from c4rsys.warehouse.models import (
    Asset,
    Item,
    Lot,
    LotStock,
    OpenedPackage,
    OperationLine,
    Packaging,
    StockMovement,
    StockOperation,
    StockReserve,
)


@dataclass(frozen=True)
class IssuedLine:
    """A line issued to a work as the service needs to see it. The price is for `price_per`
    base units: one, or the quantity of a package for closed packages."""

    line_id: UUID
    operation_id: UUID
    operation_number: int
    location_id: UUID
    item_id: UUID
    item_name: str
    unit: str
    asset_public_id: str | None
    lot_public_id: str | None
    quantity: Decimal
    price: Decimal | None
    price_per: Decimal


def _issued(line: OperationLine) -> IssuedLine:
    operation = line.operation
    packaging = line.packaging if line.opened_package_id is None else None
    price = prices.price_for(line.item, lot=line.lot, packaging=packaging, asset=line.asset)
    return IssuedLine(
        line_id=line.id,
        operation_id=operation.id,
        operation_number=operation.number,
        location_id=operation.context.location_id,
        item_id=line.item_id,
        item_name=line.item.name,
        unit=line.item.unit,
        asset_public_id=line.asset.public_id if line.asset else None,
        lot_public_id=line.lot.public_id if line.lot else None,
        quantity=line.quantity,
        price=price,
        price_per=packaging.quantity if packaging is not None else Decimal(1),
    )


def warehouse_location(company_id: UUID, context_id: UUID) -> UUID:
    """The location of a usable warehouse, for the scope of the issue right."""
    return operations._warehouse(company_id, context_id).location_id


def issue_to_work(
    ctx: CommandContext,
    company: Company,
    context_id: UUID,
    vehicle_id: UUID,
    take: operations.Take,
    comment: str,
) -> list[IssuedLine]:
    """Issues from the warehouse to the vehicle of a service work; a partial use of a closed
    package opens one package (§ 11.1.17)."""
    context = operations._warehouse(company.id, context_id)
    taken = operations._take_all(
        ctx, company.id, context, (take,), allow_open=True, check_expiry=True
    )
    operation = operations._new_operation(
        ctx, company, StockOperation.Kind.ISSUE, context, vehicle_id=vehicle_id, comment=comment
    )
    operations._write_out(ctx, operation, taken, StockMovement.Kind.ISSUE, Asset.State.ISSUED)
    ctx.audit(
        "warehouse.issued",
        object_type="warehouse_operation",
        object_id=operation.id,
        after=operations.snapshot(operation),
    )
    lines = OperationLine.objects.select_related(
        "operation__context", "item", "lot", "asset", "packaging"
    ).filter(operation=operation)
    return [_issued(line) for line in lines.order_by("position")]


@dataclass(frozen=True)
class LineState:
    quantity: Decimal
    returned: Decimal
    consumed: Decimal

    @property
    def outstanding(self) -> Decimal:
        return self.quantity - self.returned - self.consumed


def states(line_ids: list[UUID]) -> dict[UUID, LineState]:
    return {
        row["id"]: LineState(row["quantity"], row["returned_quantity"], row["consumed_quantity"])
        for row in OperationLine.objects.filter(pk__in=line_ids).values(
            "id", "quantity", "returned_quantity", "consumed_quantity"
        )
    }


def _operation_of(company_id: UUID, line_id: UUID) -> UUID:
    found = (
        OperationLine.objects.filter(company_id=company_id, pk=line_id)
        .values_list("operation_id", flat=True)
        .first()
    )
    if found is None:
        raise errors.not_found()
    return found


def return_line(
    ctx: CommandContext,
    company_id: UUID,
    line_id: UUID,
    quantity: Decimal,
    target_id: UUID | None = None,
) -> None:
    """Unused material comes back into the same lot and unit, or — through a move — into
    another warehouse (§ 11.4.5)."""
    operations.return_issued(
        ctx,
        company_id,
        _operation_of(company_id, line_id),
        (operations.LineAmount(line_id, quantity),),
        target_id,
    )


def parse_take(data: dict[str, Any]) -> operations.Take:
    """What to take, in the format of a line of an issue."""
    from c4rsys.warehouse.operations_views import _takes

    return _takes({"lines": [data]})[0]


def line_location(company_id: UUID, line_id: UUID) -> UUID:
    """The location of the warehouse a line was issued from."""
    found = (
        OperationLine.objects.filter(company_id=company_id, pk=line_id)
        .values_list("operation__context__location_id", flat=True)
        .first()
    )
    if found is None:
        raise errors.not_found()
    return found


def consume_line(ctx: CommandContext, company_id: UUID, line_id: UUID, quantity: Decimal) -> None:
    """Of the issued, this was actually used (§ 11.4.1, § 16.5.1)."""
    operations.consume_issued(
        ctx,
        company_id,
        _operation_of(company_id, line_id),
        (operations.LineAmount(line_id, quantity),),
    )


# --- Planned materials and their reserves (§ 11.7.7, § 11.8.3) -----------------------------------


@dataclass(frozen=True)
class Planned:
    """A material added to a planned work before its issue: what to take, from which warehouse,
    its price for the client and its reserve if any."""

    take: operations.Take
    context_id: UUID
    item_name: str
    unit: str
    label: str
    quantity: Decimal
    price: Decimal | None
    price_per: Decimal
    reserve_id: UUID | None

    def stored(self) -> dict[str, Any]:
        return {"context_id": str(self.context_id), **self.take.as_dict()}


def work_reserve_mode(company_id: UUID) -> str:
    return operations.settings_of(company_id).work_reserve_mode


def take_of(planned: dict[str, Any]) -> tuple[UUID, operations.Take]:
    """The warehouse and what to take of a stored planned material."""
    context_id = UUID(planned["context_id"])
    take = parse_take({k: v for k, v in planned.items() if k != "context_id"})
    return context_id, take


def _free(company_id: UUID, context_id: UUID, take: operations.Take) -> None:
    """§ 11.8.3: a material is added to a work only within the actually available quantity."""
    item = Item.objects.filter(company_id=company_id, pk=take.item_id).first()
    if item is None:
        raise errors.validation_error("item_id", code="ITEM_UNKNOWN")
    if item.accounting == Item.Accounting.INDIVIDUAL:
        assets = list(Asset.objects.filter(company_id=company_id, pk__in=take.asset_ids, item=item))
        if not take.asset_ids or len(assets) != len(set(take.asset_ids)):
            raise errors.validation_error("asset_ids", code="ASSET_UNKNOWN")
        for asset in assets:
            if (
                asset.context_id != context_id
                or asset.state != Asset.State.IN_STOCK
                or reserves.asset_reserved(asset.id)
            ):
                raise errors.business_rule("ASSET_NOT_AVAILABLE", asset=asset.public_id)
        return
    if take.lot_id is None or take.quantity is None:
        raise errors.validation_error("lot_id", code="FIELD_REQUIRED")
    lot = Lot.objects.filter(company_id=company_id, pk=take.lot_id, context_id=context_id).first()
    if lot is None:
        raise errors.validation_error("lot_id", code="LOT_UNKNOWN")
    packaging_id = take.packaging_id
    needed = take.quantity
    if take.opened_package_id is not None:
        opened = OpenedPackage.objects.filter(pk=take.opened_package_id, lot=lot).first()
        if opened is None:
            raise errors.validation_error("opened_package_id", code="OPENED_PACKAGE_UNKNOWN")
        physical = opened.remaining
        packaging_id = opened.packaging_id
    else:
        rows = LotStock.objects.filter(lot=lot)
        row = (
            rows.filter(packaging__isnull=True)
            if take.packaging_id is None
            else rows.filter(packaging_id=take.packaging_id)
        ).first()
        physical = row.quantity if row else Decimal(0)
        if take.packaging_id is not None:
            packaging = Packaging.objects.filter(pk=take.packaging_id, item=item).first()
            if packaging is None:
                raise errors.validation_error("packaging_id", code="PACKAGING_UNKNOWN")
            size = packaging.quantity
            needed = size * (take.quantity / size).to_integral_value(rounding=ROUND_CEILING)
    free = physical - reserves.reserved_in_form(lot.id, packaging_id, take.opened_package_id)
    if needed > free:
        raise errors.business_rule(
            "STOCK_NOT_ENOUGH", field="quantity", available=str(max(free, Decimal(0)))
        )


def plan_for_work(
    ctx: CommandContext,
    company: Company,
    context_id: UUID,
    job_id: UUID,
    take: operations.Take,
    *,
    reserve: bool,
) -> list[Planned]:
    """Adds a material to a planned work without issuing it: one entry per unit of an individual
    item. Reserved at once by the automatic mode, or left for a manual reserve (§ 11.7.7)."""
    context = operations._warehouse(company.id, context_id)
    _free(company.id, context.id, take)
    item = Item.objects.get(company_id=company.id, pk=take.item_id)
    pieces: list[operations.Take] = (
        [operations.Take(item_id=item.id, asset_ids=(a,)) for a in take.asset_ids]
        if item.accounting == Item.Accounting.INDIVIDUAL
        else [take]
    )
    result = []
    for piece in pieces:
        reserve_id = None
        if reserve:
            reserve_id = reserve_for_work(ctx, company, context.id, job_id, piece).id
        result.append(_planned(company.id, context.id, piece, item, reserve_id))
    return result


def _planned(
    company_id: UUID, context_id: UUID, take: operations.Take, item: Item, reserve_id: UUID | None
) -> Planned:
    asset = (
        Asset.objects.filter(company_id=company_id, pk=take.asset_ids[0]).first()
        if take.asset_ids
        else None
    )
    lot = Lot.objects.filter(pk=take.lot_id).first() if take.lot_id else None
    packaging = None
    if take.packaging_id and take.opened_package_id is None:
        packaging = Packaging.objects.filter(pk=take.packaging_id).first()
    price = prices.price_for(item, lot=lot, packaging=packaging, asset=asset)
    return Planned(
        take=take,
        context_id=context_id,
        item_name=item.name,
        unit=item.unit,
        label=asset.public_id if asset else (lot.public_id if lot else ""),
        quantity=Decimal(1) if asset else (take.quantity or Decimal(0)),
        price=price,
        price_per=packaging.quantity if packaging is not None else Decimal(1),
        reserve_id=reserve_id,
    )


def reserve_for_work(
    ctx: CommandContext, company: Company, context_id: UUID, job_id: UUID, take: operations.Take
) -> StockReserve:
    return reserves.create(
        ctx,
        company,
        context_id,
        item_id=take.item_id,
        asset_id=take.asset_ids[0] if take.asset_ids else None,
        lot_id=take.lot_id,
        packaging_id=take.packaging_id if take.opened_package_id is None else None,
        opened_package_id=take.opened_package_id,
        quantity=take.quantity,
        purpose=StockReserve.Purpose.WORK,
        source_id=job_id,
    )


def release(ctx: CommandContext, company_id: UUID, reserve_id: UUID, reason: str) -> None:
    reserve = reserves.lock(company_id, reserve_id)
    if reserve.state == StockReserve.State.ACTIVE:
        reserves.release(ctx, company_id, reserve_id, reason)


def reserve_state(reserve_id: UUID) -> str | None:
    found: str | None = (
        StockReserve.objects.filter(pk=reserve_id).values_list("state", flat=True).first()
    )
    return found


def issue_planned(
    ctx: CommandContext,
    company: Company,
    planned: dict[str, Any],
    reserve_id: UUID | None,
    vehicle_id: UUID,
    comment: str,
) -> IssuedLine:
    """Issues a planned material; its active reserve is used, otherwise the free stock counts on
    fresh data (§ 11.8.1)."""
    context_id, take = take_of(planned)
    if reserve_id is not None and reserve_state(reserve_id) == StockReserve.State.ACTIVE:
        take = replace(take, reserve_id=reserve_id)
    issued = issue_to_work(ctx, company, context_id, vehicle_id, take, comment)
    return issued[0]


def line_costs(company_id: UUID, line_ids: list[UUID]) -> dict[UUID, costs.LineCost]:
    """The purchase cost of what the work consumed (§ 11.2.9, § 11.4.4)."""
    return costs.of_lines(company_id, line_ids)
