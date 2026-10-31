"""Registry of company documents: upload, versions, links, term of validity and archive (ТЗ
§ 18.1, § 18.5; customer's decisions Р-5, Р-7, Р-11 of 29.10.2026).

* The number is the next one inside the company (decision Р-5).
* The scope is the whole company or chosen locations; by default the location of the linked
  operation or vehicle, the whole company without such links (decision Р-11). Links never widen
  it (§ 18.5.4).
* A lasting warning is active from `warn_days` before `valid_until` until the document gets a new
  version with a new term or is archived; reading it does not remove it (§ 18.1.13–18.1.14).
  Until the notification centre comes, it is shown in the registry and on the linked vehicle
  (decision Р-7).
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import Company
from c4rsys.documents.models import (
    Document,
    DocumentKind,
    DocumentLink,
    DocumentLocation,
    DocumentVersion,
)
from c4rsys.files import services as files
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number

ObjectType = DocumentLink.ObjectType
STARTING_KINDS = ("contract", "act", "settlement", "purchase", "internal", "inspection_act")


# --- Kinds (§ 18.1.11) ---------------------------------------------------------------------------


def kinds_of(company_id: UUID) -> QuerySet[DocumentKind]:
    return DocumentKind.objects.filter(Q(company__isnull=True) | Q(company_id=company_id)).order_by(
        "created_at", "code", "name"
    )


def add_kind(ctx: CommandContext, company: Company, name: str) -> DocumentKind:
    kind = DocumentKind.objects.create(company=company, name=name)
    ctx.audit(
        "document_kind.created",
        object_type="document_kind",
        object_id=kind.id,
        after={"name": name},
    )
    return kind


def kind_of(company_id: UUID, kind_id: UUID) -> DocumentKind:
    kind = kinds_of(company_id).filter(pk=kind_id).first()
    if kind is None:
        raise errors.validation_error("kind_id", code="DOCUMENT_KIND_UNKNOWN")
    return kind


# --- Links (§ 18.1.9) ----------------------------------------------------------------------------


def _getters() -> dict[str, Callable[[UUID, UUID], Any]]:
    from c4rsys.delivery import services as deliveries
    from c4rsys.fleet import services as fleet
    from c4rsys.inspections import services as inspections
    from c4rsys.orders import services as orders
    from c4rsys.parties import counterparties
    from c4rsys.parties import services as parties
    from c4rsys.rentals import rentals
    from c4rsys.service import operations

    return {
        ObjectType.CLIENT: parties.get_client,
        ObjectType.COUNTERPARTY: counterparties.get,
        ObjectType.VEHICLE: fleet.get_card,
        ObjectType.ORDER: orders.get_order,
        ObjectType.RENTAL: rentals.get_rental,
        ObjectType.SERVICE_OPERATION: operations.get_operation,
        ObjectType.DELIVERY: deliveries.get,
        ObjectType.INSPECTION: inspections.get,
    }


def resolve(company_id: UUID, object_type: str, object_id: UUID) -> Any:
    """The linked object of this company, or a validation error."""
    getter = _getters().get(object_type)
    if getter is None:
        raise errors.validation_error("links", code="DOCUMENT_LINK_TYPE_UNKNOWN")
    try:
        return getter(company_id, object_id)
    except errors.ApiError as exc:
        raise errors.validation_error("links", code="DOCUMENT_LINK_UNKNOWN") from exc


def _place_of(object_type: str, obj: Any) -> UUID | None:
    """The location of a linked operation or vehicle, for the default scope (decision Р-11)."""
    places: dict[str, str] = {
        ObjectType.RENTAL: "issue_location_id",
        ObjectType.SERVICE_OPERATION: "location_id",
        ObjectType.DELIVERY: "from_location_id",
        ObjectType.VEHICLE: "accounting_location_id",
    }
    attribute = places.get(object_type)
    return getattr(obj, attribute, None) if attribute else None


@dataclass(frozen=True)
class Link:
    object_type: str
    object_id: UUID


@dataclass(frozen=True)
class Scope:
    """None — by default; an empty set — the whole company; else the listed locations."""

    location_ids: frozenset[UUID] | None


def _apply_scope(d: Document, scope: Scope, links: list[Link]) -> None:
    chosen = scope.location_ids
    if chosen is None:
        places = {
            _place_of(link.object_type, resolve(d.company_id, link.object_type, link.object_id))
            for link in links
        }
        places.discard(None)
        chosen = frozenset(p for p in places if p is not None)
    for location_id in chosen:
        if not locations.location_exists(d.company_id, location_id):
            raise errors.validation_error("locations", code="LOCATION_UNKNOWN")
    d.all_locations = not chosen
    d.save(update_fields=["all_locations", "updated_at"])
    DocumentLocation.objects.filter(document=d).delete()
    DocumentLocation.objects.bulk_create(
        DocumentLocation(company_id=d.company_id, document=d, location_id=x) for x in sorted(chosen)
    )


def location_ids(d: Document) -> set[UUID]:
    return set(DocumentLocation.objects.filter(document=d).values_list("location_id", flat=True))


def links_of(d: Document) -> list[DocumentLink]:
    return list(
        DocumentLink.objects.filter(document=d, removed_at__isnull=True).order_by("created_at")
    )


def _link(ctx: CommandContext, d: Document, link: Link) -> DocumentLink:
    resolve(d.company_id, link.object_type, link.object_id)
    try:
        with transaction.atomic():
            created = DocumentLink.objects.create(
                company_id=d.company_id,
                document=d,
                object_type=link.object_type,
                object_id=link.object_id,
                created_by_user_id=ctx.actor.user_id,
            )
    except IntegrityError as exc:
        raise errors.business_rule("DOCUMENT_ALREADY_LINKED") from exc
    ctx.audit(
        "document.linked",
        object_type="document",
        object_id=d.id,
        after={"object_type": link.object_type, "object_id": str(link.object_id)},
    )
    return created


def add_link(ctx: CommandContext, d: Document, link: Link) -> DocumentLink:
    """A link is added later; it does not change the scope (§ 18.1.10, § 18.5.4)."""
    return _link(ctx, d, link)


def remove_link(ctx: CommandContext, d: Document, link_id: UUID) -> None:
    found = (
        DocumentLink.objects.select_for_update()
        .filter(document=d, pk=link_id, removed_at__isnull=True)
        .first()
    )
    if found is None:
        raise errors.not_found()
    found.removed_at = timezone.now()
    found.removed_by_user_id = ctx.actor.user_id
    found.save(update_fields=["removed_at", "removed_by_user_id"])
    ctx.audit(
        "document.unlinked",
        object_type="document",
        object_id=d.id,
        before={"object_type": found.object_type, "object_id": str(found.object_id)},
    )


# --- Documents -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class NewDocument:
    title: str
    kind_id: UUID
    document_date: date
    file_id: UUID | None
    file_name: str
    links: list[Link] = field(default_factory=list)
    scope: Scope = field(default_factory=lambda: Scope(None))
    valid_until: date | None = None
    warn_days: int | None = None
    content: str = ""


def snapshot(d: Document) -> dict[str, Any]:
    return {
        "number": d.number,
        "title": d.title,
        "kind": str(d.kind_id),
        "document_date": d.document_date,
        "all_locations": d.all_locations,
        "locations": sorted(str(x) for x in location_ids(d)),
        "valid_until": d.valid_until,
        "warn_days": d.warn_days,
        "state": d.state,
    }


def _term(valid_until: date | None, warn_days: int | None) -> None:
    if warn_days is not None and valid_until is None:
        raise errors.validation_error("valid_until", code="FIELD_REQUIRED")


def create(
    ctx: CommandContext,
    company: Company,
    data: NewDocument,
    render: Callable[[int], str] | None = None,
) -> Document:
    """An uploaded document (§ 18.1.3) — or a document made by a template, whose content is
    rendered once its number is known (part Р2)."""
    kind_of(company.id, data.kind_id)
    _term(data.valid_until, data.warn_days)
    if data.file_id is not None:
        files.usable(
            data.file_id,
            purpose=files.COMPANY_DOCUMENT,
            user_id=ctx.actor.user_id,
            company_id=company.id,
        )
    elif not data.content and render is None:
        raise errors.validation_error("file_id", code="FIELD_REQUIRED")
    d = Document.objects.create(
        company=company,
        number=next_number(company.id, "document"),
        title=data.title,
        kind_id=data.kind_id,
        document_date=data.document_date,
        author_user_id=ctx.actor.user_id,
        valid_until=data.valid_until,
        warn_days=data.warn_days,
        created_at=timezone.now(),
    )
    if data.file_id is not None:
        files.attach_to_company(data.file_id, company.id)
    DocumentVersion.objects.create(
        company=company,
        document=d,
        number=1,
        file_id=data.file_id,
        file_name=data.file_name,
        content=render(d.number) if render is not None else data.content,
        valid_until=data.valid_until,
        created_by_user_id=ctx.actor.user_id,
    )
    _apply_scope(d, data.scope, data.links)
    ctx.audit("document.created", object_type="document", object_id=d.id, after=snapshot(d))
    for link in data.links:
        _link(ctx, d, link)
    return d


def locked(company_id: UUID, document_id: UUID, expected_version: int | None) -> Document:
    d = Document.objects.select_for_update().filter(company_id=company_id, pk=document_id).first()
    if d is None:
        raise errors.not_found()
    if expected_version is not None and d.version != expected_version:
        raise errors.version_conflict()
    return d


def require_active(d: Document) -> None:
    if d.state != Document.State.ACTIVE:
        raise errors.business_rule("DOCUMENT_ARCHIVED")


@dataclass(frozen=True)
class Changes:
    title: str | None = None
    kind_id: UUID | None = None
    document_date: date | None = None
    scope: Scope | None = None
    warn_days: int | None = None
    warn_days_set: bool = False


def change(ctx: CommandContext, d: Document, changes: Changes) -> Document:
    """Details of the document; its content changes only by a new version (§ 18.3.5)."""
    require_active(d)
    before = snapshot(d)
    if changes.title is not None:
        d.title = changes.title
    if changes.kind_id is not None:
        kind_of(d.company_id, changes.kind_id)
        d.kind_id = changes.kind_id
    if changes.document_date is not None:
        d.document_date = changes.document_date
    if changes.warn_days_set:
        _term(d.valid_until, changes.warn_days)
        d.warn_days = changes.warn_days
    d.version += 1
    d.save()
    if changes.scope is not None:
        _apply_scope(d, changes.scope, [])
    after = snapshot(d)
    if before == after:
        raise errors.business_rule("NOTHING_CHANGED")
    ctx.audit(
        "document.changed",
        object_type="document",
        object_id=d.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return d


def versions_of(d: Document) -> list[DocumentVersion]:
    return list(DocumentVersion.objects.filter(document=d).order_by("-number"))


def current_version(d: Document) -> DocumentVersion | None:
    return DocumentVersion.objects.filter(document=d).order_by("-number").first()


def add_version(
    ctx: CommandContext,
    d: Document,
    *,
    file_id: UUID | None,
    file_name: str,
    valid_until: date | None,
    note: str,
    content: str = "",
) -> DocumentVersion:
    """A replacement or an extension: a new version keeps the former one; the term and its
    warning follow the new version (§ 18.1.14)."""
    require_active(d)
    if file_id is not None:
        files.usable(
            file_id,
            purpose=files.COMPANY_DOCUMENT,
            user_id=ctx.actor.user_id,
            company_id=d.company_id,
        )
        files.attach_to_company(file_id, d.company_id)
    elif not content:
        raise errors.validation_error("file_id", code="FIELD_REQUIRED")
    last = current_version(d)
    version = DocumentVersion.objects.create(
        company_id=d.company_id,
        document=d,
        number=(last.number if last else 0) + 1,
        file_id=file_id,
        file_name=file_name,
        content=content,
        valid_until=valid_until,
        note=note,
        created_by_user_id=ctx.actor.user_id,
    )
    before = {"valid_until": d.valid_until}
    d.valid_until = valid_until
    if valid_until is None:
        d.warn_days = None
    d.version += 1
    d.save(update_fields=["valid_until", "warn_days", "version", "updated_at"])
    ctx.audit(
        "document.version_added",
        object_type="document",
        object_id=d.id,
        before=before,
        after={"version": version.number, "valid_until": valid_until, "note": note},
    )
    return version


def set_client_visible(ctx: CommandContext, d: Document, visible: bool) -> Document:
    """«Show to the client» of decision Б-2."""
    if d.show_to_client == visible:
        return d
    d.show_to_client = visible
    d.version += 1
    d.save(update_fields=["show_to_client", "version", "updated_at"])
    ctx.audit(
        "document.client_visibility_changed",
        object_type="document",
        object_id=d.id,
        before={"show_to_client": not visible},
        after={"show_to_client": visible},
    )
    return d


def archive(ctx: CommandContext, d: Document, reason: str) -> Document:
    """No longer needed: with a reason; the term control and its warning end; the document,
    its links and history stay (§ 18.1.14)."""
    require_active(d)
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    d.state = Document.State.ARCHIVED
    d.archive_reason = reason
    d.archived_at = timezone.now()
    d.version += 1
    d.save(update_fields=["state", "archive_reason", "archived_at", "version", "updated_at"])
    ctx.audit(
        "document.archived",
        object_type="document",
        object_id=d.id,
        before={"state": Document.State.ACTIVE},
        after={"state": d.state},
        reason=reason,
    )
    return d


# --- Reading, scope and warnings -----------------------------------------------------------------


def documents_of(company_id: UUID) -> QuerySet[Document]:
    return Document.objects.filter(company_id=company_id)


def linked_to(company_id: UUID, object_type: str, object_id: UUID) -> QuerySet[Document]:
    ids = DocumentLink.objects.filter(
        company_id=company_id, object_type=object_type, object_id=object_id, removed_at__isnull=True
    ).values("document_id")
    return documents_of(company_id).filter(pk__in=ids)


def warning_from(d: Document) -> date | None:
    if d.state != Document.State.ACTIVE or d.valid_until is None or d.warn_days is None:
        return None
    return d.valid_until - timedelta(days=d.warn_days)


def warning(d: Document, today: date) -> str | None:
    """«expiring» from the warning date, «expired» after the term (§ 18.1.13)."""
    start = warning_from(d)
    if start is None or d.valid_until is None or today < start:
        return None
    return "expired" if today > d.valid_until else "expiring"


def with_warnings(company_id: UUID, today: date) -> list[Document]:
    found = documents_of(company_id).filter(
        state=Document.State.ACTIVE, valid_until__isnull=False, warn_days__isnull=False
    )
    return [d for d in found if warning(d, today) is not None]


def file_reader(user_id: UUID, item: Any) -> bool:
    """A document file is read by whoever may view a document holding it (§ 18.5.3); the client
    reads only the file of the version shown in the personal cabinet (Б-2)."""
    from c4rsys.companies import access
    from c4rsys.companies.models import Employment
    from c4rsys.documents.models import DocumentSigner
    from c4rsys.platform.commands import Actor

    version = DocumentVersion.objects.select_related("document").filter(file_id=item.id).first()
    scan = (
        DocumentSigner.objects.select_related("document")
        .filter(Q(paper_file_id=item.id) | Q(signature_file_id=item.id))
        .first()
    )
    holder = version or scan
    if holder is None:
        return False
    d = holder.document
    # An employee is checked first: it is cheap, the cabinet reads the client's history.
    employment = Employment.objects.filter(
        company_id=d.company_id, user_id=user_id, state=Employment.State.ACTIVE
    ).first()
    if employment is not None:
        actor = Actor(user_id=user_id, company_id=d.company_id, employment_id=employment.id)
        if can(actor, "documents.view", d, access.RightsResolver()):
            return True
    if version is None:
        return False
    from c4rsys.documents import cabinet

    return cabinet.may_read_file(user_id, d, item.id)


def journal_file_read(user_id: UUID, item: Any, kind: str) -> None:
    """§ 24.4.1: a download of a document's file through the files API, in the company's journal
    with the employee who read it (or the client from the cabinet)."""
    from c4rsys.companies.models import Employment
    from c4rsys.documents.models import DocumentSigner
    from c4rsys.platform import audit
    from c4rsys.platform.commands import Actor

    version = DocumentVersion.objects.select_related("document").filter(file_id=item.id).first()
    scan = (
        DocumentSigner.objects.select_related("document")
        .filter(Q(paper_file_id=item.id) | Q(signature_file_id=item.id))
        .first()
    )
    holder = version or scan
    if holder is None:
        return
    d = holder.document
    employment = Employment.objects.filter(
        company_id=d.company_id, user_id=user_id, state=Employment.State.ACTIVE
    ).first()
    audit.record(
        "document.file_downloaded",
        object_type="document",
        object_id=d.id,
        company_id=d.company_id,
        actor=Actor(user_id=user_id, company_id=d.company_id, employment_id=employment.id)
        if employment
        else None,
        user_id=user_id,
        after={
            "file": str(item.id),
            "version": version.number if version else None,
            "by": "employee" if employment else "client",
            "kind": kind,
        },
    )


def can(actor: Any, permission: str, d: Document, resolver: Any = None) -> bool:
    """The right in the document's scope: anywhere for the whole company, else in one of its
    locations (§ 18.5.4)."""
    from c4rsys.companies import access

    resolver = resolver or access.RightsResolver()
    if d.all_locations:
        return bool(resolver.has_permission(actor, permission, whole_company=True))
    return any(resolver.has_permission(actor, permission, location_id=x) for x in location_ids(d))


def safe_file_name(name: str, fallback: str) -> str:
    """A file name for an archive: the last part of the path without separators, `..` and
    control characters."""
    base = name.replace("\\", "/").split("/")[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip().lstrip(".")
    return base or fallback


def files_for_export(actor: Any, company_id: UUID) -> Iterator[tuple[str, bytes]]:
    """Files of the documents the employee may view, for the full export (§ 22.6.1, § 18.5.4):
    every version and the scans of paper signatures, named by the document number. The files
    are read one by one, as the archive takes them."""
    from c4rsys.companies import access
    from c4rsys.documents.models import DocumentSigner
    from c4rsys.files.models import StoredFile

    resolver = access.RightsResolver()
    for d in Document.objects.filter(company_id=company_id).order_by("number").iterator():
        if not can(actor, "documents.view", d, resolver):
            continue
        for v in d.versions.filter(file__isnull=False).order_by("number"):
            item = StoredFile.objects.filter(pk=str(v.file_id)).first()
            if item is not None:
                name = safe_file_name(v.file_name, str(item.id))
                yield f"{d.number}/v{v.number}_{name}", files.content(item)
        for signer in DocumentSigner.objects.filter(document=d, paper_file__isnull=False):
            item = StoredFile.objects.filter(pk=str(signer.paper_file_id)).first()
            if item is not None:
                yield f"{d.number}/paper_signature_{signer.id}", files.content(item)
        for signer in DocumentSigner.objects.filter(document=d, signature_file__isnull=False):
            item = StoredFile.objects.filter(pk=str(signer.signature_file_id)).first()
            if item is not None:
                yield f"{d.number}/screen_signature_{signer.id}.png", files.content(item)


def vehicle_copies(company_id: UUID, card_id: UUID) -> list[dict[str, Any]]:
    """Versions with a file of the company's active documents linked to the vehicle card: copies
    uploaded by the company (§ 8.2.7) that the confirmed owner may accept as global versions."""
    result = []
    for d in linked_to(company_id, DocumentLink.ObjectType.VEHICLE, card_id).filter(
        state=Document.State.ACTIVE
    ):
        for v in versions_of(d):
            if v.file_id is None:
                continue
            result.append(
                {
                    "document_id": str(d.id),
                    "title": d.title,
                    "version_id": str(v.id),
                    "number": v.number,
                    "file_id": str(v.file_id),
                    "file_name": v.file_name,
                    "valid_until": v.valid_until.isoformat() if v.valid_until else None,
                    "created_at": v.created_at.isoformat(),
                }
            )
    return result


def vehicle_copy(company_id: UUID, card_id: UUID, version_id: UUID) -> dict[str, Any] | None:
    return next(
        (c for c in vehicle_copies(company_id, card_id) if c["version_id"] == str(version_id)),
        None,
    )
