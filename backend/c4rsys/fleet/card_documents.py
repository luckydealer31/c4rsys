"""Documents of a vehicle kept by the company in its card (customer's decision of 12.11.2026).

The company names the document itself (a vehicle passport is called differently in different
countries), gives its series and number, the start and the end of its term — either, both or
none — and may attach a JPEG or PDF file. A document is added, changed and sent to the archive.
These are the company's own records: the owner's global documents (§ 8.2.3) are kept apart.

TODO(spec): passing these documents between the owner and the managing company at the start and
the end of the management (the customer described it on 12.11.2026) is not designed yet.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.files import services as files
from c4rsys.files.models import StoredFile
from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import VehicleCard, VehicleCardDocument
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

FILE_TYPES = ("image/jpeg", "application/pdf")


@dataclass(frozen=True)
class DocumentData:
    name: str
    series: str
    number: str
    valid_from: date | None
    valid_until: date | None
    file_id: UUID | None
    file_name: str
    # On a change: no new file is given and the attached one stays.
    keep_file: bool = False


def payload(d: VehicleCardDocument) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "version": d.version,
        "name": d.name,
        "series": d.series,
        "number": d.number,
        "valid_from": d.valid_from.isoformat() if d.valid_from else None,
        "valid_until": d.valid_until.isoformat() if d.valid_until else None,
        "has_file": d.file_id is not None,
        "file_name": d.file_name,
        "state": d.state,
    }


def card_payload(card: VehicleCard) -> list[dict[str, Any]]:
    """The documents of the card: the active ones first, by the time they were added."""
    items = VehicleCardDocument.objects.filter(vehicle_card_id=card.id).order_by("created_at")
    return [payload(d) for d in sorted(items, key=lambda d: d.state != "active")]


def _snapshot(d: VehicleCardDocument) -> dict[str, Any]:
    return {
        "name": d.name,
        "series": d.series,
        "number": d.number,
        "valid_from": d.valid_from,
        "valid_until": d.valid_until,
        "file_id": str(d.file_id) if d.file_id else None,
        "state": d.state,
    }


def _card(company_id: UUID, card_id: UUID) -> VehicleCard:
    card = fleet.lock_card(company_id, card_id)
    fleet.require_open_for_new(card)
    if card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_ARCHIVED")
    return card


def _check(ctx: CommandContext, company_id: UUID, data: DocumentData, kept: UUID | None) -> None:
    if data.valid_from and data.valid_until and data.valid_until < data.valid_from:
        raise errors.validation_error("valid_until", code="PERIOD_INVALID")
    if data.file_id is None or data.file_id == kept:
        return
    files.usable(
        data.file_id,
        purpose=files.VEHICLE_DOCUMENT,
        user_id=ctx.actor.user_id,
        company_id=company_id,
    )
    item = StoredFile.objects.get(pk=data.file_id)
    if item.content_type not in FILE_TYPES:
        raise errors.validation_error("file", code="VEHICLE_DOCUMENT_FILE_TYPE")
    files.attach_to_company(data.file_id, company_id)


def _locked(card: VehicleCard, document_id: UUID, expected_version: int) -> VehicleCardDocument:
    d = (
        VehicleCardDocument.objects.select_for_update()
        .filter(vehicle_card_id=card.id, pk=document_id)
        .first()
    )
    if d is None:
        raise errors.not_found()
    if d.version != expected_version:
        raise errors.version_conflict()
    if d.state != VehicleCardDocument.State.ACTIVE:
        raise errors.business_rule("VEHICLE_DOCUMENT_ARCHIVED")
    return d


def add(
    ctx: CommandContext, company_id: UUID, card_id: UUID, data: DocumentData
) -> VehicleCardDocument:
    card = _card(company_id, card_id)
    _check(ctx, company_id, data, None)
    d = VehicleCardDocument.objects.create(
        company_id=company_id,
        vehicle_card_id=card.id,
        name=data.name,
        series=data.series,
        number=data.number,
        valid_from=data.valid_from,
        valid_until=data.valid_until,
        file_id=data.file_id,
        file_name=data.file_name if data.file_id else "",
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "vehicle_document.added",
        object_type="vehicle_card",
        object_id=card.id,
        after={"document": str(d.id), **_snapshot(d)},
    )
    return d


def change(
    ctx: CommandContext,
    company_id: UUID,
    card_id: UUID,
    document_id: UUID,
    data: DocumentData,
    expected_version: int,
) -> VehicleCardDocument:
    card = _card(company_id, card_id)
    d = _locked(card, document_id, expected_version)
    _check(ctx, company_id, data, d.file_id)
    before = _snapshot(d)
    d.name = data.name
    d.series = data.series
    d.number = data.number
    d.valid_from = data.valid_from
    d.valid_until = data.valid_until
    if data.file_id != d.file_id and not (data.keep_file and data.file_id is None):
        d.file_id = data.file_id
        d.file_name = data.file_name if data.file_id else ""
    d.version += 1
    d.save()
    ctx.audit(
        "vehicle_document.changed",
        object_type="vehicle_card",
        object_id=card.id,
        before={"document": str(d.id), **before},
        after={"document": str(d.id), **_snapshot(d)},
    )
    return d


def archive(
    ctx: CommandContext, company_id: UUID, card_id: UUID, document_id: UUID, expected_version: int
) -> VehicleCardDocument:
    card = _card(company_id, card_id)
    d = _locked(card, document_id, expected_version)
    d.state = VehicleCardDocument.State.ARCHIVED
    d.archived_at = timezone.now()
    d.version += 1
    d.save()
    ctx.audit(
        "vehicle_document.archived",
        object_type="vehicle_card",
        object_id=card.id,
        before={"document": str(d.id), "state": "active"},
        after={"document": str(d.id), "state": d.state, "name": d.name},
    )
    return d


def file_of(card: VehicleCard, document_id: UUID) -> StoredFile:
    d = VehicleCardDocument.objects.filter(vehicle_card_id=card.id, pk=document_id).first()
    if d is None or d.file_id is None:
        raise errors.not_found()
    item = StoredFile.objects.filter(pk=d.file_id, state__in=files.AVAILABLE).first()
    if item is None:
        raise errors.not_found()
    return item
