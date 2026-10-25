"""Receipts, purchases and stock of the warehouse (ТЗ § 11.1.2–11.1.4, § 11.1.9–11.1.10, § 11.2).

Stock is kept only in the warehouses of locations (customer's decision of 25.10.2026). A receipt
is a draft until posted: the draft is changed or deleted freely, posting increases the stock by
what was actually received (§ 11.2.3, § 11.6.2). A purchase may be made in advance and received in
several receipts, or goods already bought are received directly (§ 11.2.12). The supplier may
stay «Not given» (customer's decision of 25.10.2026); payments come with the finance stage.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Prefetch, QuerySet, Sum
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import ActivityContext, Company
from c4rsys.files import services as files
from c4rsys.files.models import StoredFile
from c4rsys.parties import counterparties
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money
from c4rsys.platform.registry import allocate_public_id
from c4rsys.warehouse import catalog
from c4rsys.warehouse.models import (
    Asset,
    Item,
    Lot,
    LotLayer,
    LotStock,
    Packaging,
    PurchaseLine,
    PurchaseOrder,
    Receipt,
    ReceiptLine,
    StockMovement,
    SupplierArticle,
)

ZERO = Decimal("0.00")
MAX_LINES = 200
MAX_TAGS = 20
TAG_MAX = 50
SERIAL_MAX = 100


# --- Values -----------------------------------------------------------------------------------


def money(value: Any, field_name: str, minor_units: int) -> Decimal | None:
    """A purchase cost per base unit in the company currency. Empty — «Cost not given»
    (§ 11.2.6), never zero by default; an explicit zero is a value. More decimals than the
    currency has are refused, not rounded (§ 17.1.7)."""
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise errors.validation_error(field_name)
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise errors.validation_error(field_name) from exc
    if not number.is_finite() or number < 0:
        raise errors.validation_error(field_name, code="AMOUNT_NEGATIVE")
    step = Decimal(1).scaleb(-minor_units)
    if number != number.quantize(step):
        raise errors.validation_error(field_name, code="AMOUNT_TOO_PRECISE", max_places=minor_units)
    if number >= Decimal("10000000000"):
        raise errors.validation_error(field_name, code="OUT_OF_RANGE")
    return number.quantize(step)


def money_text(value: Decimal | None, currency: str) -> str | None:
    """An amount shown with the minor units of its currency; a computed one is rounded by
    § 17.1.7. Empty stays empty — «Cost not given»."""
    if value is None:
        return None
    minor = Currency.objects.filter(code=currency).values_list("minor_units", flat=True).first()
    return str(round_money(value, 2 if minor is None else minor))


def tags(value: Any, field_name: str = "tags") -> list[str]:
    """Optional text marks of a place (§ 11.1.9): hints where to look, no quantity per tag."""
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > MAX_TAGS:
        raise errors.validation_error(field_name)
    found: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or len(entry.strip()) > TAG_MAX:
            raise errors.validation_error(field_name)
        text = entry.strip()
        if text and text not in found:
            found.append(text)
    return found


def company_currency(company: Company) -> Currency:
    """Costs are kept in the main currency of the company (§ 17.1.7)."""
    currency = company.main_currency
    if currency is None:
        raise errors.business_rule("CURRENCY_NOT_SET")
    return currency


def _warehouse(company_id: UUID, context_id: UUID | None) -> ActivityContext:
    """An enabled warehouse of an active location of the company."""
    if context_id is None:
        raise errors.validation_error("context_id", code="FIELD_REQUIRED")
    context = locations.storage_context(company_id, context_id)
    if context is None:
        raise errors.validation_error("context_id", code="WAREHOUSE_NOT_USABLE")
    return context


# --- Receipt data -------------------------------------------------------------------------------


@dataclass(frozen=True)
class LineData:
    item_id: UUID
    quantity: Decimal | None = None
    packaging_id: UUID | None = None
    packs: int | None = None
    expected_quantity: Decimal | None = None
    unit_cost: Decimal | None = None
    tags: tuple[str, ...] = ()
    serial_numbers: tuple[str, ...] = ()
    lot_id: UUID | None = None
    lot_external_number: str = ""
    lot_production_date: date | None = None
    lot_expiry_date: date | None = None
    purchase_line_id: UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": str(self.item_id),
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "packaging_id": str(self.packaging_id) if self.packaging_id else None,
            "packs": self.packs,
            "expected_quantity": str(self.expected_quantity)
            if self.expected_quantity is not None
            else None,
            "unit_cost": str(self.unit_cost) if self.unit_cost is not None else None,
            "tags": list(self.tags),
            "serial_numbers": list(self.serial_numbers),
            "lot_id": str(self.lot_id) if self.lot_id else None,
            "lot_external_number": self.lot_external_number,
            "lot_production_date": self.lot_production_date.isoformat()
            if self.lot_production_date
            else None,
            "lot_expiry_date": self.lot_expiry_date.isoformat() if self.lot_expiry_date else None,
            "purchase_line_id": str(self.purchase_line_id) if self.purchase_line_id else None,
        }


@dataclass(frozen=True)
class ReceiptData:
    context_id: UUID | None
    source: str
    supplier_id: UUID | None = None
    purchase_id: UUID | None = None
    document_id: UUID | None = None
    received_at: datetime | None = None
    comment: str = ""
    lines: tuple[LineData, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_id": str(self.context_id) if self.context_id else None,
            "source": self.source,
            "supplier_id": str(self.supplier_id) if self.supplier_id else None,
            "purchase_id": str(self.purchase_id) if self.purchase_id else None,
            "document_id": str(self.document_id) if self.document_id else None,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "comment": self.comment,
            "lines": [line.as_dict() for line in self.lines],
        }


def _check_lot_dates(line: LineData, where: str) -> None:
    if (
        line.lot_production_date
        and line.lot_expiry_date
        and line.lot_expiry_date < line.lot_production_date
    ):
        raise errors.validation_error(f"{where}.lot_expiry_date", code="EXPIRY_BEFORE_PRODUCTION")


def _line_quantity(item: Item, line: LineData, where: str) -> tuple[Decimal, Packaging | None]:
    """Quantity in the base unit: entered directly, or as closed packages of a packaging
    (§ 11.1.15). An individual item is received by whole units, each one an asset."""
    if item.accounting == Item.Accounting.INDIVIDUAL:
        if line.packaging_id or line.packs:
            raise errors.validation_error(
                f"{where}.packaging_id", code="PACKAGING_FOR_MATERIALS_ONLY"
            )
        if (
            line.lot_id
            or line.lot_external_number
            or line.lot_production_date
            or line.lot_expiry_date
        ):
            raise errors.validation_error(f"{where}.lot_id", code="LOT_FOR_MATERIALS_ONLY")
        if line.quantity is None or line.quantity != line.quantity.to_integral_value():
            raise errors.validation_error(f"{where}.quantity", code="QUANTITY_WHOLE_UNITS")
        if len(line.serial_numbers) > int(line.quantity):
            raise errors.validation_error(f"{where}.serial_numbers", code="SERIALS_EXCEED_QUANTITY")
        return line.quantity, None
    if line.serial_numbers:
        raise errors.validation_error(f"{where}.serial_numbers", code="SERIALS_FOR_PIECES_ONLY")
    if line.packaging_id is None:
        if line.packs is not None:
            raise errors.validation_error(f"{where}.packaging_id", code="FIELD_REQUIRED")
        if line.quantity is None:
            raise errors.validation_error(f"{where}.quantity", code="FIELD_REQUIRED")
        return line.quantity, None
    packaging = Packaging.objects.filter(
        item=item, pk=line.packaging_id, state=Packaging.State.ACTIVE
    ).first()
    if packaging is None:
        raise errors.validation_error(f"{where}.packaging_id", code="PACKAGING_UNKNOWN")
    if not line.packs or line.packs <= 0:
        raise errors.validation_error(f"{where}.packs", code="QUANTITY_NOT_POSITIVE")
    total = packaging.quantity * line.packs
    if line.quantity is not None and line.quantity != total:
        raise errors.validation_error(f"{where}.quantity", code="QUANTITY_NOT_PACKS")
    return total, packaging


def _validate_lines(
    company_id: UUID,
    context: ActivityContext,
    purchase: PurchaseOrder | None,
    lines: tuple[LineData, ...],
) -> list[tuple[LineData, Item, Decimal, Packaging | None]]:
    if not lines:
        raise errors.validation_error("lines", code="LINES_REQUIRED")
    if len(lines) > MAX_LINES:
        raise errors.validation_error("lines", code="TOO_MANY_LINES", max=MAX_LINES)
    checked = []
    for index, line in enumerate(lines):
        where = f"lines[{index}]"
        item = Item.objects.filter(company_id=company_id, pk=line.item_id).first()
        if item is None:
            raise errors.validation_error(f"{where}.item_id", code="ITEM_UNKNOWN")
        if item.state != Item.State.ACTIVE:
            raise errors.validation_error(f"{where}.item_id", code="ITEM_ARCHIVED")
        amount, packaging = _line_quantity(item, line, where)
        _check_lot_dates(line, where)
        if line.lot_id is not None:
            lot = Lot.objects.filter(company_id=company_id, pk=line.lot_id).first()
            # A lot belongs to one warehouse; another warehouse gets a lot of its own.
            if lot is None or lot.item_id != item.id or lot.context_id != context.id:
                raise errors.validation_error(f"{where}.lot_id", code="LOT_UNKNOWN")
            if line.lot_external_number or line.lot_production_date or line.lot_expiry_date:
                raise errors.validation_error(f"{where}.lot_id", code="LOT_DATA_FOR_NEW_LOT_ONLY")
        if line.purchase_line_id is not None:
            if (
                purchase is None
                or not PurchaseLine.objects.filter(
                    purchase=purchase, pk=line.purchase_line_id, item_id=item.id
                ).exists()
            ):
                raise errors.validation_error(
                    f"{where}.purchase_line_id", code="PURCHASE_LINE_UNKNOWN"
                )
        checked.append((line, item, amount, packaging))
    return checked


def _write_lines(
    receipt: Receipt, checked: list[tuple[LineData, Item, Decimal, Packaging | None]]
) -> None:
    receipt.lines.all().delete()
    ReceiptLine.objects.bulk_create(
        ReceiptLine(
            company_id=receipt.company_id,
            receipt=receipt,
            item=item,
            purchase_line_id=line.purchase_line_id,
            quantity=amount,
            packaging=packaging,
            packs=line.packs if packaging else None,
            expected_quantity=line.expected_quantity,
            unit_cost=line.unit_cost,
            tags=list(line.tags),
            serial_numbers=list(line.serial_numbers),
            lot_id=line.lot_id,
            new_lot=item.accounting == Item.Accounting.BATCH and line.lot_id is None,
            lot_external_number=line.lot_external_number,
            lot_production_date=line.lot_production_date,
            lot_expiry_date=line.lot_expiry_date,
            position=index,
        )
        for index, (line, item, amount, packaging) in enumerate(checked)
    )


def _resolve_parties(
    company_id: UUID, data: ReceiptData
) -> tuple[UUID | None, PurchaseOrder | None]:
    """Supplier and purchase of a receipt. An initial stock has neither (§ 11.2.8); a receipt by
    a purchase takes the purchase's supplier."""
    if data.source == Receipt.Source.INITIAL:
        if data.supplier_id or data.purchase_id:
            raise errors.validation_error("supplier_id", code="INITIAL_WITHOUT_SUPPLIER")
        return None, None
    if data.source != Receipt.Source.PURCHASE:
        raise errors.validation_error("source")
    purchase = None
    supplier_id = data.supplier_id
    if data.purchase_id is not None:
        purchase = PurchaseOrder.objects.filter(company_id=company_id, pk=data.purchase_id).first()
        if purchase is None:
            raise errors.validation_error("purchase_id", code="PURCHASE_UNKNOWN")
        if supplier_id is not None and supplier_id != purchase.supplier_id:
            raise errors.validation_error("supplier_id", code="SUPPLIER_OF_PURCHASE")
        supplier_id = purchase.supplier_id
    elif supplier_id is not None:
        counterparties.usable(company_id, supplier_id)
    return supplier_id, purchase


def _check_document(
    data: ReceiptData, user_id: UUID, company_id: UUID, current: UUID | None
) -> None:
    """An invoice, receipt or other document is optional (§ 11.2.8)."""
    if data.document_id is not None and data.document_id != current:
        files.usable(
            data.document_id,
            purpose=StoredFile.Purpose.WAREHOUSE_DOCUMENT,
            user_id=user_id,
            company_id=company_id,
        )


def receipt_snapshot(receipt: Receipt) -> dict[str, Any]:
    return {
        "number": receipt.number,
        "context": str(receipt.context_id),
        "source": receipt.source,
        "supplier": str(receipt.supplier_id) if receipt.supplier_id else None,
        "purchase": str(receipt.purchase_id) if receipt.purchase_id else None,
        "document": str(receipt.document_id) if receipt.document_id else None,
        "received_at": receipt.received_at.isoformat(),
        "comment": receipt.comment,
        "state": receipt.state,
        "lines": [
            {
                "item": str(line.item_id),
                "quantity": str(line.quantity),
                "packaging": str(line.packaging_id) if line.packaging_id else None,
                "packs": line.packs,
                "expected_quantity": str(line.expected_quantity)
                if line.expected_quantity is not None
                else None,
                "unit_cost": str(line.unit_cost) if line.unit_cost is not None else None,
                "lot": str(line.lot_id) if line.lot_id else None,
                "tags": line.tags,
                "serial_numbers": line.serial_numbers,
            }
            for line in receipt.lines.order_by("position")
        ],
    }


def create_receipt(ctx: CommandContext, company: Company, data: ReceiptData) -> Receipt:
    context = _warehouse(company.id, data.context_id)
    currency = company_currency(company)
    supplier_id, purchase = _resolve_parties(company.id, data)
    checked = _validate_lines(company.id, context, purchase, data.lines)
    _check_document(data, ctx.actor.user_id, company.id, None)
    receipt = Receipt.objects.create(
        company=company,
        number=next_number(company.id, "warehouse_receipt"),
        context=context,
        source=data.source,
        supplier_id=supplier_id,
        purchase=purchase,
        document_id=data.document_id,
        currency=currency.code,
        comment=data.comment,
        received_at=data.received_at or timezone.now(),
        created_by_user_id=ctx.actor.user_id,
    )
    _write_lines(receipt, checked)
    if data.document_id:
        files.attach_to_company(data.document_id, company.id)
    ctx.audit(
        "warehouse.receipt_created",
        object_type="warehouse_receipt",
        object_id=receipt.id,
        after=receipt_snapshot(receipt),
    )
    return receipt


def _lock_receipt(
    company_id: UUID, receipt_id: UUID, expected_version: int | None = None
) -> Receipt:
    receipt = (
        Receipt.objects.select_for_update().filter(company_id=company_id, pk=receipt_id).first()
    )
    if receipt is None:
        raise errors.not_found()
    if expected_version is not None and receipt.version != expected_version:
        raise errors.version_conflict()
    return receipt


def _require_draft(receipt: Receipt) -> None:
    if receipt.state != Receipt.State.DRAFT:
        # § 11.6.2: after posting a correction is a separate action with a reason.
        raise errors.business_rule("RECEIPT_ALREADY_POSTED")


def update_receipt(
    ctx: CommandContext,
    company: Company,
    receipt_id: UUID,
    data: ReceiptData,
    expected_version: int,
) -> Receipt:
    """A draft is changed freely (§ 11.6.2): the whole content is replaced."""
    receipt = _lock_receipt(company.id, receipt_id, expected_version)
    _require_draft(receipt)
    context = _warehouse(company.id, data.context_id)
    supplier_id, purchase = _resolve_parties(company.id, data)
    checked = _validate_lines(company.id, context, purchase, data.lines)
    _check_document(data, ctx.actor.user_id, company.id, receipt.document_id)
    before = receipt_snapshot(receipt)
    receipt.context = context
    receipt.source = data.source
    receipt.supplier_id = supplier_id
    receipt.purchase = purchase
    receipt.document_id = data.document_id
    receipt.comment = data.comment
    receipt.received_at = data.received_at or receipt.received_at
    receipt.version += 1
    receipt.save()
    _write_lines(receipt, checked)
    if data.document_id:
        files.attach_to_company(data.document_id, company.id)
    after = receipt_snapshot(receipt)
    changed = [k for k in after if before[k] != after[k]]
    ctx.audit(
        "warehouse.receipt_changed",
        object_type="warehouse_receipt",
        object_id=receipt.id,
        before={k: before[k] for k in changed},
        after={k: after[k] for k in changed},
    )
    return receipt


def delete_receipt(ctx: CommandContext, company_id: UUID, receipt_id: UUID) -> None:
    """Only a draft is deleted (§ 11.6.2)."""
    receipt = _lock_receipt(company_id, receipt_id)
    _require_draft(receipt)
    snap = receipt_snapshot(receipt)
    receipt.lines.all().delete()
    receipt.delete()
    ctx.audit(
        "warehouse.receipt_deleted",
        object_type="warehouse_receipt",
        object_id=receipt_id,
        before=snap,
    )


def _add_lot_stock(lot: Lot, packaging: Packaging | None, amount: Decimal) -> None:
    row, _ = LotStock.objects.select_for_update().get_or_create(
        company_id=lot.company_id, lot=lot, packaging=packaging
    )
    row.quantity += amount
    row.save(update_fields=["quantity"])


def post_receipt(
    ctx: CommandContext, company_id: UUID, receipt_id: UUID, expected_version: int
) -> Receipt:
    """Stock grows by the received quantity (§ 11.2.3): one asset with its own AST ID per unit of
    an individual item, a new or chosen lot with its own layer for a material (§ 11.2.14). The
    data is checked again on fresh rows."""
    receipt = _lock_receipt(company_id, receipt_id, expected_version)
    _require_draft(receipt)
    context = locations.storage_context(company_id, receipt.context_id)
    if context is None:
        raise errors.business_rule("WAREHOUSE_NOT_USABLE", field="context_id")
    if receipt.supplier_id is not None and receipt.purchase_id is None:
        counterparties.usable(company_id, receipt.supplier_id)
    now = timezone.now()
    created_assets: list[str] = []
    created_lots: list[str] = []
    lines = list(receipt.lines.select_related("item", "packaging").order_by("position"))
    for index, line in enumerate(lines):
        where = f"lines[{index}]"
        item = Item.objects.select_for_update().get(pk=line.item_id)
        if item.state != Item.State.ACTIVE:
            raise errors.business_rule("ITEM_ARCHIVED", field=f"{where}.item_id")
        if line.packaging is not None and line.packaging.state != Packaging.State.ACTIVE:
            raise errors.business_rule("PACKAGING_UNKNOWN", field=f"{where}.packaging_id")
        if item.accounting == Item.Accounting.INDIVIDUAL:
            for unit in range(int(line.quantity)):
                asset = Asset(
                    company_id=company_id,
                    item=item,
                    context=context,
                    serial_number=line.serial_numbers[unit]
                    if unit < len(line.serial_numbers)
                    else "",
                    tags=line.tags,
                    purchase_cost=line.unit_cost,
                    currency=receipt.currency,
                    receipt_line=line,
                    created_at=now,
                )
                asset.public_id = allocate_public_id(
                    "AST", entity_type="warehouse_asset", entity_id=asset.id
                )
                asset.save(force_insert=True)
                created_assets.append(asset.public_id)
                StockMovement.objects.create(
                    company_id=company_id,
                    kind=StockMovement.Kind.RECEIPT,
                    context=context,
                    item=item,
                    asset=asset,
                    quantity=Decimal(1),
                    receipt_line=line,
                    actor_user_id=ctx.actor.user_id,
                    created_at=now,
                )
            continue
        if line.new_lot:
            lot = Lot(
                company_id=company_id,
                item=item,
                context=context,
                external_number=line.lot_external_number,
                production_date=line.lot_production_date,
                expiry_date=line.lot_expiry_date,
                tags=line.tags,
                currency=receipt.currency,
                created_at=now,
            )
            lot.public_id = allocate_public_id("LOT", entity_type="warehouse_lot", entity_id=lot.id)
            lot.save(force_insert=True)
            created_lots.append(lot.public_id)
            line.lot = lot
            line.save(update_fields=["lot"])
        else:
            found = (
                Lot.objects.select_for_update()
                .filter(company_id=company_id, pk=line.lot_id or UUID(int=0))
                .first()
            )
            if found is None or found.context_id != context.id or found.item_id != item.id:
                raise errors.business_rule("LOT_UNKNOWN", field=f"{where}.lot_id")
            lot = found
            if line.tags:
                lot.tags = list(dict.fromkeys([*lot.tags, *line.tags]))
                lot.save(update_fields=["tags"])
        LotLayer.objects.create(
            company_id=company_id,
            lot=lot,
            receipt_line=line,
            received_quantity=line.quantity,
            remaining=line.quantity,
            unit_cost=line.unit_cost,
            received_at=receipt.received_at,
        )
        _add_lot_stock(lot, line.packaging, line.quantity)
        StockMovement.objects.create(
            company_id=company_id,
            kind=StockMovement.Kind.RECEIPT,
            context=context,
            item=item,
            lot=lot,
            packaging_id=line.packaging_id,
            quantity=line.quantity,
            receipt_line=line,
            actor_user_id=ctx.actor.user_id,
            created_at=now,
        )
    receipt.state = Receipt.State.POSTED
    receipt.posted_at = now
    receipt.posted_by_user_id = ctx.actor.user_id
    receipt.version += 1
    receipt.save(update_fields=["state", "posted_at", "posted_by_user_id", "version"])
    ctx.audit(
        "warehouse.receipt_posted",
        object_type="warehouse_receipt",
        object_id=receipt.id,
        after={**receipt_snapshot(receipt), "assets": created_assets, "lots": created_lots},
    )
    return receipt


def set_line_cost(
    ctx: CommandContext, company: Company, receipt_id: UUID, line_id: UUID, unit_cost: Decimal
) -> ReceiptLine:
    """A cost that was not given is entered later (§ 11.2.6) for the line, its assets and its lot
    layer; the history stays in the journal. Changing a cost already given is a correction of a
    posted receipt (part С3). Supplementing the cost of used material (§ 11.2.5) comes with
    consumption."""
    receipt = _lock_receipt(company.id, receipt_id)
    line = ReceiptLine.objects.select_for_update().filter(receipt=receipt, pk=line_id).first()
    if line is None:
        raise errors.not_found()
    if line.unit_cost is not None:
        raise errors.business_rule("COST_ALREADY_GIVEN")
    line.unit_cost = unit_cost
    line.save(update_fields=["unit_cost"])
    if receipt.state == Receipt.State.POSTED:
        Asset.objects.filter(receipt_line=line).update(purchase_cost=unit_cost)
        LotLayer.objects.filter(receipt_line=line).update(unit_cost=unit_cost)
    ctx.audit(
        "warehouse.receipt_cost_entered",
        object_type="warehouse_receipt",
        object_id=receipt.id,
        before={"line": str(line.id), "unit_cost": None},
        after={"line": str(line.id), "unit_cost": str(unit_cost)},
    )
    return line


# --- Reading receipts --------------------------------------------------------------------------


def receipts_of(company_id: UUID) -> QuerySet[Receipt]:
    return (
        Receipt.objects.filter(company_id=company_id)
        .select_related("context__location", "purchase")
        .prefetch_related(
            Prefetch(
                "lines",
                queryset=ReceiptLine.objects.select_related("item", "packaging", "lot").order_by(
                    "position"
                ),
            ),
            "lines__assets",
        )
        .order_by("-number")
    )


def get_receipt(company_id: UUID, receipt_id: UUID) -> Receipt:
    receipt = receipts_of(company_id).filter(pk=receipt_id).first()
    if receipt is None:
        raise errors.not_found()
    return receipt


# --- Purchases (§ 11.2.10–11.2.12) ------------------------------------------------------------


@dataclass(frozen=True)
class PurchaseLineData:
    item_id: UUID
    quantity: Decimal
    unit_cost: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": str(self.item_id),
            "quantity": str(self.quantity),
            "unit_cost": str(self.unit_cost) if self.unit_cost is not None else None,
        }


def create_purchase(
    ctx: CommandContext,
    company: Company,
    supplier_id: UUID | None,
    comment: str,
    lines: tuple[PurchaseLineData, ...],
) -> PurchaseOrder:
    currency = company_currency(company)
    if supplier_id is not None:
        counterparties.usable(company.id, supplier_id)
    if not lines:
        raise errors.validation_error("lines", code="LINES_REQUIRED")
    if len(lines) > MAX_LINES:
        raise errors.validation_error("lines", code="TOO_MANY_LINES", max=MAX_LINES)
    items = []
    for index, line in enumerate(lines):
        where = f"lines[{index}]"
        item = Item.objects.filter(company_id=company.id, pk=line.item_id).first()
        if item is None:
            raise errors.validation_error(f"{where}.item_id", code="ITEM_UNKNOWN")
        if item.state != Item.State.ACTIVE:
            raise errors.validation_error(f"{where}.item_id", code="ITEM_ARCHIVED")
        if (
            item.accounting == Item.Accounting.INDIVIDUAL
            and line.quantity != line.quantity.to_integral_value()
        ):
            raise errors.validation_error(f"{where}.quantity", code="QUANTITY_WHOLE_UNITS")
        items.append(item)
    purchase = PurchaseOrder.objects.create(
        company=company,
        number=next_number(company.id, "warehouse_purchase"),
        supplier_id=supplier_id,
        currency=currency.code,
        comment=comment,
        created_by_user_id=ctx.actor.user_id,
    )
    PurchaseLine.objects.bulk_create(
        PurchaseLine(
            company_id=company.id,
            purchase=purchase,
            item=item,
            ordered_quantity=line.quantity,
            unit_cost=line.unit_cost,
            position=index,
        )
        for index, (line, item) in enumerate(zip(lines, items, strict=True))
    )
    ctx.audit(
        "warehouse.purchase_created",
        object_type="warehouse_purchase",
        object_id=purchase.id,
        after={
            "number": purchase.number,
            "supplier": str(supplier_id) if supplier_id else None,
            "comment": comment,
            "lines": [line.as_dict() for line in lines],
        },
    )
    return purchase


def received_by_line(line_ids: list[UUID]) -> dict[UUID, Decimal]:
    """Received by posted receipts only; a draft does not count (§ 11.2.11)."""
    rows = (
        ReceiptLine.objects.filter(
            purchase_line_id__in=line_ids, receipt__state=Receipt.State.POSTED
        )
        .values("purchase_line_id")
        .annotate(total=Sum("quantity"))
    )
    return {row["purchase_line_id"]: row["total"] for row in rows}


def expected(line: PurchaseLine, received: Decimal) -> Decimal:
    """Still expected: ordered minus cancelled minus received, not below zero. Receiving more
    than ordered is shown as a discrepancy (§ 11.2.3)."""
    return max(line.ordered_quantity - line.cancelled_quantity - received, ZERO)


def cancel_rest(
    ctx: CommandContext, company_id: UUID, purchase_id: UUID, line_ids: list[UUID] | None
) -> PurchaseOrder:
    """The undelivered rest is no longer expected (§ 11.2.10): what was received and the history
    stay, the expected quantity goes down. A prepayment is settled separately."""
    purchase = (
        PurchaseOrder.objects.select_for_update()
        .filter(company_id=company_id, pk=purchase_id)
        .first()
    )
    if purchase is None:
        raise errors.not_found()
    lines = list(purchase.lines.select_for_update().order_by("position"))
    if line_ids is not None:
        known = {line.id for line in lines}
        if not set(line_ids) <= known:
            raise errors.validation_error("line_ids", code="PURCHASE_LINE_UNKNOWN")
        lines = [line for line in lines if line.id in set(line_ids)]
    received = received_by_line([line.id for line in lines])
    cancelled = []
    for line in lines:
        rest = expected(line, received.get(line.id, ZERO))
        if rest > 0:
            line.cancelled_quantity += rest
            line.save(update_fields=["cancelled_quantity"])
            cancelled.append({"line": str(line.id), "cancelled": str(rest)})
    if not cancelled:
        raise errors.business_rule("NOTHING_EXPECTED")
    purchase.version += 1
    purchase.save(update_fields=["version"])
    ctx.audit(
        "warehouse.purchase_rest_cancelled",
        object_type="warehouse_purchase",
        object_id=purchase.id,
        after={"lines": cancelled},
    )
    return purchase


def purchases_of(company_id: UUID) -> QuerySet[PurchaseOrder]:
    return (
        PurchaseOrder.objects.filter(company_id=company_id)
        .prefetch_related(
            Prefetch(
                "lines", queryset=PurchaseLine.objects.select_related("item").order_by("position")
            )
        )
        .order_by("-number")
    )


def get_purchase(company_id: UUID, purchase_id: UUID) -> PurchaseOrder:
    purchase = purchases_of(company_id).filter(pk=purchase_id).first()
    if purchase is None:
        raise errors.not_found()
    return purchase


# --- Supplier articles (§ 11.2.17) --------------------------------------------------------------


def add_supplier_article(
    ctx: CommandContext,
    company_id: UUID,
    item_id: UUID,
    supplier_id: UUID,
    article: str,
    supplier_name: str,
) -> SupplierArticle:
    item = catalog.get_item(company_id, item_id)
    counterparties.usable(company_id, supplier_id)
    try:
        with transaction.atomic():
            entry = SupplierArticle.objects.create(
                company_id=company_id,
                supplier_id=supplier_id,
                article=article,
                item=item,
                supplier_name=supplier_name,
            )
    except IntegrityError as exc:
        raise errors.business_rule("SUPPLIER_ARTICLE_TAKEN", field="article") from exc
    ctx.audit(
        "warehouse.supplier_article_added",
        object_type="warehouse_supplier_article",
        object_id=entry.id,
        after={
            "item": str(item.id),
            "supplier": str(supplier_id),
            "article": article,
            "supplier_name": supplier_name,
        },
    )
    return entry


def remove_supplier_article(
    ctx: CommandContext, company_id: UUID, item_id: UUID, entry_id: UUID
) -> None:
    entry = SupplierArticle.objects.filter(
        company_id=company_id, item_id=item_id, pk=entry_id
    ).first()
    if entry is None:
        raise errors.not_found()
    before = {"item": str(item_id), "supplier": str(entry.supplier_id), "article": entry.article}
    entry.delete()
    ctx.audit(
        "warehouse.supplier_article_removed",
        object_type="warehouse_supplier_article",
        object_id=entry_id,
        before=before,
    )


def find_supplier_article(
    company_id: UUID, supplier_id: UUID, article: str
) -> SupplierArticle | None:
    """At a purchase the article of the chosen supplier gives the linked item."""
    return (
        SupplierArticle.objects.select_related("item")
        .filter(company_id=company_id, supplier_id=supplier_id, article__iexact=article.strip())
        .first()
    )


# --- Tags (§ 11.1.9) ----------------------------------------------------------------------------


def set_tags(
    ctx: CommandContext, company_id: UUID, kind: str, record_id: UUID, values: list[str]
) -> Asset | Lot:
    model = Asset if kind == "asset" else Lot
    record = model.objects.select_for_update().filter(company_id=company_id, pk=record_id).first()
    if record is None:
        raise errors.not_found()
    before = list(record.tags)
    record.tags = values
    record.save(update_fields=["tags"])
    ctx.audit(
        "warehouse.tags_changed",
        object_type=f"warehouse_{kind}",
        object_id=record.id,
        before={"tags": before},
        after={"tags": values},
    )
    return record


def record_context(company_id: UUID, kind: str, record_id: UUID) -> ActivityContext:
    model = Asset if kind == "asset" else Lot
    record = (
        model.objects.select_related("context").filter(company_id=company_id, pk=record_id).first()
    )
    if record is None:
        raise errors.not_found()
    return record.context


# --- Reading stock (§ 11.1.1, § 11.1.10, § 11.2.13) ----------------------------------------------


def expired(lot: Lot, today: date | None = None) -> bool:
    """Past the expiry date the lot is marked expired, not written off (§ 11.2.15)."""
    return lot.expiry_date is not None and lot.expiry_date < (today or timezone.localdate())


def totals(company_id: UUID, context_ids: set[UUID]) -> dict[UUID, dict[UUID, dict[str, Any]]]:
    """item -> warehouse -> quantity and tags: assets in stock count as units, lots by their
    physical stock. A tag is a hint and does not make a stock of its own (§ 11.1.10)."""
    result: dict[UUID, dict[UUID, dict[str, Any]]] = defaultdict(dict)

    def cell(item_id: UUID, context_id: UUID) -> dict[str, Any]:
        return result[item_id].setdefault(context_id, {"quantity": ZERO, "tags": set()})

    for asset in Asset.objects.filter(
        company_id=company_id, context_id__in=context_ids, state=Asset.State.IN_STOCK
    ).only("item_id", "context_id", "tags"):
        entry = cell(asset.item_id, asset.context_id)
        entry["quantity"] += 1
        entry["tags"].update(asset.tags)
    lots = {
        lot.id: lot
        for lot in Lot.objects.filter(company_id=company_id, context_id__in=context_ids).only(
            "id", "item_id", "context_id", "tags"
        )
    }
    for row in (
        LotStock.objects.filter(lot_id__in=lots).values("lot_id").annotate(total=Sum("quantity"))
    ):
        if row["total"] <= 0:
            continue
        lot = lots[row["lot_id"]]
        entry = cell(lot.item_id, lot.context_id)
        entry["quantity"] += row["total"]
        entry["tags"].update(lot.tags)
    return result


def item_stock(company_id: UUID, item_id: UUID, context_ids: set[UUID]) -> dict[str, Any]:
    """The expanded row of an item: per warehouse its assets or lots with quantities by
    packaging, their own tags and the actual purchase costs of each receipt (§ 11.1.10,
    § 11.2.13)."""
    item = catalog.get_item(company_id, item_id)
    contexts = {
        c.id: c
        for c in ActivityContext.objects.select_related("location").filter(
            company_id=company_id, pk__in=context_ids
        )
    }
    places: dict[UUID, dict[str, Any]] = {}

    def place(context_id: UUID) -> dict[str, Any]:
        context = contexts[context_id]
        return places.setdefault(
            context_id,
            {
                "context_id": str(context_id),
                "location_name": context.location.name,
                "quantity": ZERO,
                "assets": [],
                "lots": [],
            },
        )

    numbers = dict(
        ReceiptLine.objects.filter(company_id=company_id, item=item).values_list(
            "id", "receipt__number"
        )
    )
    if item.accounting == Item.Accounting.INDIVIDUAL:
        for asset in Asset.objects.filter(
            company_id=company_id, item=item, context_id__in=context_ids, state=Asset.State.IN_STOCK
        ).order_by("created_at"):
            entry = place(asset.context_id)
            entry["quantity"] += 1
            entry["assets"].append(
                {
                    "id": str(asset.id),
                    "public_id": asset.public_id,
                    "serial_number": asset.serial_number,
                    "tags": asset.tags,
                    "purchase_cost": money_text(asset.purchase_cost, asset.currency),
                    "currency": asset.currency,
                    "receipt_number": numbers.get(asset.receipt_line_id)
                    if asset.receipt_line_id
                    else None,
                    "received_at": asset.created_at.isoformat(),
                }
            )
    else:
        packaging_names = {p.id: p for p in item.packagings.all()}
        today = timezone.localdate()
        for lot in (
            Lot.objects.filter(company_id=company_id, item=item, context_id__in=context_ids)
            .prefetch_related("stock", "layers")
            .order_by("created_at")
        ):
            stock = [row for row in lot.stock.all() if row.quantity > 0]
            quantity = sum((row.quantity for row in stock), ZERO)
            if quantity <= 0:
                continue
            entry = place(lot.context_id)
            entry["quantity"] += quantity
            entry["lots"].append(
                {
                    "id": str(lot.id),
                    "public_id": lot.public_id,
                    "external_number": lot.external_number,
                    "production_date": lot.production_date.isoformat()
                    if lot.production_date
                    else None,
                    "expiry_date": lot.expiry_date.isoformat() if lot.expiry_date else None,
                    "expired": expired(lot, today),
                    "tags": lot.tags,
                    "quantity": str(quantity),
                    "currency": lot.currency,
                    "stock": [
                        {
                            "packaging_id": str(row.packaging_id) if row.packaging_id else None,
                            "packaging_quantity": str(packaging_names[row.packaging_id].quantity)
                            if row.packaging_id
                            else None,
                            "packs": int(row.quantity / packaging_names[row.packaging_id].quantity)
                            if row.packaging_id
                            else None,
                            "quantity": str(row.quantity),
                        }
                        for row in sorted(
                            stock, key=lambda r: (r.packaging_id is None, str(r.packaging_id))
                        )
                    ],
                    "layers": [
                        {
                            "receipt_number": numbers.get(layer.receipt_line_id),
                            "received_at": layer.received_at.isoformat(),
                            "received_quantity": str(layer.received_quantity),
                            "remaining": str(layer.remaining),
                            "unit_cost": money_text(layer.unit_cost, lot.currency),
                        }
                        for layer in sorted(lot.layers.all(), key=lambda la: la.received_at)
                    ],
                }
            )
    return {
        "item_id": str(item.id),
        "places": [
            {**p, "quantity": str(p["quantity"])}
            for p in sorted(places.values(), key=lambda p: p["location_name"])
        ],
    }


def lots_for_receipt(
    company_id: UUID, item_id: UUID, context_id: UUID, external_number: str
) -> list[dict[str, Any]]:
    """Lots of the item in the warehouse to add a receipt to (§ 11.2.14). A lot with the same
    external number is shown as a possible existing one; the employee chooses it or a new lot
    (§ 11.2.16)."""
    needle = external_number.strip().lower()
    found = []
    for lot in Lot.objects.filter(
        company_id=company_id, item_id=item_id, context_id=context_id
    ).order_by("-created_at"):
        found.append(
            {
                "id": str(lot.id),
                "public_id": lot.public_id,
                "external_number": lot.external_number,
                "production_date": lot.production_date.isoformat() if lot.production_date else None,
                "expiry_date": lot.expiry_date.isoformat() if lot.expiry_date else None,
                "matches": bool(needle) and lot.external_number.strip().lower() == needle,
            }
        )
    found.sort(key=lambda entry: not entry["matches"])
    return found
