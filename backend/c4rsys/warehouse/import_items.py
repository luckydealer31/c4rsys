"""Import template of the catalog (ТЗ § 22.4.1): its columns, checks and matches.

Selling price and minimum stock columns are not here yet (OPEN, part С5).
"""

from typing import Any
from uuid import UUID

from c4rsys.companies.models import Company
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse import catalog
from c4rsys.warehouse import categories as part_catalog
from c4rsys.warehouse.imports import Checked, Scope
from c4rsys.warehouse.models import Barcode, Item, Manufacturer

UNIT_ALIASES = {
    "шт": "piece",
    "шт.": "piece",
    "pcs": "piece",
    "л": "liter",
    "l": "liter",
    "кг": "kilogram",
    "kg": "kilogram",
    "м": "meter",
    "m": "meter",
}


def _manufacturer(name: str) -> Manufacturer | None:
    needle = name.lower()
    for m in Manufacturer.objects.all():
        if m.name.lower() == needle or any(a.lower() == needle for a in m.aliases):
            return m
    return None


class ItemsTemplate:
    kind = "items"
    version = 1
    sheet = "items"
    permission = "warehouse.catalog.manage"
    object_type = "warehouse_item"
    columns: dict[str, int] = {
        "name": 200,
        "unit": 20,
        "article": 100,
        "category": 100,
        "manufacturer": 150,
        "model_characteristic": 200,
        "side": 10,
        "longitudinal": 10,
        "vertical": 10,
        "placement": 10,
        "originality": 10,
        "condition": 10,
        "barcode": 64,
        "photo_url": 500,
        "comment": 2000,
        "external_id": 100,
    }
    required: tuple[str, ...] = ("name", "unit")
    help = {
        "name": (
            "Name of the item; required",
            "Название позиции; обязательно",
            "Shell Helix 5W-30",
        ),
        "unit": (
            "Unit code: piece, liter, kilogram, meter; required",
            "Код единицы: piece, liter, kilogram, meter; обязательно",
            "liter",
        ),
        "article": ("Article (part number)", "Артикул", "550046361"),
        "category": ("Category", "Категория", "Масла"),
        "manufacturer": (
            "Manufacturer as named in the catalog, or any name",
            "Производитель из справочника или любое название",
            "Shell",
        ),
        "model_characteristic": (
            "Model or characteristic",
            "Модель или характеристика",
            "5W-30, синтетика",
        ),
        "side": ("left, right, both", "left, right, both — левая, правая, обе", "both"),
        "longitudinal": ("front, rear, both", "front, rear, both — переднее, заднее, обе", ""),
        "vertical": ("upper, lower, both", "upper, lower, both — верхнее, нижнее, обе", ""),
        "placement": ("inner, outer, both", "inner, outer, both — внутреннее, наружное, обе", ""),
        "originality": (
            "original or analog",
            "original или analog — оригинал или аналог",
            "original",
        ),
        "condition": ("new or used", "new или used — новое или Б/У", "new"),
        "barcode": ("Barcode of the item", "Штрихкод позиции", "5011987000001"),
        "photo_url": ("Link to a photo", "Ссылка на фотографию", "https://example.com/oil.jpg"),
        "comment": ("Comment", "Комментарий", ""),
        "external_id": (
            "Your own ID of the item, used to find it again",
            "Ваш внешний ID позиции для повторного поиска",
            "OIL-001",
        ),
    }

    def references(self, company_id: UUID) -> list[tuple[str, list[list[str]]]]:
        return []

    def check_row(self, scope: Scope, values: dict[str, str]) -> Checked:
        result = Checked()
        if not values.get("name"):
            result.error("name", "FIELD_REQUIRED")
        unit = values.get("unit", "").strip()
        unit = UNIT_ALIASES.get(unit.lower(), unit.lower())
        if not unit:
            result.error("unit", "FIELD_REQUIRED")
        elif unit not in Item.Unit.values:
            result.error("unit", "UNIT_UNKNOWN")
        for key, allowed in catalog.ATTRIBUTES.items():
            value = values.get(key, "").strip().lower()
            if value and value not in allowed:
                result.error(key, "VALUE_UNKNOWN")
        barcode = values.get("barcode", "")
        if barcode:
            if scope.repeated("barcode", barcode):
                result.error("barcode", "BARCODE_REPEATED_IN_FILE")
            elif Barcode.objects.filter(company_id=scope.company_id, code=barcode).exists():
                result.error("barcode", "BARCODE_TAKEN")
        self._matches(scope.company_id, values, unit, result)
        result.extra = {"unit": unit}
        return result

    def _matches(self, company_id: UUID, values: dict[str, str], unit: str, out: Checked) -> None:
        """By external ID; without it — by article and manufacturer; the same name and unit
        shows a possible duplicate (§ 22.2.3)."""
        items = Item.objects.filter(company_id=company_id).exclude(state=Item.State.ARCHIVED)

        def add(queryset: Any, by: str) -> None:
            for item in queryset[:5]:
                out.match(item.id, item.name, by)

        if values.get("external_id"):
            add(items.filter(external_id=values["external_id"]), "external_id")
        elif values.get("article") and values.get("manufacturer"):
            maker = _manufacturer(values["manufacturer"])
            same = items.filter(article=values["article"])
            same = (
                same.filter(manufacturer=maker)
                if maker
                else same.filter(manufacturer_other__iexact=values["manufacturer"])
            )
            add(same, "article")
        if values.get("name") and unit:
            add(items.filter(name__iexact=values["name"], unit=unit), "name_unit")

    def create(
        self,
        ctx: CommandContext,
        company: Company,
        values: dict[str, str],
        extra: dict[str, Any],
        *,
        duplicate: bool,
        operation_id: UUID,
    ) -> UUID:
        maker = _manufacturer(values["manufacturer"]) if values.get("manufacturer") else None
        # К5: a category given in the file wins; otherwise it is chosen by the name.
        part_category, part_subcategory = part_catalog.by_text(values.get("category", ""))
        if part_category is None:
            found = part_catalog.suggest(values["name"])
            if found:
                part_category, part_subcategory = found[0].category_id, found[0].subcategory_id
        data = catalog.ItemData(
            name=values["name"],
            manufacturer_id=maker.id if maker else None,
            manufacturer_other="" if maker else values.get("manufacturer", ""),
            attributes={k: values.get(k, "").strip().lower() for k in catalog.ATTRIBUTES},
            texts={k: values.get(k, "") for k in catalog.TEXT_FIELDS},
            part_category_id=part_category,
            part_subcategory_id=part_subcategory,
        )
        item = catalog.create_item(
            ctx,
            company,
            extra["unit"],
            data,
            import_operation_id=operation_id,
            possible_duplicate=duplicate,
        )
        if values.get("barcode"):
            Barcode.objects.create(company=company, item=item, code=values["barcode"])
        return item.id

    def remove(self, ctx: CommandContext, company_id: UUID, record_ids: list[UUID]) -> None:
        for item in Item.objects.filter(company_id=company_id, pk__in=record_ids):
            item.barcodes.all().delete()
            item.applicability.all().delete()
            item.packagings.all().delete()
            item.delete()


ADAPTER = ItemsTemplate()
