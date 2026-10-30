"""Documents in the personal cabinet (ТЗ § 7.1.2, § 18.3.8; decision Б-2).

The client sees, of each company linked to the profile:
* the documents in which the client is a signer, any version;
* the documents linked to the client's card, orders or their rentals and service operations that
  the company marked «Show to the client».
Only active documents (PROPOSAL). The client signs or declines inside the cabinet with a
one-time code to the email of the profile, as an in-app signer (§ 18.3.9); the personal link
stays a separate way.
"""

from typing import Any
from uuid import UUID

from django.db.models import Q

from c4rsys.accounts import services as accounts
from c4rsys.documents import services, signing
from c4rsys.documents.models import Document, DocumentLink, DocumentSigner, DocumentVersion
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

Method = DocumentSigner.Method


def _scope(user_id: UUID) -> dict[UUID, dict[str, set[UUID]]]:
    """For each linked company: the client cards and the objects of their history."""
    from c4rsys.orders import services as orders
    from c4rsys.parties import links

    found: dict[UUID, dict[str, set[UUID]]] = {}
    for company_id, card_id, until in links.client_cards_of_user(user_id):
        scope = found.setdefault(company_id, {"cards": set(), "objects": set()})
        scope["cards"].add(card_id)
        scope["objects"].add(card_id)
        for order in orders.client_orders(company_id, [card_id], until):
            scope["objects"].add(order.id)
            for item in orders.client_view(order)["operations"]:
                if item["type"] in ("rental", "service"):
                    scope["objects"].add(UUID(item["id"]))
    return found


def _query(company_id: UUID, scope: dict[str, set[UUID]]) -> Any:
    signed = DocumentSigner.objects.filter(
        company_id=company_id, client_id__in=scope["cards"]
    ).values_list("document_id", flat=True)
    linked = DocumentLink.objects.filter(
        company_id=company_id, object_id__in=scope["objects"], removed_at__isnull=True
    ).values_list("document_id", flat=True)
    return Document.objects.filter(company_id=company_id, state=Document.State.ACTIVE).filter(
        Q(pk__in=signed) | Q(show_to_client=True, pk__in=linked)
    )


def documents_of(user_id: UUID) -> list[Document]:
    result: list[Document] = []
    for company_id, scope in _scope(user_id).items():
        result.extend(_query(company_id, scope).select_related("kind"))
    return sorted(result, key=lambda d: d.created_at, reverse=True)


def visible_to(user_id: UUID, d: Document) -> bool:
    scope = _scope(user_id).get(d.company_id)
    return scope is not None and _query(d.company_id, scope).filter(pk=d.id).exists()


def get(user_id: UUID, document_id: UUID) -> Document:
    d = Document.objects.select_related("kind").filter(pk=document_id).first()
    if d is None or not visible_to(user_id, d):
        raise errors.not_found()
    return d


def _my_signer(user_id: UUID, d: Document, *, lock: bool = False) -> DocumentSigner | None:
    """The client's pending signature on the last sent version, while the link is confirmed."""
    from c4rsys.parties import links

    cards = [c for company, c, until in links.client_cards_of_user(user_id) if until is None]
    query = DocumentSigner.objects.select_related("document_version").filter(
        document=d, client_id__in=cards
    )
    if lock:
        query = query.select_for_update()
    last = signing.sent_version(d)
    return query.filter(document_version=last).order_by("-created_at").first() if last else None


def _version(d: Document) -> DocumentVersion | None:
    return signing.sent_version(d) or services.current_version(d)


def payload(d: Document, user_id: UUID, *, detail: bool = False) -> dict[str, Any]:
    from c4rsys.companies import services as companies

    company = companies.get_company(d.company_id)
    version = _version(d)
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


def _signer_for_answer(ctx: CommandContext, d: Document) -> tuple[DocumentSigner, str]:
    signer = _my_signer(ctx.actor.user_id, d, lock=True)
    if signer is None:
        raise errors.business_rule("SIGNER_NOT_YOU")
    signing._require_open(d, signer)
    signing._allow(d, Method.IN_APP)
    emails = accounts.confirmed_emails_of(ctx.actor.user_id)
    if not emails:
        raise errors.business_rule("SIGNER_HAS_NO_EMAIL")
    return signer, emails[0]


def request_code(ctx: CommandContext, d: Document) -> str:
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
    ctx: CommandContext, d: Document, code: str, decision: str, comment: str
) -> errors.ApiError | None:
    """Sign or decline in the cabinet; a wrong code is counted and returned, not raised."""
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
        return failed
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
    return None
