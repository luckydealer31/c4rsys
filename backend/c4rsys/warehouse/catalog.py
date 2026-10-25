"""Catalog commands and reads (ТЗ § 11.1.11–11.1.16, § 11.8.4–11.8.5).

Name and base unit are required; the accounting method follows the unit and is not chosen. The
base unit is not changed after creation, even before the first receipt: a wrong unit is fixed by
a new item, the old one is archived or, while unused, deleted.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, Q, QuerySet

from c4rsys.companies.models import Company
from c4rsys.files import services as files
from c4rsys.files.models import StoredFile
from c4rsys.fleet.models import VehicleMake, VehicleModel
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse import categories as part_catalog
from c4rsys.warehouse.models import Barcode, Item, ItemApplicability, Manufacturer, Packaging

INDIVIDUAL_UNITS = {Item.Unit.PIECE}
# Values of the attributes of spare parts (§ 11.1.13); «both» is the explicit universal value.
ATTRIBUTES: dict[str, list[str]] = {
    "side": ["left", "right", "both"],
    "longitudinal": ["front", "rear", "both"],
    "vertical": ["upper", "lower", "both"],
    "placement": ["inner", "outer", "both"],
    "originality": ["original", "analog"],
    # Customer's decision of 25.10.2026.
    "condition": ["new", "used"],
}
# Optional text data of the card (§ 22.4.1) with their maximum length.
TEXT_FIELDS = {
    "article": 100,
    "category": 100,
    "model_characteristic": 200,
    "photo_url": 500,
    "comment": 2000,
    "external_id": 100,
}
HUNDREDTH = Decimal("0.01")


def accounting_for(unit: str) -> str:
    return Item.Accounting.INDIVIDUAL if unit in INDIVIDUAL_UNITS else Item.Accounting.BATCH


def quantity(value: Any, field_name: str) -> Decimal:
    """A quantity of a material with at most two decimals (§ 11.1.15): more digits are refused,
    not rounded."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise errors.validation_error(field_name)
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise errors.validation_error(field_name) from exc
    if not number.is_finite() or number <= 0:
        raise errors.validation_error(field_name, code="QUANTITY_NOT_POSITIVE")
    if number != number.quantize(HUNDREDTH):
        raise errors.validation_error(field_name, code="QUANTITY_TOO_PRECISE")
    if number >= Decimal("10000000000"):
        raise errors.validation_error(field_name, code="OUT_OF_RANGE")
    return number.quantize(HUNDREDTH)


@dataclass(frozen=True)
class ItemData:
    name: str
    manufacturer_id: UUID | None = None
    manufacturer_other: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    photo_id: UUID | None = None
    # Category of the platform catalog of parts (decisions К1–К7); empty — without a category.
    part_category_id: UUID | None = None
    part_subcategory_id: UUID | None = None
    # (make code, model code or None)
    applicability: tuple[tuple[str, str | None], ...] = ()


def items_of(company_id: UUID) -> QuerySet[Item]:
    return (
        Item.objects.filter(company_id=company_id)
        .select_related("manufacturer", "part_category", "part_subcategory")
        .prefetch_related("packagings", "barcodes", "applicability")
        .order_by("name", "created_at")
    )


def get_item(company_id: UUID, item_id: UUID) -> Item:
    item = items_of(company_id).filter(pk=item_id).first()
    if item is None:
        raise errors.not_found()
    return item


def search(company_id: UUID, text: str, state: str = Item.State.ACTIVE) -> QuerySet[Item]:
    """By name, manufacturer or barcode."""
    found = items_of(company_id)
    if state:
        found = found.filter(state=state)
    text = text.strip()
    if text:
        found = found.filter(
            Q(name__icontains=text)
            | Q(article__icontains=text)
            | Q(category__icontains=text)
            | Q(part_category__name_ru__icontains=text)
            | Q(part_category__name_en__icontains=text)
            | Q(part_category__name_th__icontains=text)
            | Q(part_subcategory__name_ru__icontains=text)
            | Q(part_subcategory__name_en__icontains=text)
            | Q(part_subcategory__name_th__icontains=text)
            | Q(manufacturer__name__icontains=text)
            | Q(manufacturer_other__icontains=text)
            | Q(barcodes__code=text)
            | Q(external_id=text)
        ).distinct()
    return found


def snapshot(item: Item) -> dict[str, Any]:
    return {
        "name": item.name,
        "unit": item.unit,
        "accounting": item.accounting,
        "manufacturer": str(item.manufacturer_id) if item.manufacturer_id else None,
        "manufacturer_other": item.manufacturer_other,
        **{key: getattr(item, key) for key in ATTRIBUTES},
        **{key: getattr(item, key) for key in TEXT_FIELDS},
        "photo": str(item.photo_id) if item.photo_id else None,
        "part_category": str(item.part_category_id) if item.part_category_id else None,
        "part_subcategory": str(item.part_subcategory_id) if item.part_subcategory_id else None,
        "applicability": sorted(
            f"{a.make_id}/{a.model_id or ''}" for a in item.applicability.all()
        ),
        "state": item.state,
    }


def categories(company_id: UUID) -> list[str]:
    """Categories already used in the company, as suggestions (PROPOSAL: a category is text)."""
    return sorted(
        set(
            Item.objects.filter(company_id=company_id)
            .exclude(category="")
            .values_list("category", flat=True)
        )
    )


def _validate(data: ItemData) -> None:
    if data.manufacturer_id and not Manufacturer.objects.filter(pk=data.manufacturer_id).exists():
        raise errors.validation_error("manufacturer", code="MANUFACTURER_UNKNOWN")
    for key, value in data.attributes.items():
        if value and value not in ATTRIBUTES[key]:
            raise errors.validation_error(key)
    for make, model in data.applicability:
        if not VehicleMake.objects.filter(code=make).exists():
            raise errors.validation_error("applicability", code="MAKE_UNKNOWN")
        if model and not VehicleModel.objects.filter(code=model, make_id=make).exists():
            raise errors.validation_error("applicability", code="MODEL_UNKNOWN")


def _check_photo(data: ItemData, user_id: UUID, current: UUID | None) -> None:
    if data.photo_id is not None and data.photo_id != current:
        files.usable(data.photo_id, purpose=StoredFile.Purpose.ITEM_PHOTO, user_id=user_id)


def _apply(item: Item, data: ItemData) -> None:
    item.name = data.name
    item.manufacturer_id = data.manufacturer_id
    item.manufacturer_other = "" if data.manufacturer_id else data.manufacturer_other
    for key in ATTRIBUTES:
        setattr(item, key, data.attributes.get(key, ""))
    for key in TEXT_FIELDS:
        setattr(item, key, data.texts.get(key, ""))
    item.photo_id = data.photo_id
    item.part_category_id, item.part_subcategory_id = part_catalog.validate(
        data.part_category_id, data.part_subcategory_id
    )


def _write_applicability(item: Item, data: ItemData) -> None:
    item.applicability.all().delete()
    ItemApplicability.objects.bulk_create(
        ItemApplicability(company_id=item.company_id, item=item, make_id=make, model_id=model)
        for make, model in sorted(set(data.applicability), key=lambda x: (x[0], x[1] or ""))
    )


def create_item(
    ctx: CommandContext,
    company: Company,
    unit: str,
    data: ItemData,
    *,
    import_operation_id: UUID | None = None,
    possible_duplicate: bool = False,
) -> Item:
    if unit not in Item.Unit.values:
        raise errors.validation_error("unit")
    _validate(data)
    _check_photo(data, ctx.actor.user_id, None)
    item = Item(
        company=company,
        unit=unit,
        accounting=accounting_for(unit),
        created_by_user_id=ctx.actor.user_id,
        import_operation_id=import_operation_id,
        possible_duplicate=possible_duplicate,
    )
    _apply(item, data)
    item.save(force_insert=True)
    if item.photo_id:
        files.attach_to_company(item.photo_id, company.id)
    _write_applicability(item, data)
    ctx.audit(
        "warehouse.item_created",
        object_type="warehouse_item",
        object_id=item.id,
        after=snapshot(item),
    )
    return item


def _lock(company_id: UUID, item_id: UUID, expected_version: int | None = None) -> Item:
    item = Item.objects.select_for_update().filter(company_id=company_id, pk=item_id).first()
    if item is None:
        raise errors.not_found()
    if expected_version is not None and item.version != expected_version:
        raise errors.version_conflict()
    return item


def update_item(
    ctx: CommandContext, company_id: UUID, item_id: UUID, data: ItemData, expected_version: int
) -> Item:
    """Everything but the base unit and the accounting method (§ 11.1.11, § 11.8.5)."""
    item = _lock(company_id, item_id, expected_version)
    _validate(data)
    _check_photo(data, ctx.actor.user_id, item.photo_id)
    before = snapshot(item)
    _apply(item, data)
    item.version += 1
    item.save()
    if item.photo_id:
        files.attach_to_company(item.photo_id, company_id)
    _write_applicability(item, data)
    after = snapshot(item)
    changed = [k for k in after if before[k] != after[k]]
    ctx.audit(
        "warehouse.item_changed",
        object_type="warehouse_item",
        object_id=item.id,
        before={k: before[k] for k in changed},
        after={k: after[k] for k in changed},
    )
    return item


def set_archived(
    ctx: CommandContext, company_id: UUID, item_id: UUID, archived: bool, expected_version: int
) -> Item:
    item = _lock(company_id, item_id, expected_version)
    target = Item.State.ARCHIVED if archived else Item.State.ACTIVE
    if item.state == target:
        raise errors.business_rule("ITEM_ALREADY_IN_STATE", state=target)
    item.state = target
    item.version += 1
    item.save(update_fields=["state", "version", "updated_at"])
    ctx.audit(
        "warehouse.item_archived" if archived else "warehouse.item_restored",
        object_type="warehouse_item",
        object_id=item.id,
        after={"state": target},
    )
    return item


def delete_item(ctx: CommandContext, company_id: UUID, item_id: UUID) -> None:
    """Only an item nothing refers to yet; otherwise it is archived (§ 11.1.11)."""
    item = _lock(company_id, item_id)
    snap = snapshot(item)
    try:
        with transaction.atomic():
            item.barcodes.all().delete()
            item.applicability.all().delete()
            item.packagings.all().delete()
            item.delete()
    except ProtectedError as exc:
        raise errors.business_rule("ITEM_IN_USE") from exc
    ctx.audit(
        "warehouse.item_deleted",
        object_type="warehouse_item",
        object_id=item_id,
        before=snap,
    )


# --- Packagings and barcodes (§ 11.1.15–11.1.16) ------------------------------------------------


def add_packaging(
    ctx: CommandContext, company_id: UUID, item_id: UUID, amount: Decimal, name: str
) -> Packaging:
    item = _lock(company_id, item_id)
    if item.accounting != Item.Accounting.BATCH:
        raise errors.business_rule("PACKAGING_FOR_MATERIALS_ONLY")
    try:
        with transaction.atomic():
            packaging = Packaging.objects.create(
                company_id=company_id, item=item, quantity=amount, name=name
            )
    except IntegrityError as exc:
        raise errors.business_rule("PACKAGING_EXISTS", field="quantity") from exc
    ctx.audit(
        "warehouse.packaging_added",
        object_type="warehouse_packaging",
        object_id=packaging.id,
        after={"item": str(item.id), "quantity": str(amount), "name": name},
    )
    return packaging


def archive_packaging(
    ctx: CommandContext, company_id: UUID, item_id: UUID, packaging_id: UUID
) -> None:
    packaging = (
        Packaging.objects.select_for_update()
        .filter(company_id=company_id, item_id=item_id, pk=packaging_id)
        .first()
    )
    if packaging is None:
        raise errors.not_found()
    if packaging.state == Packaging.State.ARCHIVED:
        raise errors.business_rule("PACKAGING_ALREADY_ARCHIVED")
    packaging.state = Packaging.State.ARCHIVED
    packaging.save(update_fields=["state"])
    ctx.audit(
        "warehouse.packaging_archived",
        object_type="warehouse_packaging",
        object_id=packaging.id,
        after={"state": packaging.state},
    )


def add_barcode(
    ctx: CommandContext, company_id: UUID, item_id: UUID, code: str, packaging_id: UUID | None
) -> Barcode:
    item = _lock(company_id, item_id)
    if (
        packaging_id is not None
        and not item.packagings.filter(pk=packaging_id, state=Packaging.State.ACTIVE).exists()
    ):
        raise errors.validation_error("packaging_id", code="PACKAGING_UNKNOWN")
    try:
        with transaction.atomic():
            barcode = Barcode.objects.create(
                company_id=company_id, item=item, code=code, packaging_id=packaging_id
            )
    except IntegrityError as exc:
        raise errors.business_rule("BARCODE_TAKEN", field="code") from exc
    ctx.audit(
        "warehouse.barcode_added",
        object_type="warehouse_barcode",
        object_id=barcode.id,
        after={"item": str(item.id), "code": code, "packaging": str(packaging_id or "")},
    )
    return barcode


def remove_barcode(ctx: CommandContext, company_id: UUID, item_id: UUID, barcode_id: UUID) -> None:
    barcode = Barcode.objects.filter(company_id=company_id, item_id=item_id, pk=barcode_id).first()
    if barcode is None:
        raise errors.not_found()
    code = barcode.code
    barcode.delete()
    ctx.audit(
        "warehouse.barcode_removed",
        object_type="warehouse_barcode",
        object_id=barcode_id,
        before={"item": str(item_id), "code": code},
    )


def find_by_barcode(company_id: UUID, code: str) -> Barcode | None:
    return (
        Barcode.objects.select_related("item", "packaging")
        .filter(company_id=company_id, code=code.strip())
        .first()
    )
