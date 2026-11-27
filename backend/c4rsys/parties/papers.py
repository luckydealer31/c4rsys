"""The photos of the documents a card was filled from (customer's decision of 28.11.2026): the
photo read to fill a new card is kept with the card as a file. The data of the documents stay in
their own fields (extended.py); here are only the files."""

from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.files import services as files
from c4rsys.files.models import StoredFile
from c4rsys.parties import recognition
from c4rsys.parties.models import ClientCard, Counterparty
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

PURPOSE = StoredFile.Purpose.PARTY_PAPER
MAX = 20


def parse(data: Any) -> tuple[UUID, str] | None:
    """`{file_id, kind}` of the body of a new card, or nothing."""
    if data in (None, "", {}):
        return None
    if not isinstance(data, dict):
        raise errors.validation_error("paper")
    try:
        file_id = UUID(str(data.get("file_id")))
    except ValueError as exc:
        raise errors.validation_error("paper.file_id") from exc
    kind = data.get("kind")
    if kind not in recognition.DOCUMENT_KINDS:
        raise errors.validation_error("paper.kind", code="VALUE_UNKNOWN")
    return file_id, kind


def attach(ctx: CommandContext, card: ClientCard | Counterparty, file_id: UUID, kind: str) -> None:
    """The file is the one its uploader read for this card; it belongs to the company from now."""
    files.usable(file_id, purpose=PURPOSE, user_id=ctx.actor.user_id, company_id=card.company_id)
    files.attach_to_company(file_id, card.company_id)
    kept = [p for p in card.papers if p.get("file_id") != str(file_id)]
    if len(kept) >= MAX:
        raise errors.validation_error("paper", code="TOO_MANY")
    kept.append(
        {
            "file_id": str(file_id),
            "kind": kind,
            "added_at": timezone.now().isoformat(),
            "added_by": str(ctx.actor.user_id),
        }
    )
    card.papers = kept
    card.save(update_fields=["papers", "updated_at"])
    ctx.audit(
        "card.paper_attached",
        object_type="client_card" if isinstance(card, ClientCard) else "counterparty",
        object_id=card.id,
        after={"file": str(file_id), "kind": kind},
    )


def payload(card: ClientCard | Counterparty) -> list[dict[str, Any]]:
    return [
        {"file_id": p["file_id"], "kind": p["kind"], "added_at": p["added_at"]} for p in card.papers
    ]


def file_of(card: ClientCard | Counterparty, file_id: UUID) -> StoredFile:
    """A file of the card for who may see the card: the access is checked by the caller."""
    if not any(p.get("file_id") == str(file_id) for p in card.papers):
        raise errors.not_found()
    item = files.of_company(card.company_id, file_id)
    if item is None:
        raise errors.not_found()
    return item
