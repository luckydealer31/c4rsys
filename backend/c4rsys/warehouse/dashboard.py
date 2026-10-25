"""The starter dashboard «Warehouse» (ТЗ § 21.6.1, § 21.1.1–21.1.2).

Only warehouses the employee may see are counted; with partial access the scope of the
calculation is stated. The period is inclusive, by dates in the time zone of the company.
Quantities of different items and units are never added up: they are shown per item; money is
summed, and a sum with unknown costs or prices is marked incomplete.

PROPOSAL: «expiring soon» means an expiry within 30 days.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import Q, QuerySet, Sum
from django.utils import timezone

from c4rsys.companies.models import ActivityContext, Company
from c4rsys.fleet import services as fleet
from c4rsys.warehouse import operations, prices, stock
from c4rsys.warehouse.models import (
    Asset,
    Item,
    Lot,
    LotLayer,
    LotStock,
    OpenedPackage,
    OperationLine,
    PurchaseOrder,
    Receipt,
    SaleLine,
    StockOperation,
)

EXPIRY_HORIZON_DAYS = 30
ZERO = Decimal("0.00")
PERIOD_KINDS = (
    StockOperation.Kind.CONSUME,
    StockOperation.Kind.ISSUE,
    StockOperation.Kind.MOVE,
    StockOperation.Kind.WRITE_OFF,
    StockOperation.Kind.SUPPLIER_RETURN,
    StockOperation.Kind.SALE,
)


def company_today(company: Company) -> date:
    """Today in the time zone of the company, not of the server (§ 2.7.5)."""
    return timezone.localdate(timezone=ZoneInfo(company.timezone or "UTC"))


@dataclass(frozen=True)
class Filters:
    date_from: date
    date_to: date
    context_id: UUID | None = None
    part_category_id: UUID | None = None
    item_id: UUID | None = None
    lot_id: UUID | None = None
    counterparty_id: UUID | None = None


@dataclass
class Money:
    amount: Decimal = ZERO
    incomplete: bool = False

    def add(self, value: Decimal | None, quantity: Decimal = Decimal(1)) -> None:
        if value is None:
            self.incomplete = True
        else:
            self.amount += value * quantity

    def payload(self, currency: str) -> dict[str, Any]:
        return {"amount": stock.money_text(self.amount, currency), "incomplete": self.incomplete}


@dataclass(frozen=True)
class Scope:
    company: Company
    context_ids: set[UUID]
    items: dict[UUID, Item]
    lot_ids: set[UUID]
    filters: Filters

    def brief(self, item_id: UUID) -> dict[str, str]:
        item = self.items[item_id]
        return {"id": str(item.id), "name": item.name, "unit": item.unit}


def _items(company_id: UUID, filters: Filters) -> QuerySet[Item]:
    found = Item.objects.filter(company_id=company_id)
    if filters.part_category_id:
        found = found.filter(part_category_id=filters.part_category_id)
    if filters.item_id:
        found = found.filter(pk=filters.item_id)
    return found


def _assets(scope: Scope) -> QuerySet[Asset]:
    if scope.filters.lot_id:
        return Asset.objects.none()
    return Asset.objects.filter(
        company_id=scope.company.id,
        context_id__in=scope.context_ids,
        item_id__in=scope.items,
        state=Asset.State.IN_STOCK,
    )


def _stock(scope: Scope) -> list[dict[str, Any]]:
    """Actual, reserved and available stock by item (§ 21.6.1)."""
    actual: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    reserved: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    for asset in _assets(scope):
        actual[asset.item_id] += 1
    lot_rows = LotStock.objects.filter(lot_id__in=scope.lot_ids)
    for total in lot_rows.values("lot__item_id").annotate(total=Sum("quantity")):
        actual[total["lot__item_id"]] += total["total"]
    opened_rows = OpenedPackage.objects.filter(lot_id__in=scope.lot_ids)
    for total in opened_rows.values("lot__item_id").annotate(total=Sum("remaining")):
        actual[total["lot__item_id"]] += total["total"]
    sale_lines = SaleLine.objects.filter(
        company_id=scope.company.id,
        state=SaleLine.State.RESERVED,
        context_id__in=scope.context_ids,
        item_id__in=scope.items,
    )
    if scope.filters.lot_id:
        sale_lines = sale_lines.filter(lot_id=scope.filters.lot_id)
    for sold in sale_lines.values("item_id").annotate(total=Sum("quantity")):
        reserved[sold["item_id"]] += sold["total"]
    return [
        {
            "item": scope.brief(item_id),
            "actual": str(actual[item_id]),
            "reserved": str(reserved[item_id]),
            "available": str(actual[item_id] - reserved[item_id]),
        }
        for item_id in sorted(set(actual) | set(reserved), key=lambda i: scope.items[i].name)
        if actual[item_id] or reserved[item_id]
    ]


def _value(scope: Scope) -> dict[str, Any]:
    """The stock at actual purchase costs and at current selling prices (§ 21.6.1)."""
    currency = scope.company.main_currency_id or ""
    cost = Money()
    sale = Money()
    for asset in _assets(scope):
        cost.add(asset.purchase_cost)
        sale.add(prices.price_for(scope.items[asset.item_id], asset=asset))
    for layer in LotLayer.objects.filter(lot_id__in=scope.lot_ids, remaining__gt=0):
        cost.add(layer.unit_cost, layer.remaining)
    forms = LotStock.objects.filter(lot_id__in=scope.lot_ids, quantity__gt=0)
    for form in forms.select_related("lot", "packaging"):
        item = scope.items[form.lot.item_id]
        if form.packaging is not None:
            packs = form.quantity / form.packaging.quantity
            sale.add(prices.price_for(item, lot=form.lot, packaging=form.packaging), packs)
        else:
            sale.add(prices.price_for(item, lot=form.lot), form.quantity)
    opened = OpenedPackage.objects.filter(lot_id__in=scope.lot_ids, remaining__gt=0)
    for package in opened.select_related("lot"):
        sale.add(
            prices.price_for(scope.items[package.lot.item_id], lot=package.lot), package.remaining
        )
    filtered = scope.filters.item_id or scope.filters.part_category_id or scope.filters.lot_id
    if not filtered:
        for card_id in fleet.stored_cards(scope.company.id, scope.context_ids):
            sale.add(prices.vehicle_price(scope.company.id, card_id))
    return {"cost": cost.payload(currency), "sale": sale.payload(currency)}


def _issued(scope: Scope) -> list[dict[str, Any]]:
    """Issued with a return possible and still out."""
    issue_lines = list(
        OperationLine.objects.filter(
            company_id=scope.company.id,
            operation__kind=StockOperation.Kind.ISSUE,
            operation__context_id__in=scope.context_ids,
            item_id__in=scope.items,
        ).select_related("operation", "asset", "lot")
    )
    vehicles = {il.operation.vehicle_id for il in issue_lines if il.operation.vehicle_id}
    names = fleet.card_names(scope.company.id, vehicles)
    result = []
    for il in issue_lines:
        left = operations.outstanding(il)
        if left <= 0:
            continue
        vehicle_id = il.operation.vehicle_id
        result.append(
            {
                "operation_id": str(il.operation_id),
                "number": il.operation.number,
                "item": scope.brief(il.item_id),
                "vehicle": names.get(vehicle_id, "") if vehicle_id else "",
                "what": il.asset.public_id if il.asset else (il.lot.public_id if il.lot else ""),
                "quantity": str(left),
            }
        )
    return result


def _in_transit(scope: Scope) -> list[dict[str, Any]]:
    moving = StockOperation.objects.filter(
        Q(context_id__in=scope.context_ids) | Q(target_context_id__in=scope.context_ids),
        company_id=scope.company.id,
        kind=StockOperation.Kind.MOVE,
        state__in=[StockOperation.State.IN_TRANSIT, StockOperation.State.DISCREPANCY],
    ).select_related("context__location", "target_context__location")
    return [
        {
            "operation_id": str(o.id),
            "number": o.number,
            "state": o.state,
            "from": o.context.location.name,
            "to": o.target_context.location.name if o.target_context else "",
        }
        for o in moving.order_by("number")
    ]


def _expiring(scope: Scope, today: date) -> list[dict[str, Any]]:
    horizon = today + timedelta(days=EXPIRY_HORIZON_DAYS)
    lots = (
        Lot.objects.filter(
            pk__in=scope.lot_ids, expiry_date__isnull=False, expiry_date__lte=horizon
        )
        .select_related("context__location")
        .prefetch_related("stock", "opened_packages")
        .order_by("expiry_date")
    )
    result = []
    for lot in lots:
        quantity = sum((r.quantity for r in lot.stock.all()), ZERO) + sum(
            (o.remaining for o in lot.opened_packages.all()), ZERO
        )
        if quantity > 0:
            result.append(
                {
                    "lot": lot.public_id,
                    "item": scope.brief(lot.item_id),
                    "location_name": lot.context.location.name,
                    "expiry_date": lot.expiry_date.isoformat() if lot.expiry_date else None,
                    "expired": stock.expired(lot, today),
                    "quantity": str(quantity),
                }
            )
    return result


def _period(scope: Scope) -> dict[str, Any]:
    """Receipts, consumption, issues, moves, write-offs, returns and sales of the period."""
    company = scope.company
    currency = company.main_currency_id or ""
    zone = ZoneInfo(company.timezone or "UTC")
    start = datetime.combine(scope.filters.date_from, time.min, tzinfo=zone)
    end = datetime.combine(scope.filters.date_to + timedelta(days=1), time.min, tzinfo=zone)
    result: dict[str, Any] = {}
    receipts = Receipt.objects.filter(
        company_id=company.id,
        state=Receipt.State.POSTED,
        context_id__in=scope.context_ids,
        posted_at__gte=start,
        posted_at__lt=end,
    )
    if scope.filters.counterparty_id:
        receipts = receipts.filter(supplier_id=scope.filters.counterparty_id)
    received = Money()
    receipt_count = 0
    for receipt in receipts.prefetch_related("lines"):
        chosen = [
            rl
            for rl in receipt.lines.all()
            if rl.item_id in scope.items
            and (not scope.filters.lot_id or rl.lot_id == scope.filters.lot_id)
        ]
        if not chosen:
            continue
        receipt_count += 1
        for rl in chosen:
            amount = rl.corrected_quantity if rl.corrected_quantity is not None else rl.quantity
            received.add(rl.unit_cost, amount)
    result["receipts"] = {"count": receipt_count, "cost": received.payload(currency)}
    for kind in PERIOD_KINDS:
        found = StockOperation.objects.filter(
            company_id=company.id,
            kind=kind,
            context_id__in=scope.context_ids,
            created_at__gte=start,
            created_at__lt=end,
        )
        op_lines = OperationLine.objects.filter(
            operation__in=found, item_id__in=scope.items, output=False
        )
        if scope.filters.lot_id:
            op_lines = op_lines.filter(lot_id=scope.filters.lot_id)
        money = Money()
        for ol in op_lines.select_related("asset").prefetch_related("layer_uses"):
            if ol.asset is not None:
                money.add(ol.asset.purchase_cost)
            for use in ol.layer_uses.all():
                money.add(use.unit_cost, use.quantity)
        count = op_lines.values("operation_id").distinct().count()
        result[kind] = {"count": count, "cost": money.payload(currency)}
    return result


def _purchases(scope: Scope) -> list[dict[str, Any]]:
    """By purchases: ordered, received and still expected."""
    purchases = (
        PurchaseOrder.objects.filter(company_id=scope.company.id)
        .prefetch_related("lines__item")
        .order_by("-number")
    )
    if scope.filters.counterparty_id:
        purchases = purchases.filter(supplier_id=scope.filters.counterparty_id)
    rows = []
    for purchase in purchases[:100]:
        chosen = [pl for pl in purchase.lines.all() if pl.item_id in scope.items]
        got = stock.received_by_line([pl.id for pl in chosen])
        for pl in chosen:
            rest = stock.expected(pl, got.get(pl.id, ZERO))
            if rest <= 0:
                continue
            rows.append(
                {
                    "purchase_id": str(purchase.id),
                    "number": purchase.number,
                    "item": scope.brief(pl.item_id),
                    "ordered": str(pl.ordered_quantity),
                    "received": str(got.get(pl.id, ZERO)),
                    "expected": str(rest),
                }
            )
    return rows


def build(
    company: Company, visible: dict[UUID, ActivityContext], all_count: int, filters: Filters
) -> dict[str, Any]:
    contexts = {k: v for k, v in visible.items() if filters.context_id in (None, k)}
    items = {i.id: i for i in _items(company.id, filters)}
    lots = Lot.objects.filter(company_id=company.id, context_id__in=contexts, item_id__in=items)
    if filters.lot_id:
        lots = lots.filter(pk=filters.lot_id)
    scope = Scope(
        company=company,
        context_ids=set(contexts),
        items=items,
        lot_ids=set(lots.values_list("id", flat=True)),
        filters=filters,
    )
    low = [
        row
        for row in prices.low_stock(company.id, scope.context_ids)
        if UUID(str(row["item"]["id"])) in items
    ]
    return {
        "scope": {
            "warehouses": sorted(c.location.name for c in contexts.values()),
            # § 21.1.1: with partial access the totals cover only the available warehouses.
            "partial": len(visible) < all_count,
        },
        "filters": {
            "date_from": filters.date_from.isoformat(),
            "date_to": filters.date_to.isoformat(),
            "time_zone": company.timezone or "UTC",
        },
        "currency": company.main_currency_id or "",
        "stock": _stock(scope),
        "value": _value(scope),
        "issued": _issued(scope),
        "in_transit": _in_transit(scope),
        "below_minimum": low,
        "expiring": _expiring(scope, company_today(company)),
        "expiry_horizon_days": EXPIRY_HORIZON_DAYS,
        "period": _period(scope),
        "purchases": _purchases(scope),
    }
