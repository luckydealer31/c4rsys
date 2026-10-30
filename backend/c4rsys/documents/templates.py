"""Templates and documents made by them (ТЗ § 18.1.4–18.1.5, § 18.2, § 18.5.2; customer's decisions
Р-1…Р-3 of 29.10.2026).

The content of a template is text with simple formatting (decision Р-1):

* `# `, `## `, `### ` at the start of a line — headings; `- ` — an item of a list;
* `**text**` — bold; an empty line separates paragraphs;
* `{{source.field}}` — a field; `{{table:source.collection}}` alone on a line — a table.

The made document keeps the rendered content of its version; later changes of the cards or of the
template do not rewrite it (§ 18.2.4, § 18.5.2). A required field without data stops the making
until it is filled; an empty optional field is warned of (§ 18.2.1).
"""

import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies import staff
from c4rsys.companies.models import Company
from c4rsys.documents import fields, services
from c4rsys.documents.models import Document, DocumentTemplate
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

FIELD = re.compile(r"\{\{\s*([a-z_]+\.[a-z_]+)\s*\}\}")
TABLE = re.compile(r"^\s*\{\{\s*table:([a-z_]+\.[a-z_]+)\s*\}\}\s*$")
BOLD = re.compile(r"\*\*(.+?)\*\*")
SOURCES_OF = {
    "rental": {"rental", "order", "client", "vehicle", "location"},
    "order": {"order", "client"},
    "service_operation": {"service", "order", "client", "vehicle", "location"},
    "delivery": {"delivery", "order", "client", "vehicle", "location"},
    "client": {"client"},
    "vehicle": {"vehicle"},
    "inspection": {"inspection", "client", "vehicle", "location"},
    "none": set(),
}
TABLE_SOURCES = {
    "order.lines": "order",
    "rental.charges": "rental",
    "service.works": "service",
    "service.materials": "service",
    "inspection.damages": "inspection",
    "inspection.photos": "inspection",
}
COLUMN_LABELS = {
    "name": {"en": "Item", "ru": "Наименование", "th": "รายการ"},
    "amount": {"en": "Amount", "ru": "Сумма", "th": "จำนวนเงิน"},
    "executor": {"en": "Performed by", "ru": "Исполнитель", "th": "ผู้ดำเนินการ"},
    "price": {"en": "Price", "ru": "Цена", "th": "ราคา"},
    "quantity": {"en": "Quantity", "ru": "Количество", "th": "จำนวน"},
    "zone": {"en": "Zone", "ru": "Зона", "th": "ตำแหน่ง"},
    "description": {"en": "Description", "ru": "Описание", "th": "รายละเอียด"},
    "state": {"en": "State", "ru": "Состояние", "th": "สถานะ"},
    "position": {"en": "Position", "ru": "Позиция", "th": "มุมถ่าย"},
    "count": {"en": "Photos", "ru": "Снимков", "th": "จำนวนรูป"},
}


def codes_in(content: str) -> tuple[set[str], set[str]]:
    """Fields and tables a content uses."""
    tables = {m.group(1) for line in content.splitlines() if (m := TABLE.match(line))}
    return set(FIELD.findall(content)), tables


def validate(content: str, source: str, required: list[str]) -> None:
    """Only codes of the catalog, available for the source of the template (§ 18.2.6)."""
    used, tables = codes_in(content)
    available = SOURCES_OF.get(source)
    if available is None:
        raise errors.validation_error("source", code="CHOICE_REQUIRED")
    for code in sorted(used | set(required)):
        need = fields.FIELDS.get(code)
        if need is None:
            raise errors.validation_error("content", code="TEMPLATE_FIELD_UNKNOWN", field_code=code)
        if need and need not in available:
            raise errors.validation_error(
                "content", code="TEMPLATE_FIELD_NOT_AVAILABLE", field_code=code
            )
    for code in sorted(tables):
        need = TABLE_SOURCES.get(code)
        if need is None:
            raise errors.validation_error("content", code="TEMPLATE_FIELD_UNKNOWN", field_code=code)
        if need not in available:
            raise errors.validation_error(
                "content", code="TEMPLATE_FIELD_NOT_AVAILABLE", field_code=code
            )
    for code in required:
        if code not in used:
            raise errors.validation_error(
                "required_fields", code="TEMPLATE_REQUIRED_NOT_USED", field_code=code
            )


def _inline(text: str, values: dict[str, str], mark: bool) -> str:
    def field(match: re.Match[str]) -> str:
        return f"\x00{match.group(1)}\x01"

    marked = FIELD.sub(field, text)
    escaped = html.escape(marked)
    escaped = BOLD.sub(r"<strong>\1</strong>", escaped)

    def value(match: re.Match[str]) -> str:
        code = match.group(1)
        found = values.get(code)
        if found:
            return html.escape(found)
        if not mark:
            return ""
        return f'<span class="missing">{{{{{html.escape(code)}}}}}</span>'

    return re.sub("\x00([a-z_.]+)\x01", value, escaped)


def _table(code: str, rows: list[dict[str, str]], language: str) -> str:
    columns = fields.TABLES[code]
    head = "".join(f"<th>{html.escape(COLUMN_LABELS[c].get(language, c))}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(row.get(c, ''))}</td>" for c in columns) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render(content: str, values: fields.Values, language: str, mark: bool = True) -> str:
    """Safe HTML of the content: every text and value is escaped. In a preview a field without
    data is marked; in the made document it stays empty (§ 18.2.1, § 18.2.4)."""
    out: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
            items.clear()

    for raw in content.splitlines():
        line = raw.rstrip()
        table = TABLE.match(line)
        if table:
            flush()
            code = table.group(1)
            out.append(_table(code, values.tables.get(code, []), language))
        elif not line.strip():
            flush()
        elif line.startswith("### ") or line.startswith("## ") or line.startswith("# "):
            flush()
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{_inline(line[level + 1 :], values.fields, mark)}</h{level}>")
        elif line.startswith("- "):
            if paragraph:
                flush()
            items.append(_inline(line[2:], values.fields, mark))
        else:
            if items:
                flush()
            paragraph.append(_inline(line, values.fields, mark))
    flush()
    return "\n".join(out)


# --- Templates -----------------------------------------------------------------------------------


def templates_of(company_id: UUID) -> QuerySet[DocumentTemplate]:
    return DocumentTemplate.objects.filter(Q(company__isnull=True) | Q(company_id=company_id))


def get(company_id: UUID, template_id: UUID) -> DocumentTemplate:
    found = templates_of(company_id).filter(pk=template_id).first()
    if found is None:
        raise errors.not_found()
    return found


@dataclass(frozen=True)
class TemplateData:
    title: str
    kind_id: UUID
    source: str
    content: str
    required_fields: list[str]
    signers: dict[str, bool]
    language: str = ""


def snapshot(t: DocumentTemplate) -> dict[str, Any]:
    return {
        "title": t.title,
        "kind": str(t.kind_id),
        "source": t.source,
        "language": t.language,
        "required_fields": list(t.required_fields),
        "signers": dict(t.signers),
        "state": t.state,
        "template_version": t.template_version,
    }


def _check(company_id: UUID, data: TemplateData) -> None:
    services.kind_of(company_id, data.kind_id)
    validate(data.content, data.source, data.required_fields)
    if set(data.signers) - {"company", "client", "counterparty"}:
        raise errors.validation_error("signers")


def create(ctx: CommandContext, company: Company, data: TemplateData) -> DocumentTemplate:
    """A template of the company, its own or copied from a starting one (§ 18.1.5)."""
    _check(company.id, data)
    t = DocumentTemplate.objects.create(
        company=company,
        title=data.title,
        kind_id=data.kind_id,
        source=data.source,
        content=data.content,
        required_fields=data.required_fields,
        signers=data.signers,
        language=data.language,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "document_template.created",
        object_type="document_template",
        object_id=t.id,
        after=snapshot(t),
    )
    return t


def locked(company_id: UUID, template_id: UUID, expected_version: int) -> DocumentTemplate:
    t = (
        DocumentTemplate.objects.select_for_update()
        .filter(company_id=company_id, pk=template_id)
        .first()
    )
    if t is None:
        raise errors.not_found()
    if t.version != expected_version:
        raise errors.version_conflict()
    return t


def change(ctx: CommandContext, t: DocumentTemplate, data: TemplateData) -> DocumentTemplate:
    """A new version of the template applies to new documents only (§ 18.5.2)."""
    _check(t.company_id, data)  # type: ignore[arg-type]
    before = snapshot(t)
    changed_content = t.content != data.content
    t.title = data.title
    t.kind_id = data.kind_id
    t.source = data.source
    t.content = data.content
    t.required_fields = data.required_fields
    t.signers = data.signers
    t.language = data.language
    after = snapshot(t)
    if before == after and not changed_content:
        raise errors.business_rule("NOTHING_CHANGED")
    t.template_version += 1
    t.version += 1
    t.save()
    ctx.audit(
        "document_template.changed",
        object_type="document_template",
        object_id=t.id,
        before={k: v for k, v in before.items() if v != after.get(k)},
        after={
            **{k: v for k, v in after.items() if v != before.get(k)},
            "template_version": t.template_version,
        },
    )
    return t


def set_archived(ctx: CommandContext, t: DocumentTemplate, archived: bool) -> DocumentTemplate:
    target = DocumentTemplate.State.ARCHIVED if archived else DocumentTemplate.State.ACTIVE
    if t.state == target:
        raise errors.business_rule("NOTHING_CHANGED")
    t.state = target
    t.version += 1
    t.save(update_fields=["state", "version", "updated_at"])
    ctx.audit(
        "document_template.archived" if archived else "document_template.restored",
        object_type="document_template",
        object_id=t.id,
        after={"state": target},
    )
    return t


# --- Making documents (§ 18.2.4) -----------------------------------------------------------------


@dataclass(frozen=True)
class Preview:
    html: str
    missing_required: list[str]
    missing_optional: list[str]
    values: fields.Values


def _author(ctx_user_company: tuple[UUID, UUID | None]) -> str:
    company_id, employment_id = ctx_user_company
    if employment_id is None:
        return ""
    return staff.employee_names(company_id, {employment_id}).get(employment_id, "")


def preview(
    company: Company,
    t: DocumentTemplate,
    source_id: UUID | None,
    *,
    employment_id: UUID | None,
    number: int | None = None,
    on: date | None = None,
    final: bool = False,
) -> Preview:
    """The result with the fields without data marked (§ 18.2.4)."""
    if t.source != DocumentTemplate.Source.NONE and source_id is None:
        raise errors.validation_error("source_id", code="FIELD_REQUIRED")
    language = t.language or "en"
    values = fields.collect(
        company,
        t.source,
        source_id,
        number=number,
        on=on or timezone.localdate(),
        author=_author((company.id, employment_id)),
        language=language,
    )
    used, _ = codes_in(t.content)
    missing = sorted(c for c in used if not values.fields.get(c) and c != "document.number")
    required = set(t.required_fields)
    return Preview(
        html=render(t.content, values, language, mark=not final),
        missing_required=[c for c in missing if c in required],
        missing_optional=[c for c in missing if c not in required],
        values=values,
    )


def make(
    ctx: CommandContext,
    company: Company,
    t: DocumentTemplate,
    source_id: UUID | None,
    *,
    title: str,
    on: date,
    act: bool = False,
) -> Document:
    """The document with the values fixed; it is linked to the objects its data came from and
    scoped by them (decision Р-11). An act of inspection is made by `inspection_act.make` only:
    it is checked there to be the only one and sent to its signer."""
    if t.state != DocumentTemplate.State.ACTIVE:
        raise errors.business_rule("TEMPLATE_ARCHIVED")
    if (t.source == DocumentTemplate.Source.INSPECTION) != act:
        raise errors.business_rule("TEMPLATE_SOURCE_MISMATCH")
    checked = preview(company, t, source_id, employment_id=ctx.actor.employment_id, on=on)
    if checked.missing_required:
        raise errors.business_rule(
            "TEMPLATE_REQUIRED_FIELDS_MISSING", fields=checked.missing_required
        )

    def rendered(number: int) -> str:
        return preview(
            company,
            t,
            source_id,
            employment_id=ctx.actor.employment_id,
            number=number,
            on=on,
            final=True,
        ).html

    d = services.create(
        ctx,
        company,
        services.NewDocument(
            title=title or t.title,
            kind_id=t.kind_id,
            document_date=on,
            file_id=None,
            file_name="",
            links=[services.Link(kind, object_id) for kind, object_id in checked.values.links],
        ),
        render=rendered,
    )
    d.template = t
    d.template_version = t.template_version
    d.source_type = t.source
    d.source_id = source_id
    d.save(update_fields=["template", "template_version", "source_type", "source_id", "updated_at"])
    return d


def remake(ctx: CommandContext, company: Company, d: Document) -> Document:
    """An unsigned document made again by the current template: a new version (§ 18.5.2)."""
    if d.template_id is None:
        raise errors.business_rule("DOCUMENT_NOT_FROM_TEMPLATE")
    t = get(company.id, d.template_id)
    checked = preview(
        company,
        t,
        d.source_id,
        employment_id=ctx.actor.employment_id,
        number=d.number,
        on=d.document_date,
        final=True,
    )
    if checked.missing_required:
        raise errors.business_rule(
            "TEMPLATE_REQUIRED_FIELDS_MISSING", fields=checked.missing_required
        )
    services.add_version(
        ctx,
        d,
        file_id=None,
        file_name="",
        valid_until=d.valid_until,
        note="template",
        content=checked.html,
    )
    d.template_version = t.template_version
    d.save(update_fields=["template_version", "updated_at"])
    return d
