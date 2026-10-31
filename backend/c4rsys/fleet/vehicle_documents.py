"""Global documents of a vehicle with versions (ТЗ § 8.2.3–8.2.4, § 8.2.7, § 8.3.10).

* The vehicle passport, the registration certificate, insurance policies and other changeable
  documents are kept by versions with the global vehicle: the type, the details, the date of
  issue, the term of validity where it applies and the file. A new version never overwrites the
  earlier one (§ 8.2.3–8.2.4).
* They are not public. The confirmed owner manages them; a company reads them within the
  confirmed ownership, the confirmed management or a separate grant of the owner (§ 8.2.3). The
  access follows the current confirmed relation, so after a change of the owner the company loses
  what the earlier owner gave until the new one confirms (§ 8.3.10).
* Before the owner is confirmed, a company keeps copies inside its card only — the company's own
  documents linked to the vehicle (documents module), marked as given by the company and not
  confirmed by the owner. They never replace the global version and are not passed on. After the
  confirmation the owner accepts each copy as a new version of the global document or leaves it in
  the closed history of that company; the decision and the source are kept (§ 8.2.7).
"""

from datetime import date
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.fleet import disclosure, identity
from c4rsys.fleet.models import (
    VehicleCard,
    VehicleCopyDecision,
    VehicleDocument,
    VehicleDocumentGrant,
    VehicleDocumentVersion,
    VehicleOwnership,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

PURPOSE = "vehicle_document"


def current_owner(vehicle_id: UUID) -> VehicleOwnership | None:
    return VehicleOwnership.objects.filter(
        vehicle_id__in=identity.family(identity.resolve(vehicle_id)), ended_at__isnull=True
    ).first()


def is_owner(vehicle_id: UUID, *, user_id: UUID | None, company_id: UUID | None) -> bool:
    current = current_owner(vehicle_id)
    if current is None:
        return False
    if company_id is not None:
        return current.owner_company_id == company_id
    return user_id is not None and current.owner_user_id == user_id


def company_access(card: VehicleCard) -> str | None:
    """Why the company reads the global documents now, or None."""
    current = current_owner(card.vehicle_id)
    if current is None:
        return None
    if current.owner_company_id == card.company_id:
        return "ownership"
    confirmed = disclosure.confirmed_now(card.id)
    if (
        confirmed is not None
        and card.state == VehicleCard.State.ACTIVE
        and confirmed.owner_user_id == current.owner_user_id
        and confirmed.owner_company_id == current.owner_company_id
    ):
        return "management"
    grant = VehicleDocumentGrant.objects.filter(
        vehicle_id=identity.resolve(card.vehicle_id),
        company_id=card.company_id,
        revoked_at__isnull=True,
        owner_user_id=current.owner_user_id,
        owner_company_id=current.owner_company_id,
    ).exists()
    return "grant" if grant else None


def _documents(vehicle_id: UUID) -> list[VehicleDocument]:
    return list(
        VehicleDocument.objects.filter(
            vehicle_id__in=identity.family(identity.resolve(vehicle_id))
        ).order_by("doc_type", "created_at")
    )


def _version_payload(v: VehicleDocumentVersion) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "number": v.number,
        "details": v.details,
        "issued_on": v.issued_on.isoformat() if v.issued_on else None,
        "valid_until": v.valid_until.isoformat() if v.valid_until else None,
        "has_file": v.file_id is not None,
        "source": v.source,
        "added_at": v.added_at.isoformat(),
    }


def documents_payload(vehicle_id: UUID) -> list[dict[str, Any]]:
    result = []
    for d in _documents(vehicle_id):
        versions = list(d.versions.order_by("-number"))
        result.append(
            {
                "id": str(d.id),
                "doc_type": d.doc_type,
                "title": d.title,
                "current": _version_payload(versions[0]) if versions else None,
                "versions": [_version_payload(v) for v in versions],
            }
        )
    return result


def card_payload(card: VehicleCard) -> dict[str, Any]:
    """For the company: the global documents if it has access; its own copies with their mark
    and the owner's decision (§ 8.2.7)."""
    from c4rsys.documents import services as documents

    access = company_access(card)
    decisions = {
        str(d.company_version_id): d.decision
        for d in VehicleCopyDecision.objects.filter(company_id=card.company_id)
    }
    return {
        "access": access,
        "documents": documents_payload(card.vehicle_id) if access else [],
        "copies": [
            {**c, "owner_decision": decisions.get(c["version_id"])}
            for c in documents.vehicle_copies(card.company_id, card.id)
        ],
    }


def version_file(card: VehicleCard, version_id: UUID) -> UUID:
    """The file of a global version for a company with access."""
    if company_access(card) is None:
        raise errors.not_found()
    v = VehicleDocumentVersion.objects.filter(
        pk=version_id, document__vehicle_id__in=identity.family(identity.resolve(card.vehicle_id))
    ).first()
    if v is None or v.file_id is None:
        raise errors.not_found()
    return v.file_id


def owner_version_file(vehicle_id: UUID, user_id: UUID, version_id: UUID) -> UUID:
    if not is_owner(vehicle_id, user_id=user_id, company_id=None):
        raise errors.not_found()
    v = VehicleDocumentVersion.objects.filter(
        pk=version_id, document__vehicle_id__in=identity.family(identity.resolve(vehicle_id))
    ).first()
    if v is None or v.file_id is None:
        raise errors.not_found()
    return v.file_id


# --- The owner's commands ----------------------------------------------------------------------


def _require_owner(vehicle_id: UUID, user_id: UUID | None, company_id: UUID | None) -> None:
    if not is_owner(vehicle_id, user_id=user_id, company_id=company_id):
        raise errors.not_found()


def add_version(
    ctx: CommandContext,
    vehicle_id: UUID,
    *,
    owner_user_id: UUID | None,
    owner_company_id: UUID | None,
    document_id: UUID | None,
    doc_type: str,
    title: str,
    details: str,
    issued_on: date | None,
    valid_until: date | None,
    file_id: UUID | None,
) -> VehicleDocumentVersion:
    """A new document or a new version of one; the earlier versions stay (§ 8.2.3)."""
    from c4rsys.files import services as files

    _require_owner(vehicle_id, owner_user_id, owner_company_id)
    entity = identity.resolve(vehicle_id)
    if file_id is not None:
        files.usable(file_id, purpose=PURPOSE, user_id=ctx.actor.user_id)
    if file_id is None and not details.strip():
        raise errors.validation_error("file", code="FIELD_REQUIRED")
    if issued_on and valid_until and valid_until < issued_on:
        raise errors.validation_error("valid_until", code="PERIOD_INVALID")
    if document_id is not None:
        doc = (
            VehicleDocument.objects.select_for_update()
            .filter(pk=document_id, vehicle_id__in=identity.family(entity))
            .first()
        )
        if doc is None:
            raise errors.not_found()
    else:
        if doc_type not in VehicleDocument.Type.values:
            raise errors.validation_error("doc_type", code="CHOICE_REQUIRED")
        doc = VehicleDocument.objects.create(
            vehicle_id=entity, doc_type=doc_type, title=title, created_by_user_id=ctx.actor.user_id
        )
    return _new_version(
        ctx,
        doc,
        details=details,
        issued_on=issued_on,
        valid_until=valid_until,
        file_id=file_id,
        source=VehicleDocumentVersion.Source.OWNER,
        owner_user_id=owner_user_id,
        owner_company_id=owner_company_id,
    )


def _new_version(
    ctx: CommandContext,
    doc: VehicleDocument,
    *,
    details: str,
    issued_on: date | None,
    valid_until: date | None,
    file_id: UUID | None,
    source: str,
    owner_user_id: UUID | None,
    owner_company_id: UUID | None,
    source_company_id: UUID | None = None,
    source_version_id: UUID | None = None,
) -> VehicleDocumentVersion:
    last = doc.versions.order_by("-number").first()
    version = VehicleDocumentVersion.objects.create(
        document=doc,
        number=(last.number + 1) if last else 1,
        details=details,
        issued_on=issued_on,
        valid_until=valid_until,
        file_id=file_id,
        source=source,
        source_company_id=source_company_id,
        source_version_id=source_version_id,
        owner_user_id=owner_user_id,
        owner_company_id=owner_company_id,
        added_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "vehicle.document_version_added",
        object_type="vehicle_document",
        object_id=doc.id,
        after={
            "vehicle": str(doc.vehicle_id),
            "doc_type": doc.doc_type,
            "version": version.number,
            "source": source,
            "source_company": str(source_company_id) if source_company_id else None,
        },
        company_id=owner_company_id,
    )
    return version


def owner_cards(
    vehicle_id: UUID, user_id: UUID | None, company_id: UUID | None
) -> list[VehicleCard]:
    """The cards of the companies whose management this owner confirmed (now or before)."""
    from c4rsys.fleet.models import VehicleManagementConfirmation

    query = VehicleManagementConfirmation.objects.filter(
        vehicle_id__in=identity.family(identity.resolve(vehicle_id))
    )
    query = (
        query.filter(owner_company_id=company_id)
        if company_id is not None
        else query.filter(owner_user_id=user_id)
    )
    ids = set(query.values_list("vehicle_card_id", flat=True))
    return list(VehicleCard.objects.select_related("company").filter(pk__in=ids))


def copies_for_owner(vehicle_id: UUID, user_id: UUID | None, company_id: UUID | None) -> list[Any]:
    """The copies of the companies whose management the owner confirmed, with his decisions."""
    from c4rsys.documents import services as documents

    if not is_owner(vehicle_id, user_id=user_id, company_id=company_id):
        return []
    decisions = {str(d.company_version_id): d.decision for d in VehicleCopyDecision.objects.all()}
    result = []
    for card in owner_cards(vehicle_id, user_id, company_id):
        for c in documents.vehicle_copies(card.company_id, card.id):
            result.append(
                {
                    **{k: c[k] for k in ("title", "version_id", "number", "valid_until")},
                    "company": card.company.display_name,
                    "card_id": str(card.id),
                    "decision": decisions.get(c["version_id"]),
                }
            )
    return result


def decide_copy(
    ctx: CommandContext,
    vehicle_id: UUID,
    *,
    owner_user_id: UUID | None,
    owner_company_id: UUID | None,
    card_id: UUID,
    version_id: UUID,
    decision: str,
    doc_type: str,
    document_id: UUID | None,
) -> VehicleCopyDecision:
    """§ 8.2.7: accepted as a new version of a global document, or kept in the company only."""
    from c4rsys.documents import services as documents

    _require_owner(vehicle_id, owner_user_id, owner_company_id)
    if decision not in VehicleCopyDecision.Decision.values:
        raise errors.validation_error("decision", code="CHOICE_REQUIRED")
    card = next(
        (c for c in owner_cards(vehicle_id, owner_user_id, owner_company_id) if c.id == card_id),
        None,
    )
    if card is None:
        raise errors.not_found()
    copy = documents.vehicle_copy(card.company_id, card.id, version_id)
    if copy is None:
        raise errors.not_found()
    if VehicleCopyDecision.objects.filter(company_version_id=version_id).exists():
        raise errors.business_rule("COPY_ALREADY_DECIDED")
    entity = identity.resolve(vehicle_id)
    version = None
    if decision == VehicleCopyDecision.Decision.ACCEPTED:
        if document_id is not None:
            doc = VehicleDocument.objects.filter(
                pk=document_id, vehicle_id__in=identity.family(entity)
            ).first()
            if doc is None:
                raise errors.not_found()
        else:
            if doc_type not in VehicleDocument.Type.values:
                raise errors.validation_error("doc_type", code="CHOICE_REQUIRED")
            doc = VehicleDocument.objects.create(
                vehicle_id=entity,
                doc_type=doc_type,
                title=copy["title"],
                created_by_user_id=ctx.actor.user_id,
            )
        version = _new_version(
            ctx,
            doc,
            details=copy["title"],
            issued_on=None,
            valid_until=date.fromisoformat(copy["valid_until"]) if copy["valid_until"] else None,
            file_id=UUID(copy["file_id"]),
            source=VehicleDocumentVersion.Source.COMPANY_COPY,
            owner_user_id=owner_user_id,
            owner_company_id=owner_company_id,
            source_company_id=card.company_id,
            source_version_id=version_id,
        )
    found = VehicleCopyDecision.objects.create(
        vehicle_id=entity,
        company_id=card.company_id,
        company_version_id=version_id,
        decision=decision,
        global_version=version,
        owner_user_id=owner_user_id,
        owner_company_id=owner_company_id,
        decided_by_user_id=ctx.actor.user_id,
        decided_at=timezone.now(),
    )
    ctx.audit(
        "vehicle.document_copy_decided",
        object_type="vehicle_card",
        object_id=card.id,
        after={"version": str(version_id), "decision": decision},
        company_id=card.company_id,
    )
    return found


def copy_file(vehicle_id: UUID, user_id: UUID, card_id: UUID, version_id: UUID) -> UUID:
    """The file of a company copy for the owner deciding on it."""
    from c4rsys.documents import services as documents

    if not is_owner(vehicle_id, user_id=user_id, company_id=None):
        raise errors.not_found()
    card = next((c for c in owner_cards(vehicle_id, user_id, None) if c.id == card_id), None)
    copy = documents.vehicle_copy(card.company_id, card.id, version_id) if card else None
    if copy is None:
        raise errors.not_found()
    return UUID(copy["file_id"])


def grant(
    ctx: CommandContext,
    vehicle_id: UUID,
    *,
    owner_user_id: UUID | None,
    owner_company_id: UUID | None,
    company_id: UUID,
    give: bool,
) -> None:
    """A separate access of a company to the global documents (§ 8.2.3)."""
    _require_owner(vehicle_id, owner_user_id, owner_company_id)
    entity = identity.resolve(vehicle_id)
    active = VehicleDocumentGrant.objects.filter(
        vehicle_id=entity, company_id=company_id, revoked_at__isnull=True
    ).first()
    if give and active is None:
        VehicleDocumentGrant.objects.create(
            vehicle_id=entity,
            company_id=company_id,
            owner_user_id=owner_user_id,
            owner_company_id=owner_company_id,
            granted_by_user_id=ctx.actor.user_id,
        )
    elif not give and active is not None:
        active.revoked_at = timezone.now()
        active.save(update_fields=["revoked_at"])
    ctx.audit(
        "vehicle.document_access_changed",
        object_type="vehicle",
        object_id=entity,
        after={"company": str(company_id), "granted": give},
        company_id=company_id,
    )


def file_reader(user_id: UUID, item: Any) -> bool:
    """A file of a global document: its uploader reads it (files), the owner reads it here; the
    companies read it through the card (`version_file`)."""
    version = VehicleDocumentVersion.objects.filter(file_id=item.id).first()
    if version is None:
        return False
    return is_owner(version.document.vehicle_id, user_id=user_id, company_id=None)
