"""Copies of the documents of a vehicle passed between its owner and the managing company
(customer's decisions of 12.11.2026).

* The receiver gets its own copy — the name, the series and number, the dates and the file — and
  keeps it itself: changes it, sends it to the archive or works with it on.
* The owner decides what to pass to the company whose management he confirms: all, some or
  nothing — with the answer that confirms the management, or later while it is confirmed.
* The company may pass copies of the documents it added when it ends the management.
* What was passed stays with the receiver after the management ends.

The owner is a person (documents of the personal garage, `vehicle_documents`) or a company
(documents of its own card of the vehicle, `card_documents`).
"""

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from c4rsys.fleet import card_documents, disclosure, identity, vehicle_documents
from c4rsys.fleet.models import VehicleCard
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext


@dataclass(frozen=True)
class Item:
    id: UUID
    name: str
    number: str
    valid_from: date | None
    valid_until: date | None
    file_id: UUID | None
    file_name: str


def parse_ids(value: Any) -> list[UUID]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise errors.validation_error("document_ids")
    try:
        return [UUID(str(v)) for v in value]
    except ValueError as exc:
        raise errors.validation_error("document_ids") from exc


def _owner_card(vehicle_id: UUID, company_id: UUID) -> VehicleCard | None:
    """The owner company's own card of the vehicle, if it keeps one."""
    return (
        VehicleCard.objects.filter(
            company_id=company_id,
            vehicle_id__in=identity.family(identity.resolve(vehicle_id)),
            state=VehicleCard.State.ACTIVE,
        )
        .order_by("created_at")
        .first()
    )


def owner_items(vehicle_id: UUID, *, user_id: UUID | None, company_id: UUID | None) -> list[Item]:
    """What the owner may pass: his documents that are not in the archive."""
    if company_id is not None:
        card = _owner_card(vehicle_id, company_id)
        if card is None:
            return []
        return [
            Item(d.id, d.name, d.number, d.valid_from, d.valid_until, d.file_id, d.file_name)
            for d in card_documents.active_of(card.id)
        ]
    if not vehicle_documents.is_owner(vehicle_id, user_id=user_id, company_id=None):
        return []
    return [
        Item(d.id, d.title or d.doc_type, v.details, v.issued_on, v.valid_until, v.file_id, "")
        for d, v in vehicle_documents.active_documents(vehicle_id)
    ]


def items_payload(items: list[Item]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(i.id),
            "name": i.name,
            "number": i.number,
            "valid_from": i.valid_from.isoformat() if i.valid_from else None,
            "valid_until": i.valid_until.isoformat() if i.valid_until else None,
            "has_file": i.file_id is not None,
        }
        for i in items
    ]


def _chosen(items: list[Item], ids: list[UUID]) -> list[Item]:
    by_id = {i.id: i for i in items}
    unknown = [str(i) for i in ids if i not in by_id]
    if unknown:
        raise errors.validation_error("document_ids", code="VALUE_UNKNOWN", values=unknown)
    return [by_id[i] for i in dict.fromkeys(ids)]


def to_company(
    ctx: CommandContext,
    card: VehicleCard,
    *,
    owner_user_id: UUID | None,
    owner_company_id: UUID | None,
    document_ids: list[UUID],
) -> int:
    """The owner passes copies to the company that manages the vehicle with his confirmation."""
    confirmed = disclosure.confirmed_now(card.id)
    if (
        confirmed is None
        or confirmed.owner_user_id != owner_user_id
        or confirmed.owner_company_id != owner_company_id
    ):
        raise errors.business_rule("VEHICLE_DOCUMENTS_NEED_MANAGEMENT")
    items = owner_items(card.vehicle_id, user_id=owner_user_id, company_id=owner_company_id)
    source = {"owner_company": str(owner_company_id)} if owner_company_id else {"owner": "person"}
    for item in _chosen(items, document_ids):
        card_documents.receive(
            ctx,
            card,
            name=item.name,
            number=item.number,
            valid_from=item.valid_from,
            valid_until=item.valid_until,
            file_id=item.file_id,
            file_name=item.file_name,
            source=source,
        )
    return len(document_ids)


def _recipient(card: VehicleCard) -> tuple[UUID | None, VehicleCard | None] | None:
    """The confirmed owner who would get the company's copies: a person (his id) or a company
    that keeps its own card of the vehicle (that card). None — there is nobody to pass to."""
    current = vehicle_documents.current_owner(card.vehicle_id)
    if current is None or current.owner_company_id == card.company_id:
        return None
    if current.owner_company_id is not None:
        target = _owner_card(card.vehicle_id, current.owner_company_id)
        return (None, target) if target is not None else None
    return (current.owner_user_id, None)


def recipient(card: VehicleCard) -> str | None:
    found = _recipient(card)
    if found is None:
        return None
    return "company" if found[1] is not None else "person"


def to_owner(ctx: CommandContext, card: VehicleCard, document_ids: list[UUID]) -> int:
    """The company passes copies of its documents of the vehicle to the confirmed owner."""
    found = _recipient(card)
    if found is None:
        raise errors.business_rule("VEHICLE_DOCUMENTS_NO_RECIPIENT")
    owner_user_id, target = found
    kind = "company" if target is not None else "person"
    items = [
        Item(d.id, d.name, d.number, d.valid_from, d.valid_until, d.file_id, d.file_name)
        for d in card_documents.active_of(card.id)
    ]
    chosen = _chosen(items, document_ids)
    for item in chosen:
        if target is not None:
            card_documents.receive(
                ctx,
                target,
                name=item.name,
                number=item.number,
                valid_from=item.valid_from,
                valid_until=item.valid_until,
                file_id=item.file_id,
                file_name=item.file_name,
                source={"company": str(card.company_id)},
            )
        else:
            vehicle_documents.receive_from_company(
                ctx,
                card.vehicle_id,
                owner_user_id=owner_user_id,
                owner_company_id=None,
                company_id=card.company_id,
                name=item.name,
                number=item.number,
                valid_from=item.valid_from,
                valid_until=item.valid_until,
                file_id=item.file_id,
            )
    ctx.audit(
        "vehicle_document.passed_to_owner",
        object_type="vehicle_card",
        object_id=card.id,
        after={"documents": [str(i.id) for i in chosen], "recipient": kind},
        company_id=card.company_id,
    )
    return len(chosen)
