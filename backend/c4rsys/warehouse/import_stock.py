"""Import template of initial stock (ТЗ § 22.4.2): its columns, checks and posting.

An individual item is imported by one row per unit, each unit gets its own AST ID. A material is
imported into a lot: a new one by its code in the file — rows with one code make one group and
one LOT ID — or an existing one by its LOT ID. A row is closed packages of a packaging, one opened
package with its actual rest, or loose material in the base unit. The stock is posted by
receipts of initial stock without a supplier (§ 11.2.8), one per warehouse and import.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from c4rsys.companies import access, locations
from c4rsys.companies.models import Company
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse import catalog, stock
from c4rsys.warehouse.imports import Checked, Scope
from c4rsys.warehouse.models import Item, Lot, Packaging, SalePrice

YES = {"yes", "y", "1", "true", "да", "ใช่"}
NO = {"", "no", "n", "0", "false", "нет", "ไม่"}
PRICE_RIGHT = "warehouse.price.set"


def _decimal(text: str) -> Decimal | None:
    try:
        number = Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None
    if not number.is_finite() or number != number.quantize(Decimal("0.01")):
        return None
    return number.quantize(Decimal("0.01"))


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _uuid(text: str) -> UUID | None:
    try:
        return UUID(text)
    except ValueError:
        return None


class StockTemplate:
    kind = "stock"
    version = 1
    sheet = "stock"
    permission = "warehouse.receive"
    object_type = "warehouse_receipt_line"
    # A match by the external number of a lot is only a possible existing lot (§ 22.2.3): the row
    # is skipped or loaded into a new lot anyway, never «linked» without stock.
    links = False
    columns: dict[str, int] = {
        "item_id": 36,
        "context_id": 36,
        "lot_code": 50,
        "lot_id": 40,
        "lot_external_number": 100,
        "production_date": 10,
        "expiry_date": 10,
        "packaging_quantity": 14,
        "packaging_name": 100,
        "packs": 9,
        "opened": 5,
        "quantity": 14,
        "unit_cost": 20,
        "sale_price": 20,
        "serial_number": 100,
        "place_tags": 500,
    }
    required: tuple[str, ...] = ("item_id", "context_id")
    # The common fields of the rows of one new lot (§ 22.4.2).
    group_fields: tuple[str, ...] = (
        "item_id",
        "context_id",
        "lot_external_number",
        "production_date",
        "expiry_date",
    )
    help = {
        "item_id": (
            "ID of an existing item (the «items» sheet); required",
            "ID существующей номенклатуры (лист «items»); обязательно",
            "",
        ),
        "context_id": (
            "ID of a warehouse context (the «contexts» sheet); required",
            "ID контекста хранения (лист «contexts»); обязательно",
            "",
        ),
        "lot_code": (
            "Material: code of a new lot in this file; rows with one code make one lot",
            "Материал: код новой партии в файле; строки с одним кодом образуют одну партию",
            "A1",
        ),
        "lot_id": (
            "Material: LOT ID of an existing lot instead of a code of a new one",
            "Материал: LOT ID существующей партии вместо кода новой",
            "",
        ),
        "lot_external_number": (
            "External lot number of the producer",
            "Внешний номер партии производителя",
            "",
        ),
        "production_date": ("Production date, YYYY-MM-DD", "Дата производства, ГГГГ-ММ-ДД", ""),
        "expiry_date": ("Expiry date, YYYY-MM-DD", "Срок годности, ГГГГ-ММ-ДД", ""),
        "packaging_quantity": (
            "Content of the packaging in the base unit; empty — loose material",
            "Содержимое фасовки в базовой единице; пусто — материал без упаковки",
            "4",
        ),
        "packaging_name": (
            "Name of a new packaging, used only when it is created",
            "Название новой фасовки, используется только при её создании",
            "Канистра 4 л",
        ),
        "packs": (
            "Number of closed packages",
            "Количество закрытых упаковок",
            "3",
        ),
        "opened": (
            "yes — one opened package; its actual rest goes to «quantity»",
            "yes — одна вскрытая упаковка; её фактический остаток — в «quantity»",
            "",
        ),
        "quantity": (
            "Base unit: loose material or the rest of an opened package; for an individual item"
            " empty or 1",
            "В базовой единице: материал без упаковки или остаток вскрытой упаковки; для"
            " штучного учёта пусто или 1",
            "",
        ),
        "unit_cost": (
            "Purchase cost per base unit in the company currency",
            "Закупочная стоимость за базовую единицу в валюте компании",
            "250",
        ),
        "sale_price": (
            "Selling price: of the unit, or of the lot in the lot price mode",
            "Цена продажи: экземпляра или партии в режиме цен по партиям",
            "",
        ),
        "serial_number": (
            "Individual item: external or serial ID of the unit",
            "Штучный учёт: внешний или серийный ID экземпляра",
            "",
        ),
        "place_tags": (
            "Marks of the place, separated by commas",
            "Отметки места хранения через запятую",
            "Стеллаж 2",
        ),
    }

    def references(self, company_id: UUID) -> list[tuple[str, list[list[str]]]]:
        contexts = [["id", "location"]]
        contexts += [
            [str(c.id), c.location.name]
            for c in locations.storage_contexts(company_id).filter(
                state="enabled", location__deactivated_at__isnull=True
            )
        ]
        items = [["id", "name", "unit", "article", "packagings"]]
        for item in (
            Item.objects.filter(company_id=company_id, state=Item.State.ACTIVE)
            .prefetch_related("packagings")
            .order_by("name")
        ):
            sizes = sorted(p.quantity for p in item.packagings.all() if p.state == "active")
            items.append(
                [str(item.id), item.name, item.unit, item.article, ", ".join(map(str, sizes))]
            )
        lots = [["lot_id", "item", "context_id", "external_number", "expiry_date"]]
        lots += [
            [
                lot.public_id,
                lot.item.name,
                str(lot.context_id),
                lot.external_number,
                lot.expiry_date.isoformat() if lot.expiry_date else "",
            ]
            for lot in Lot.objects.filter(company_id=company_id)
            .select_related("item")
            .order_by("item__name", "created_at")[:5000]
        ]
        return [("contexts", contexts), ("items", items), ("lots", lots)]

    def group(self, values: dict[str, str]) -> str | None:
        return values.get("lot_code") or None

    def check_row(self, scope: Scope, values: dict[str, str]) -> Checked:
        out = Checked()
        item = self._item(scope, values, out)
        context = self._context(scope, values, out)
        company = Company.objects.select_related("main_currency").get(pk=scope.company_id)
        minor = company.main_currency.minor_units if company.main_currency else 2
        for code in ("unit_cost", "sale_price"):
            try:
                amount = stock.money(values.get(code, ""), code, minor)
            except errors.ApiError as exc:
                out.error(code, exc.code)
            else:
                out.extra[code] = str(amount) if amount is not None else None
        if values.get("sale_price") and not access.RightsResolver().has_permission(
            scope.actor, PRICE_RIGHT
        ):
            out.error("sale_price", "PRICE_NOT_ALLOWED")
        text = values.get("place_tags", "")
        try:
            out.extra["tags"] = stock.tags([t for t in text.split(",") if t.strip()], "place_tags")
        except errors.ApiError:
            out.error("place_tags", "TAGS_INVALID")
        opened = values.get("opened", "").strip().lower()
        if opened not in YES | NO:
            out.error("opened", "VALUE_UNKNOWN")
        out.extra["opened"] = opened in YES
        if item is None:
            return out
        if item.accounting == Item.Accounting.INDIVIDUAL:
            self._unit(values, out)
        else:
            self._material(scope, values, out, item, context)
        return out

    def _item(self, scope: Scope, values: dict[str, str], out: Checked) -> Item | None:
        item_id = _uuid(values.get("item_id", ""))
        item = (
            Item.objects.filter(company_id=scope.company_id, pk=item_id).first()
            if item_id
            else None
        )
        if item is None:
            out.error("item_id", "ITEM_UNKNOWN")
            return None
        if item.state != Item.State.ACTIVE:
            out.error("item_id", "ITEM_ARCHIVED")
            return None
        out.extra["item_id"] = str(item.id)
        out.extra["item_name"] = item.name
        return item

    def _context(self, scope: Scope, values: dict[str, str], out: Checked) -> Any:
        context_id = _uuid(values.get("context_id", ""))
        context = locations.storage_context(scope.company_id, context_id) if context_id else None
        if context is None:
            # A context without storage is an error of the row (§ 22.4.2).
            out.error("context_id", "WAREHOUSE_NOT_USABLE")
            return None
        # Import does not open a warehouse the employee has no access to (§ 22.5.1).
        if not access.RightsResolver().has_permission(
            scope.actor,
            self.permission,
            location_id=context.location_id,
            context_id=context.id,
        ):
            out.error("context_id", "CONTEXT_NOT_ALLOWED")
        out.extra["context_id"] = str(context.id)
        return context

    def _unit(self, values: dict[str, str], out: Checked) -> None:
        """One row is one unit with its own AST ID (§ 22.4.2)."""
        for code in ("lot_code", "lot_id", "lot_external_number", "production_date", "expiry_date"):
            if values.get(code):
                out.error(code, "LOT_FOR_MATERIALS_ONLY")
        for code in ("packaging_quantity", "packs"):
            if values.get(code):
                out.error(code, "PACKAGING_FOR_MATERIALS_ONLY")
        if out.extra.get("opened"):
            out.error("opened", "PACKAGING_FOR_MATERIALS_ONLY")
        if values.get("quantity", "") not in ("", "1"):
            out.error("quantity", "IMPORT_ONE_UNIT_PER_ROW")

    def _material(
        self, scope: Scope, values: dict[str, str], out: Checked, item: Item, context: Any
    ) -> None:
        if values.get("serial_number"):
            out.error("serial_number", "SERIALS_FOR_PIECES_ONLY")
        code, lot_ref = values.get("lot_code", ""), values.get("lot_id", "")
        lot = None
        if code and lot_ref:
            out.error("lot_id", "LOT_CODE_AND_LOT_ID")  # the employee leaves one (§ 22.4.2)
        elif not code and not lot_ref:
            out.error("lot_code", "LOT_REQUIRED")
        elif lot_ref:
            lot = Lot.objects.filter(company_id=scope.company_id, public_id=lot_ref.upper()).first()
            if lot is None or lot.item_id != item.id or (context and lot.context_id != context.id):
                out.error("lot_id", "LOT_UNKNOWN")
                lot = None
            else:
                out.extra["lot_id"] = str(lot.id)
            for field in ("lot_external_number", "production_date", "expiry_date"):
                if values.get(field):
                    out.error(field, "LOT_DATA_FOR_NEW_LOT_ONLY")
        dates: dict[str, date] = {}
        for field in ("production_date", "expiry_date"):
            if values.get(field):
                parsed = _date(values[field])
                if parsed is None:
                    out.error(field, "DATE_INVALID")
                else:
                    dates[field] = parsed
        produced, expires = dates.get("production_date"), dates.get("expiry_date")
        if produced and expires and expires < produced:
            out.error("expiry_date", "EXPIRY_BEFORE_PRODUCTION")
        external = values.get("lot_external_number", "")
        if code and external:
            # The external number of the same item only shows a possible existing lot (§ 11.2.16).
            for found in Lot.objects.filter(
                company_id=scope.company_id, item=item, external_number=external
            )[:5]:
                out.match(found.id, found.public_id, "lot_external_number")
        self._form(values, out, item)
        self._price(scope, values, out, item, lot)

    def _form(self, values: dict[str, str], out: Checked, item: Item) -> None:
        """Closed packages, one opened package or loose material (§ 11.1.15–11.1.17)."""
        size_text = values.get("packaging_quantity", "")
        packs_text = values.get("packs", "")
        quantity_text = values.get("quantity", "")
        quantity = _decimal(quantity_text) if quantity_text else None
        if quantity_text and (quantity is None or quantity <= 0):
            out.error("quantity", "QUANTITY_NOT_POSITIVE")
            return
        opened = bool(out.extra.get("opened"))
        if not size_text:
            if packs_text:
                out.error("packaging_quantity", "FIELD_REQUIRED")
            elif opened:
                out.error("packaging_quantity", "FIELD_REQUIRED")
            elif quantity is None:
                out.error("quantity", "FIELD_REQUIRED")
            out.extra["quantity"] = str(quantity) if quantity is not None else None
            return
        size = _decimal(size_text)
        if size is None or size <= 0:
            out.error("packaging_quantity", "QUANTITY_NOT_POSITIVE")
            return
        packaging = Packaging.objects.filter(
            item=item, quantity=size, state=Packaging.State.ACTIVE
        ).first()
        if packaging is not None:
            out.extra["packaging_id"] = str(packaging.id)
        else:
            # Shown with its parameters and created only after an explicit confirmation by an
            # employee with the right; never automatically (§ 22.4.2).
            out.extra["packaging_missing"] = {
                "quantity": str(size),
                "name": values.get("packaging_name", ""),
                "unit": item.unit,
            }
        if opened:
            if packs_text:
                out.error("packs", "OPENED_ONE_PACKAGE")
            if quantity is None:
                out.error("quantity", "FIELD_REQUIRED")
            elif quantity > size:
                out.error("quantity", "OPENED_REST_EXCEEDS_PACKAGING")
            out.extra["quantity"] = str(quantity) if quantity is not None else None
            return
        try:
            packs = int(packs_text)
        except ValueError:
            packs = 0
        if packs <= 0:
            out.error("packs", "QUANTITY_NOT_POSITIVE" if packs_text else "FIELD_REQUIRED")
            return
        # The number of packages and the computed total are not added up twice (§ 22.4.2).
        if quantity is not None and quantity != size * packs:
            out.error("quantity", "QUANTITY_NOT_PACKS")
        out.extra["packs"] = packs

    def _price(
        self, scope: Scope, values: dict[str, str], out: Checked, item: Item, lot: Lot | None
    ) -> None:
        """A material has prices of its lots only in the lot price mode (§ 11.7.6), one price per
        lot and form; an existing price of a lot is not changed by an import."""
        price = out.extra.get("sale_price")
        if not values.get("sale_price") or price is None:
            return
        if item.price_mode != "lot":
            out.error("sale_price", "PRICE_ITEM_MODE")
            return
        packaging_id = out.extra.get("packaging_id") if not out.extra.get("opened") else None
        size = values.get("packaging_quantity", "") if not out.extra.get("opened") else ""
        lot_key = values.get("lot_code") or values.get("lot_id", "").upper()
        given = scope.seen.setdefault(f"price:{lot_key}:{size}", set())
        if given and price not in given:
            out.error("sale_price", "SALE_PRICE_DIFFERS")
        given.add(price)
        if lot is not None:
            prices = SalePrice.objects.filter(item=item, lot=lot)
            current = (
                prices.filter(packaging__isnull=True)
                if packaging_id is None
                else prices.filter(packaging_id=UUID(str(packaging_id)))
            ).first()
            if current is not None and current.amount != Decimal(price):
                out.error("sale_price", "LOT_PRICE_EXISTS")

    def create(
        self,
        ctx: CommandContext,
        company: Company,
        values: dict[str, str],
        extra: dict[str, Any],
        *,
        duplicate: bool,
        operation_id: UUID,
        session: dict[str, Any],
    ) -> UUID:
        packaging_id = extra.get("packaging_id")
        missing = extra.get("packaging_missing")
        if missing and not packaging_id:
            if not extra.get("confirmed"):
                raise errors.business_rule("IMPORT_PACKAGING_CONFIRM", field="packaging_quantity")
            item_id = UUID(extra["item_id"])
            size = Decimal(missing["quantity"])
            found = Packaging.objects.filter(
                item_id=item_id, quantity=size, state=Packaging.State.ACTIVE
            ).first()
            packaging = found or catalog.add_packaging(
                ctx, company.id, item_id, size, missing.get("name", "")
            )
            packaging_id = str(packaging.id)

        def number(key: str) -> Decimal | None:
            value = extra.get(key)
            return Decimal(value) if value not in (None, "") else None

        line = stock.post_initial_line(
            ctx,
            company,
            stock.InitialLine(
                item_id=UUID(extra["item_id"]),
                context_id=UUID(extra["context_id"]),
                quantity=number("quantity"),
                packaging_id=UUID(packaging_id) if packaging_id else None,
                packs=extra.get("packs"),
                opened=bool(extra.get("opened")),
                lot_id=UUID(extra["lot_id"]) if extra.get("lot_id") else None,
                new_lot=values.get("lot_code", ""),
                lot_external_number=values.get("lot_external_number", ""),
                lot_production_date=_date(values["production_date"])
                if values.get("production_date")
                else None,
                lot_expiry_date=_date(values["expiry_date"]) if values.get("expiry_date") else None,
                unit_cost=number("unit_cost"),
                sale_price=number("sale_price"),
                serial_number=values.get("serial_number", ""),
                tags=tuple(extra.get("tags") or ()),
            ),
            import_id=operation_id,
            session=session,
        )
        return line.id

    def remove(self, ctx: CommandContext, company_id: UUID, record_ids: list[UUID]) -> None:
        stock.cancel_initial_lines(ctx, company_id, record_ids)


ADAPTER = StockTemplate()
