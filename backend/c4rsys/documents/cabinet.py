"""Documents in the personal cabinet (ТЗ § 7.1.2, § 18.3.8; decision Б-2).

The client sees, of each company linked to the profile:
* the documents in which the client is a signer, any version;
* the documents linked to the client's card, orders or their rentals and service operations that
  the company marked «Show to the client».
Only active documents (PROPOSAL). The client signs or declines inside the cabinet with a
one-time code to the email of the profile, as an in-app signer (§ 18.3.9); the personal link
stays a separate way.

After a break of the link only what was shared before it stays (§ 6.6.3): documents, links,
signers and versions created after the break are not shown. The orders from before the break
keep their current state (decision of 30.10.2026).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet

from c4rsys.accounts import services as accounts
from c4rsys.documents import signing
from c4rsys.documents.models import Document, DocumentLink, DocumentSigner, DocumentVersion
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

Method = DocumentSigner.Method


@dataclass
class Share:
    """Client cards of one company shared until the same moment (None: the link is confirmed)
    and the objects of their history."""

    until: datetime | None
    cards: set[UUID] = field(default_factory=set)
    objects: set[UUID] = field(default_factory=set)


def _cards(user_id: UUID, company_id: UUID | None = None) -> dict[UUID, dict[UUID, Any]]:
    """{company: {card: end of the shared history}}; a card linked again after a break is
    shared without an end."""
    from c4rsys.parties import links

    found: dict[UUID, dict[UUID, Any]] = {}
    for company, card, until in links.client_cards_of_user(user_id):
        if company_id is not None and company != company_id:
            continue
        cards = found.setdefault(company, {})
        if card not in cards:
            cards[card] = until
        elif cards[card] is not None:
            cards[card] = None if until is None else max(cards[card], until)
    return found


def _shares(cards: dict[UUID, Any], company_id: UUID, *, objects: bool = True) -> list[Share]:
    from c4rsys.orders import services as orders

    by_until: dict[Any, Share] = {}
    for card, until in cards.items():
        share = by_until.setdefault(until, Share(until))
        share.cards.add(card)
        share.objects.add(card)
        if not objects:
            continue
        for order in orders.client_orders(company_id, [card], until):
            share.objects.add(order.id)
            for item in orders.client_view(order)["operations"]:
                if item["type"] in ("rental", "service"):
                    share.objects.add(UUID(item["id"]))
    return list(by_until.values())


def _scope(user_id: UUID) -> dict[UUID, list[Share]]:
    """For each linked company: the client cards and the objects of their history."""
    return {company: _shares(cards, company) for company, cards in _cards(user_id).items()}


def _signers(company_id: UUID, share: Share) -> QuerySet[DocumentSigner]:
    found = DocumentSigner.objects.filter(company_id=company_id, client_id__in=share.cards)
    return found if share.until is None else found.filter(created_at__lte=share.until)


def _query(company_id: UUID, shares: list[Share]) -> QuerySet[Document]:
    condition: Q | None = None
    for share in shares:
        linked = DocumentLink.objects.filter(
            company_id=company_id, object_id__in=share.objects, removed_at__isnull=True
        )
        if share.until is not None:
            linked = linked.filter(created_at__lte=share.until)
        part = Q(pk__in=_signers(company_id, share).values("document_id")) | Q(
            show_to_client=True, pk__in=linked.values("document_id")
        )
        if share.until is not None:
            part &= Q(created_at__lte=share.until)
        condition = part if condition is None else condition | part
    found = Document.objects.filter(company_id=company_id, state=Document.State.ACTIVE)
    return found.filter(condition) if condition is not None else found.none()


def documents_of(user_id: UUID) -> list[Document]:
    result: list[Document] = []
    for company_id, shares in _scope(user_id).items():
        result.extend(_query(company_id, shares).select_related("kind"))
    return sorted(result, key=lambda d: d.created_at, reverse=True)


def visible_to(user_id: UUID, d: Document) -> bool:
    """Cheap checks first: a signer or a link to the card itself needs no orders of the
    history; only a shown document linked to an order or an operation reads them."""
    if d.state != Document.State.ACTIVE:
        return False
    cards = _cards(user_id, d.company_id).get(d.company_id)
    if not cards:
        return False
    plain = _shares(cards, d.company_id, objects=False)
    if _query(d.company_id, plain).filter(pk=d.id).exists():
        return True
    if not d.show_to_client:
        return False
    return _query(d.company_id, _shares(cards, d.company_id)).filter(pk=d.id).exists()


def get(user_id: UUID, document_id: UUID, *, lock: bool = False) -> Document:
    query = Document.objects.select_related("kind").filter(pk=document_id)
    if lock:
        query = query.select_for_update(of=("self",))
    d = query.first()
    if d is None or not visible_to(user_id, d):
        raise errors.not_found()
    return d


def version_for(user_id: UUID, d: Document) -> DocumentVersion | None:
    """The version shown to the client: the last one the client is a signer of (§ 18.3.5:
    signers sign a concrete version). A document only shown to the client shows the last sent
    version, else the last one. After a break only versions from before it.
    TODO(spec): § 18.5 does not say which version of a document marked «Show to the client»
    the client sees (the last sent, the last one, or only a signed one); a newer draft prepared
    after sending is not shown (§ 18.3.5)."""
    cards = _cards(user_id, d.company_id).get(d.company_id)
    if not cards:
        return None
    mine: QuerySet[DocumentSigner] | None = None
    for share in _shares(cards, d.company_id, objects=False):
        part = _signers(d.company_id, share).filter(document=d)
        mine = part if mine is None else mine | part
    if mine is not None:
        signed = (
            DocumentVersion.objects.filter(pk__in=mine.values("document_version_id"))
            .order_by("-number")
            .first()
        )
        if signed is not None:
            return signed
    if not d.show_to_client:
        return None
    untils = list(cards.values())
    versions = DocumentVersion.objects.filter(document=d)
    if None not in untils:
        versions = versions.filter(created_at__lte=max(untils))
    sent = versions.filter(signers__isnull=False).order_by("-number").distinct().first()
    return sent or versions.order_by("-number").first()


def _my_signer(user_id: UUID, d: Document, *, lock: bool = False) -> DocumentSigner | None:
    """The client's pending signature on the last sent version, while the link is confirmed."""
    cards = _cards(user_id, d.company_id).get(d.company_id) or {}
    confirmed = [card for card, until in cards.items() if until is None]
    query = DocumentSigner.objects.select_related("document_version").filter(
        document=d, client_id__in=confirmed
    )
    if lock:
        query = query.select_for_update(of=("self",))
    last = signing.sent_version(d)
    return query.filter(document_version=last).order_by("-created_at").first() if last else None


def payload(d: Document, user_id: UUID, *, detail: bool = False) -> dict[str, Any]:
    from c4rsys.companies import services as companies

    company = companies.get_company(d.company_id)
    version = version_for(user_id, d)
    signer = _my_signer(user_id, d)
    body: dict[str, Any] = {
        "id": str(d.id),
        "company": {
            "public_id": company.public_id if company else "",
            "display_name": company.display_name if company else "",
        },
        "number": d.number,
        "title": d.title,
        # A system kind has no name of its own: the interface names it by the code.
        "kind": d.kind.name or "",
        "kind_code": d.kind.code or "",
        "document_date": d.document_date.isoformat(),
        "version_number": version.number if version else None,
        "signer": None
        if signer is None
        else {
            "id": str(signer.id),
            "state": signer.state,
            "name": signer.name,
            "decided_at": signer.decided_at.isoformat() if signer.decided_at else None,
        },
    }
    if detail:
        body["content"] = version.content if version else ""
        body["has_file"] = bool(version and version.file_id)
        body["file_id"] = str(version.file_id) if version and version.file_id else None
        body["file_name"] = version.file_name if version else ""
    return body


def file_of(user_id: UUID, document_id: UUID) -> tuple[Any, str]:
    """The file of the version shown to the client, with its name; nothing of other versions."""
    from c4rsys.files import services as files

    d = get(user_id, document_id)
    version = version_for(user_id, d)
    item = files.of_company(d.company_id, version.file_id) if version and version.file_id else None
    if version is None or item is None:
        raise errors.not_found()
    return item, version.file_name


def journal_client_read(user_id: UUID, document_id: UUID, item: Any) -> None:
    """§ 24.4.1: the client's view of a document given to him, in the company's journal."""
    from c4rsys.platform import audit

    d = get(user_id, document_id)
    audit.record(
        "document.file_viewed",
        object_type="document",
        object_id=d.id,
        company_id=d.company_id,
        user_id=user_id,
        after={"by": "client", "file": str(item.id)},
    )


def may_read_file(user_id: UUID, d: Document, file_id: UUID) -> bool:
    """The file is the one of the version shown to the client in the cabinet (Б-2)."""
    if not visible_to(user_id, d):
        return False
    version = version_for(user_id, d)
    return version is not None and version.file_id == file_id


def _signer_for_answer(ctx: CommandContext, d: Document) -> tuple[DocumentSigner, str]:
    signer = _my_signer(ctx.actor.user_id, d, lock=True)
    if signer is None:
        raise errors.business_rule("CABINET_NOT_SIGNER")
    signing._require_open(d, signer)
    signing._allow(d, Method.IN_APP)
    emails = accounts.confirmed_emails_of(ctx.actor.user_id)
    if not emails:
        raise errors.business_rule("SIGNER_HAS_NO_EMAIL")
    return signer, emails[0]


def request_code(ctx: CommandContext, document_id: UUID) -> str:
    """The document is locked and read again inside the command (§ 18.3.9)."""
    d = get(ctx.actor.user_id, document_id, lock=True)
    signer, email = _signer_for_answer(ctx, d)
    signing._new_code(signer, email, "signing", None)
    ctx.audit(
        "document.signing_code_sent",
        object_type="document",
        object_id=d.id,
        after={"signer": str(signer.id), "cabinet": True},
        company_id=d.company_id,
    )
    return signing.masked(email)


def decide(
    ctx: CommandContext, document_id: UUID, code: str, decision: str, comment: str
) -> tuple[Document, errors.ApiError | None]:
    """Sign or decline in the cabinet; a wrong code is counted and returned, not raised. The
    document is locked and read again inside the command."""
    d = get(ctx.actor.user_id, document_id, lock=True)
    signer, email = _signer_for_answer(ctx, d)
    failed = signing._check_code(signer, code)
    if failed is not None:
        ctx.audit(
            "document.signing_code_rejected",
            object_type="document",
            object_id=d.id,
            after={"signer": str(signer.id), "error": failed.code},
            company_id=d.company_id,
        )
        return d, failed
    signing._decide(
        signer, decision, comment, method=Method.IN_APP, user_id=ctx.actor.user_id, contact=email
    )
    ctx.audit(
        f"document.{'signed' if decision == 'sign' else 'signing_declined'}",
        object_type="document",
        object_id=d.id,
        after={**signing._decision_after(signer), "cabinet": True},
        reason=comment,
        company_id=d.company_id,
    )
    return d, None
