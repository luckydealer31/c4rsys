"""XLSX import of the catalog (ТЗ § 22.2, § 22.4.1).

Two steps: the file is checked and its errors and matches are shown; the user chooses what to do
with each row and confirms. Correct rows are loaded, wrong ones skipped with their reasons. A
match never merges or overwrites an existing card: the row is skipped, linked to the card or
created as a possible duplicate. Just before posting the rows are checked again against current
data. The operation keeps its file, author, time, counts and links; it is cancelled only as a
whole while its cards are unused.

Not yet here (OPEN in this part): re-upload of a corrected file by row codes (§ 22.2.7), the six
month removal of source files (§ 22.2.4), selling price and minimum stock columns (part С5).
"""

import hashlib
import io
from dataclasses import dataclass
from typing import Any
from uuid import UUID
from zipfile import BadZipFile

from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from c4rsys.companies.models import Company
from c4rsys.files import services as files
from c4rsys.files.models import StoredFile
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse import catalog
from c4rsys.warehouse.models import Barcode, ImportOperation, Item, Manufacturer

TEMPLATE = "items"
TEMPLATE_VERSION = 1
META_SHEET = "c4rsys"
DATA_SHEET = "items"
HELP_SHEET = "help"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Technical codes of the columns (§ 22.2.6) with their maximum length.
COLUMNS: dict[str, int] = {
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
REQUIRED = ("name", "unit")
# Human explanations for the help sheet, in English and Russian (§ 22.2.6).
HELP = {
    "name": ("Name of the item; required", "Название позиции; обязательно", "Shell Helix 5W-30"),
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
    "originality": ("original or analog", "original или analog — оригинал или аналог", "original"),
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


def template() -> bytes:
    """The XLSX template of the catalog: technical codes in the first row, explanations and an
    example on a separate sheet, the template and its version on the service sheet."""
    book = Workbook()
    sheet = book.create_sheet(DATA_SHEET, 0)
    for extra in book.sheetnames[1:]:
        del book[extra]
    sheet.append(list(COLUMNS))
    sheet.append([HELP[c][2] for c in COLUMNS])
    help_sheet = book.create_sheet(HELP_SHEET)
    help_sheet.append(["code", "required", "en", "ru", "example"])
    for code in COLUMNS:
        en, ru, example = HELP[code]
        help_sheet.append([code, "yes" if code in REQUIRED else "", en, ru, example])
    help_sheet.append([])
    help_sheet.append(
        [
            "",
            "",
            "The second row of the «items» sheet is an example: replace or delete it.",
            "Вторая строка листа «items» — пример: замените или удалите её.",
            "",
        ]
    )
    meta = book.create_sheet(META_SHEET)
    meta.append(["template", TEMPLATE])
    meta.append(["version", TEMPLATE_VERSION])
    meta.sheet_state = "hidden"
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


@dataclass(frozen=True)
class Parsed:
    rows: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


def parse(content: bytes) -> Parsed:
    """Rows of the data sheet as text values; the file must be this template (§ 22.2.6)."""
    try:
        book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, BadZipFile, KeyError, ValueError, OSError) as exc:
        raise errors.validation_error("file", code="IMPORT_FILE_NOT_XLSX") from exc
    if META_SHEET not in book.sheetnames or DATA_SHEET not in book.sheetnames:
        raise errors.validation_error("file", code="IMPORT_TEMPLATE_UNSUPPORTED")
    meta = {
        _cell(r[0]): _cell(r[1])
        for r in book[META_SHEET].iter_rows(values_only=True)
        if r and len(r) > 1
    }
    if meta.get("template") != TEMPLATE or meta.get("version") != str(TEMPLATE_VERSION):
        raise errors.validation_error("file", code="IMPORT_TEMPLATE_UNSUPPORTED")
    rows = list(book[DATA_SHEET].iter_rows(values_only=True))
    if not rows:
        raise errors.validation_error("file", code="IMPORT_TEMPLATE_UNSUPPORTED")
    header = [_cell(c) for c in rows[0]]
    warnings = [{"code": "UNKNOWN_COLUMN", "column": c} for c in header if c and c not in COLUMNS]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise errors.validation_error("file", code="IMPORT_COLUMNS_MISSING", columns=missing)
    result = []
    for number, row in enumerate(rows[1:], start=2):
        values = {
            code: _cell(row[i]) if i < len(row) else ""
            for i, code in enumerate(header)
            if code in COLUMNS
        }
        if not any(values.values()):
            continue  # empty rows are ignored (§ 22.2.6)
        result.append({"number": number, "values": values})
    if len(result) > MAX_ROWS:
        raise errors.validation_error("file", code="IMPORT_TOO_MANY_ROWS", max_rows=MAX_ROWS)
    return Parsed(result, warnings)


def _manufacturer(name: str) -> Manufacturer | None:
    needle = name.lower()
    for m in Manufacturer.objects.all():
        if m.name.lower() == needle or any(a.lower() == needle for a in m.aliases):
            return m
    return None


def _check_row(company_id: UUID, row: dict[str, Any], seen_barcodes: set[str]) -> dict[str, Any]:
    """Errors and matches of one row against the current data (§ 22.2.2–22.2.3, § 22.4.1)."""
    values = row["values"]
    problems: list[dict[str, str]] = []
    for code, length in COLUMNS.items():
        if len(values.get(code, "")) > length:
            problems.append({"field": code, "code": "FIELD_TOO_LONG"})
    if not values.get("name"):
        problems.append({"field": "name", "code": "FIELD_REQUIRED"})
    unit = values.get("unit", "").strip()
    unit = UNIT_ALIASES.get(unit.lower(), unit.lower())
    if not unit:
        problems.append({"field": "unit", "code": "FIELD_REQUIRED"})
    elif unit not in Item.Unit.values:
        problems.append({"field": "unit", "code": "UNIT_UNKNOWN"})
    for key, allowed in catalog.ATTRIBUTES.items():
        value = values.get(key, "").strip().lower()
        if value and value not in allowed:
            problems.append({"field": key, "code": "VALUE_UNKNOWN"})
    barcode = values.get("barcode", "")
    if barcode:
        if barcode in seen_barcodes:
            problems.append({"field": "barcode", "code": "BARCODE_REPEATED_IN_FILE"})
        elif Barcode.objects.filter(company_id=company_id, code=barcode).exists():
            problems.append({"field": "barcode", "code": "BARCODE_TAKEN"})
        seen_barcodes.add(barcode)
    matches = _matches(company_id, values, unit)
    return {**row, "unit": unit, "errors": problems, "matches": matches}


def _matches(company_id: UUID, values: dict[str, str], unit: str) -> list[dict[str, str]]:
    """By external ID; without it — by article and manufacturer; the same name and unit shows a
    possible duplicate (§ 22.2.3). Several candidates are shown without a choice."""
    items = Item.objects.filter(company_id=company_id).exclude(state=Item.State.ARCHIVED)
    found: list[dict[str, str]] = []

    def add(queryset: Any, by: str) -> None:
        for item in queryset[:5]:
            if all(f["id"] != str(item.id) for f in found):
                found.append({"id": str(item.id), "name": item.name, "by": by})

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
    return found


def _default_action(row: dict[str, Any]) -> str:
    if row["errors"]:
        return "error"
    return "skip" if row["matches"] else "create"


def check(ctx: CommandContext, company: Company, content: bytes, file_name: str) -> ImportOperation:
    """Step one: the file is stored with the operation, rows are checked; nothing is created."""
    if len(content) > MAX_FILE_BYTES:
        raise errors.validation_error("file", code="FILE_TOO_LARGE", max_bytes=MAX_FILE_BYTES)
    parsed = parse(content)
    digest = hashlib.sha256(content).hexdigest()
    warnings = list(parsed.warnings)
    previous = (
        ImportOperation.objects.filter(company=company, sha256=digest)
        .exclude(state=ImportOperation.State.CANCELLED)
        .order_by("-created_at")
        .first()
    )
    if previous is not None:
        # § 22.2.7: the same file again — a warning with the previous result.
        warnings.append(
            {
                "code": "REPEATED_FILE",
                "import": str(previous.id),
                "loaded": previous.loaded_count,
                "skipped": previous.skipped_count,
            }
        )
    stored = files.store(
        purpose=StoredFile.Purpose.IMPORT_FILE,
        uploaded_by=ctx.actor.user_id,
        content=content,
        content_type=XLSX,
    )
    files.attach_to_company(stored.id, company.id)
    seen: set[str] = set()
    rows = []
    for index, row in enumerate(parsed.rows, start=1):
        checked = _check_row(company.id, row, seen)
        checked["code"] = f"R{index:04d}"
        checked["action"] = _default_action(checked)
        checked["target"] = None
        checked["result"] = None
        checked["item_id"] = None
        rows.append(checked)
    operation = ImportOperation.objects.create(
        company=company,
        kind=ImportOperation.Kind.ITEMS,
        template_version=TEMPLATE_VERSION,
        file=stored,
        file_name=file_name[:255],
        sha256=digest,
        rows=rows,
        warnings=warnings,
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "warehouse.import_checked",
        object_type="warehouse_import",
        object_id=operation.id,
        after={
            "file": file_name[:255],
            "rows": len(rows),
            "errors": sum(1 for r in rows if r["errors"]),
            "matches": sum(1 for r in rows if r["matches"]),
        },
    )
    return operation


def _lock(
    company_id: UUID, operation_id: UUID, expected_version: int | None = None
) -> ImportOperation:
    operation = (
        ImportOperation.objects.select_for_update()
        .filter(company_id=company_id, pk=operation_id)
        .first()
    )
    if operation is None:
        raise errors.not_found()
    if expected_version is not None and operation.version != expected_version:
        raise errors.version_conflict()
    return operation


ACTIONS = {"create", "skip", "link", "create_duplicate"}


def decide(
    ctx: CommandContext,
    company_id: UUID,
    operation_id: UUID,
    decisions: list[tuple[str, str, str | None]],
    expected_version: int,
) -> ImportOperation:
    """The user's choice for rows: create, skip, link to an existing card or create a possible
    duplicate (§ 22.2.3, § 22.4.1). A row with errors can only be skipped."""
    operation = _lock(company_id, operation_id, expected_version)
    if operation.state != ImportOperation.State.CHECKED:
        raise errors.business_rule("IMPORT_NOT_CHECKED")
    by_code = {r["code"]: r for r in operation.rows}
    for code, action, target in decisions:
        row = by_code.get(code)
        if row is None:
            raise errors.validation_error("decisions", code="IMPORT_ROW_UNKNOWN", row=code)
        if action not in ACTIONS:
            raise errors.validation_error("decisions", code="IMPORT_ACTION_UNKNOWN", row=code)
        if row["errors"] and action != "skip":
            raise errors.business_rule("IMPORT_ROW_HAS_ERRORS", row=code)
        if action == "link" and target not in {m["id"] for m in row["matches"]}:
            raise errors.validation_error("decisions", code="IMPORT_LINK_TARGET", row=code)
        if action == "create" and row["matches"]:
            raise errors.business_rule("IMPORT_ROW_HAS_MATCH", row=code)
        row["action"] = action
        row["target"] = target if action == "link" else None
    operation.version += 1
    operation.save(update_fields=["rows", "version"])
    ctx.audit(
        "warehouse.import_decided",
        object_type="warehouse_import",
        object_id=operation.id,
        after={"decisions": [{"row": c, "action": a, "target": t} for c, a, t in decisions]},
    )
    return operation


def _item_data(row: dict[str, Any]) -> tuple[str, catalog.ItemData]:
    values = row["values"]
    maker = _manufacturer(values["manufacturer"]) if values.get("manufacturer") else None
    data = catalog.ItemData(
        name=values["name"],
        manufacturer_id=maker.id if maker else None,
        manufacturer_other="" if maker else values.get("manufacturer", ""),
        attributes={k: values.get(k, "").strip().lower() for k in catalog.ATTRIBUTES},
        texts={k: values.get(k, "") for k in catalog.TEXT_FIELDS},
    )
    return row["unit"], data


def post(
    ctx: CommandContext, company: Company, operation_id: UUID, expected_version: int
) -> ImportOperation:
    """Step two: rows are checked again against current data; a changed result is a conflict
    and the row is not posted (§ 22.2.2). Each row is posted on its own."""
    operation = _lock(company.id, operation_id, expected_version)
    if operation.state != ImportOperation.State.CHECKED:
        raise errors.business_rule("IMPORT_NOT_CHECKED")
    seen: set[str] = set()
    loaded = skipped = 0
    rows = []
    for row in operation.rows:
        current = _check_row(company.id, {"number": row["number"], "values": row["values"]}, seen)
        action = row["action"]
        result: dict[str, Any] = {**row, "errors": current["errors"], "unit": current["unit"]}
        known = {m["id"] for m in row["matches"]}
        fresh = {m["id"] for m in current["matches"]}
        if action in ("skip", "error"):
            result["result"] = "skipped"
            skipped += 1
        elif current["errors"] or (action == "create" and fresh - known):
            result["result"] = "conflict"
            result["matches"] = current["matches"]
            skipped += 1
        elif action == "link":
            if row["target"] not in fresh:
                result["result"] = "conflict"
                skipped += 1
            else:
                result["result"] = "linked"
                result["item_id"] = row["target"]
                loaded += 1
        else:
            unit, data = _item_data(row)
            item = catalog.create_item(
                ctx,
                company,
                unit,
                data,
                import_operation_id=operation.id,
                possible_duplicate=action == "create_duplicate",
            )
            if row["values"].get("barcode"):
                Barcode.objects.create(company=company, item=item, code=row["values"]["barcode"])
            result["result"] = "created"
            result["item_id"] = str(item.id)
            loaded += 1
        rows.append(result)
    operation.rows = rows
    operation.state = ImportOperation.State.POSTED
    operation.posted_at = timezone.now()
    operation.loaded_count = loaded
    operation.skipped_count = skipped
    operation.version += 1
    operation.save()
    ctx.audit(
        "warehouse.import_posted",
        object_type="warehouse_import",
        object_id=operation.id,
        after={"loaded": loaded, "skipped": skipped},
    )
    return operation


def cancel(
    ctx: CommandContext, company_id: UUID, operation_id: UUID, expected_version: int
) -> ImportOperation:
    """Only as a whole and only while the created cards are unused (§ 22.2.5); links to existing
    cards are simply forgotten."""
    operation = _lock(company_id, operation_id, expected_version)
    if operation.state != ImportOperation.State.POSTED:
        raise errors.business_rule("IMPORT_NOT_POSTED")
    created = list(operation.items.all())
    try:
        with transaction.atomic():
            for item in created:
                item.barcodes.all().delete()
                item.applicability.all().delete()
                item.packagings.all().delete()
                item.delete()
    except ProtectedError as exc:
        raise errors.business_rule("IMPORT_CANCEL_BLOCKED") from exc
    operation.state = ImportOperation.State.CANCELLED
    operation.cancelled_at = timezone.now()
    operation.version += 1
    operation.save(update_fields=["state", "cancelled_at", "version"])
    ctx.audit(
        "warehouse.import_cancelled",
        object_type="warehouse_import",
        object_id=operation.id,
        after={"deleted_items": [str(i.id) for i in created]},
    )
    return operation


def payload(operation: ImportOperation, with_rows: bool = True) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(operation.id),
        "version": operation.version,
        "kind": operation.kind,
        "state": operation.state,
        "file_name": operation.file_name,
        "created_at": operation.created_at.isoformat(),
        "posted_at": operation.posted_at.isoformat() if operation.posted_at else None,
        "cancelled_at": operation.cancelled_at.isoformat() if operation.cancelled_at else None,
        "loaded_count": operation.loaded_count,
        "skipped_count": operation.skipped_count,
        "rows_count": len(operation.rows),
        "warnings": operation.warnings,
    }
    if with_rows:
        body["rows"] = operation.rows
    return body
