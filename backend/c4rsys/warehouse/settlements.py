"""What the company owes suppliers for goods, for the finance module (ТЗ § 11.2.4, § 11.4.2).

Read-only: the value of posted purchase receipts and of returns to suppliers, at the purchase
cost of each line. A line without a known cost makes the value incomplete (§ 11.2.6); it is not
counted as zero silently. Payments and their links live in the finance module.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money
from c4rsys.warehouse.models import (
    OperationLine,
    PurchaseOrder,
    Receipt,
    StockOperation,
)
from c4rsys.warehouse.operations import effective_quantity


@dataclass(frozen=True)
class Value:
    id: UUID
    number: int
    supplier_id: UUID | None
    purchase_id: UUID | None
    receipt_id: UUID | None  # for a return — the receipt the goods came by
    currency: str
    amount: Decimal
    incomplete: bool  # some line has no known cost
    at: datetime


def _places(currency: str) -> int:
    minor = Currency.objects.filter(code=currency).values_list("minor_units", flat=True).first()
    return 2 if minor is None else minor


def receipts(
    company_id: UUID,
    *,
    supplier_id: UUID | None = None,
    receipt_ids: Iterable[UUID] | None = None,
    purchase_id: UUID | None = None,
) -> list[Value]:
    """Posted purchase receipts; each line rounded to the minor units (§ 17.1.7)."""
    query = Receipt.objects.filter(
        company_id=company_id, state=Receipt.State.POSTED, source=Receipt.Source.PURCHASE
    )
    if supplier_id is not None:
        query = query.filter(supplier_id=supplier_id)
    if receipt_ids is not None:
        query = query.filter(pk__in=list(receipt_ids))
    if purchase_id is not None:
        query = query.filter(purchase_id=purchase_id)
    result = []
    for receipt in query.prefetch_related("lines").order_by("number"):
        places = _places(receipt.currency)
        amount = Decimal(0)
        incomplete = False
        for line in receipt.lines.all():
            if line.unit_cost is None:
                incomplete = True
                continue
            amount += round_money(effective_quantity(line) * line.unit_cost, places)
        result.append(
            Value(
                id=receipt.id,
                number=receipt.number,
                supplier_id=receipt.supplier_id,
                purchase_id=receipt.purchase_id,
                receipt_id=receipt.id,
                currency=receipt.currency,
                amount=amount,
                incomplete=incomplete,
                at=receipt.posted_at or receipt.received_at,
            )
        )
    return result


def returns(
    company_id: UUID,
    *,
    supplier_id: UUID | None = None,
    operation_ids: Iterable[UUID] | None = None,
) -> list[Value]:
    """Returns to suppliers (§ 11.4.2) valued at the cost of the receipt lines they return."""
    query = StockOperation.objects.filter(
        company_id=company_id, kind=StockOperation.Kind.SUPPLIER_RETURN, receipt__isnull=False
    ).select_related("receipt")
    if supplier_id is not None:
        query = query.filter(receipt__supplier_id=supplier_id)
    if operation_ids is not None:
        query = query.filter(pk__in=list(operation_ids))
    result = []
    for operation in query.order_by("number"):
        receipt = operation.receipt
        if receipt is None:  # excluded by the query
            continue
        places = _places(receipt.currency)
        amount = Decimal(0)
        incomplete = False
        lines = OperationLine.objects.filter(operation=operation).select_related("receipt_line")
        for line in lines:
            cost = line.receipt_line.unit_cost if line.receipt_line else None
            if cost is None:
                incomplete = True
                continue
            amount += round_money(line.quantity * cost, places)
        result.append(
            Value(
                id=operation.id,
                number=operation.number,
                supplier_id=receipt.supplier_id,
                purchase_id=receipt.purchase_id,
                receipt_id=receipt.id,
                currency=receipt.currency,
                amount=amount,
                incomplete=incomplete,
                at=operation.created_at,
            )
        )
    return result


def purchase(company_id: UUID, purchase_id: UUID) -> PurchaseOrder | None:
    return PurchaseOrder.objects.filter(company_id=company_id, pk=purchase_id).first()


def receipt(company_id: UUID, receipt_id: UUID) -> Receipt | None:
    return Receipt.objects.filter(company_id=company_id, pk=receipt_id).first()


def return_operation(company_id: UUID, operation_id: UUID) -> StockOperation | None:
    return (
        StockOperation.objects.filter(
            company_id=company_id, pk=operation_id, kind=StockOperation.Kind.SUPPLIER_RETURN
        )
        .select_related("receipt")
        .first()
    )
