"""Materials issued to a service work (ТЗ § 11.4, § 16.5.1–16.5.2; customer's decision of
25.10.2026: the service has no storage of its own and takes from a warehouse).

The service module calls these functions; it does not read the warehouse tables. An issue to a
work is an ordinary issue with a return possible, on the vehicle of the service operation — own,
managed or a client's. Assigning a part is not its use: it may be returned unused into the same
lot (§ 11.4.5) until it is consumed. The selling price is the one a sale would apply (§ 11.7.5);
unknown — the employee with the right sets it in the order.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from c4rsys.companies.models import Company
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse import operations, prices
from c4rsys.warehouse.models import Asset, OperationLine, StockMovement, StockOperation


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


def return_line(ctx: CommandContext, company_id: UUID, line_id: UUID, quantity: Decimal) -> None:
    """Unused material comes back into the same lot and unit (§ 11.4.5)."""
    operations.return_issued(
        ctx,
        company_id,
        _operation_of(company_id, line_id),
        (operations.LineAmount(line_id, quantity),),
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
