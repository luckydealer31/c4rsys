"""Signing of a version of a document (ТЗ § 18.3, § 18.4.2; customer's decisions Р-4, Р-6 of
29.10.2026).

* Signers belong to one version. A version is sent for signing explicitly; sending a newer
  version instead of the former revokes the unused links of the former (§ 18.3.5, § 18.3.8).
  Preparing a new version alone revokes nothing.
* The state of signing is taken from the last sent version: «signed» only when every signer of
  it has signed (§ 18.3.6); a refusal is kept and does not cancel anything (§ 18.3.4).
* The company signs by the assigned employee with the right `documents.sign` and a one-time code
  to the email of their profile (§ 18.3.3, § 18.3.9).
* A client or a counterparty signs by a personal link sent to an email of their card (decision
  Р-4: email only, 7 days by default) and confirms by a code to the same address; the link opens
  that version only and is used once (§ 18.3.9–18.3.10).
* A paper signature is a scan with the signer and the date, entered by an employee; it differs
  from a signature given inside c4rsys (§ 18.3.7). Allowed ways depend on the kind (§ 18.4.2).
* On the screen (§ 10.2.2, § 18.4.2; decision В-8.3): the client reads an act of inspection on
  the employee's device, marks the consent and signs by a finger, or refuses. The image, the
  moment, the employee and the session of the device that presented it and the version are kept.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from c4rsys.accounts import mail, security
from c4rsys.accounts import otp as account_codes
from c4rsys.accounts import services as accounts
from c4rsys.companies.models import Company, Employment
from c4rsys.documents import services
from c4rsys.documents.models import (
    Document,
    DocumentSigner,
    DocumentVersion,
    KindSigning,
    SigningInvitation,
)
from c4rsys.files import services as files
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import AuditEvent

Role = DocumentSigner.Role
Method = DocumentSigner.Method
LINK_DAYS = 7  # decision Р-4
MAX_LINK_DAYS = 90
CODE_TTL = account_codes.CODE_TTL
MAX_ATTEMPTS = account_codes.MAX_ATTEMPTS
LINK_PROCESS = "signing_link"


# --- Allowed ways by kind (§ 18.4.2) -------------------------------------------------------------


def methods_of(company_id: UUID, kind_id: UUID) -> str:
    found = KindSigning.objects.filter(company_id=company_id, kind_id=kind_id).first()
    return found.methods if found else KindSigning.Methods.BOTH


def on_screen_of(company_id: UUID, kind_id: UUID) -> bool:
    found = KindSigning.objects.filter(company_id=company_id, kind_id=kind_id).first()
    return found.on_screen if found else True


def all_methods(company_id: UUID) -> dict[UUID, tuple[str, bool]]:
    return {
        x.kind_id: (x.methods, x.on_screen)
        for x in KindSigning.objects.filter(company_id=company_id)
    }


def set_methods(
    ctx: CommandContext,
    company: Company,
    kind_id: UUID,
    methods: str,
    on_screen: bool | None = None,
) -> None:
    if methods not in KindSigning.Methods.values:
        raise errors.validation_error("methods")
    services.kind_of(company.id, kind_id)
    before = {
        "methods": methods_of(company.id, kind_id),
        "on_screen": on_screen_of(company.id, kind_id),
    }
    after = {
        "methods": methods,
        "on_screen": before["on_screen"] if on_screen is None else on_screen,
    }
    if before == after:
        raise errors.business_rule("NOTHING_CHANGED")
    KindSigning.objects.update_or_create(company=company, kind_id=kind_id, defaults=after)
    ctx.audit(
        "document_kind.signing_changed",
        object_type="document_kind",
        object_id=kind_id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )


def on_screen_allowed(d: Document) -> bool:
    """Only an act of inspection is signed on the screen (§ 18.4.2), if its kind allows it."""
    return d.source_type == "inspection" and on_screen_of(d.company_id, d.kind_id)


def _allow(d: Document, method: str) -> None:
    if method == Method.ON_SCREEN:
        if not on_screen_allowed(d):
            raise errors.business_rule("SIGNING_METHOD_NOT_ALLOWED", method=method)
        return
    allowed = methods_of(d.company_id, d.kind_id)
    wanted = KindSigning.Methods.PAPER if method == Method.PAPER else KindSigning.Methods.IN_APP
    if allowed not in (KindSigning.Methods.BOTH, wanted):
        raise errors.business_rule("SIGNING_METHOD_NOT_ALLOWED", method=method)


# --- Reading -------------------------------------------------------------------------------------


def sent_version(d: Document) -> DocumentVersion | None:
    """The last version sent for signing: the one the requirements are checked by (§ 18.3.6)."""
    return (
        DocumentVersion.objects.filter(document=d, signers__isnull=False)
        .order_by("-number")
        .distinct()
        .first()
    )


def signers_of(d: Document) -> list[DocumentSigner]:
    return list(
        DocumentSigner.objects.filter(document=d)
        .select_related("document_version")
        .order_by("-document_version__number", "created_at", "id")
    )


@dataclass(frozen=True)
class Status:
    state: str  # none | pending | signed | declined
    version: int | None


def status(d: Document) -> Status:
    version = sent_version(d)
    if version is None:
        return Status("none", None)
    states = set(
        DocumentSigner.objects.filter(document_version=version).values_list("state", flat=True)
    )
    if DocumentSigner.State.DECLINED in states:
        return Status("declined", version.number)
    if states == {DocumentSigner.State.SIGNED}:
        return Status("signed", version.number)
    return Status("pending", version.number)


def is_signed(d: Document) -> bool:
    return status(d).state == "signed"


def active_invitation(signer: DocumentSigner) -> SigningInvitation | None:
    return SigningInvitation.objects.filter(
        signer=signer, state=SigningInvitation.State.ACTIVE
    ).first()


# --- Sending a version for signing (§ 18.3.3, § 18.3.5) ------------------------------------------


@dataclass(frozen=True)
class Person:
    role: str
    person_id: UUID


def _name(d: Document, person: Person) -> str:
    from c4rsys.companies import staff
    from c4rsys.parties import counterparties
    from c4rsys.parties import services as parties

    if person.role == Role.COMPANY:
        employment = Employment.objects.filter(
            company_id=d.company_id, pk=person.person_id, state=Employment.State.ACTIVE
        ).first()
        if employment is None or employment.user_id is None:
            raise errors.validation_error("signers", code="SIGNER_EMPLOYEE_UNKNOWN")
        return staff.employee_names(d.company_id, {employment.id})[employment.id]
    if person.role == Role.CLIENT:
        return parties.display_name(services.resolve(d.company_id, "client", person.person_id))
    if person.role == Role.COUNTERPARTY:
        return counterparties.display_name(
            services.resolve(d.company_id, "counterparty", person.person_id)
        )
    raise errors.validation_error("signers")


def _revoke(ctx: CommandContext, invitation: SigningInvitation, why: str) -> None:
    invitation.state = SigningInvitation.State.REVOKED
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["state", "revoked_at"])
    ctx.audit(
        "document.signing_link_revoked",
        object_type="document",
        object_id=invitation.signer.document_id,
        after={"signer": str(invitation.signer_id), "why": why},
    )


def send(ctx: CommandContext, d: Document, people: list[Person]) -> DocumentVersion:
    """The current version goes to signing with its own signers; the unused links to former
    versions are revoked (§ 18.3.5, § 18.3.8). The roles of the template must be there."""
    services.require_active(d)
    version = services.current_version(d)
    if version is None:
        raise errors.not_found()
    if DocumentSigner.objects.filter(document_version=version).exists():
        raise errors.business_rule("DOCUMENT_VERSION_ALREADY_SENT")
    if not people:
        raise errors.validation_error("signers", code="FIELD_REQUIRED")
    if len({(p.role, p.person_id) for p in people}) != len(people):
        raise errors.validation_error("signers", code="SIGNER_DUPLICATE")
    if d.template is not None:
        wanted = {role for role, needed in d.template.signers.items() if needed}
        missing = sorted(wanted - {p.role for p in people})
        if missing:
            raise errors.business_rule("SIGNER_ROLE_MISSING", roles=missing)
    for invitation in SigningInvitation.objects.select_for_update().filter(
        signer__document=d, state=SigningInvitation.State.ACTIVE
    ):
        _revoke(ctx, invitation, "replaced")
    for person in people:
        DocumentSigner.objects.create(
            company_id=d.company_id,
            document=d,
            document_version=version,
            role=person.role,
            employment_id=person.person_id if person.role == Role.COMPANY else None,
            client_id=person.person_id if person.role == Role.CLIENT else None,
            counterparty_id=person.person_id if person.role == Role.COUNTERPARTY else None,
            name=_name(d, person),
            created_by_user_id=ctx.actor.user_id,
        )
    d.version += 1
    d.save(update_fields=["version", "updated_at"])
    ctx.audit(
        "document.sent_for_signing",
        object_type="document",
        object_id=d.id,
        after={
            "version": version.number,
            "signers": [{"role": p.role, "id": str(p.person_id)} for p in people],
        },
    )
    return version


def signer_of(d: Document, signer_id: UUID) -> DocumentSigner:
    found = (
        DocumentSigner.objects.select_for_update()
        .select_related("document_version")
        .filter(document=d, pk=signer_id)
        .first()
    )
    if found is None:
        raise errors.not_found()
    return found


def _require_open(d: Document, signer: DocumentSigner) -> None:
    """Only a pending signer of the last sent version may still answer (§ 18.3.5)."""
    services.require_active(d)
    if signer.state != DocumentSigner.State.PENDING:
        raise errors.business_rule("SIGNER_ALREADY_ANSWERED")
    last = sent_version(d)
    if last is None or last.id != signer.document_version_id:
        raise errors.business_rule("SIGNING_VERSION_REPLACED")


# --- One-time codes ------------------------------------------------------------------------------


def _new_code(holder: Any, email: str, purpose: str, ip: str | None) -> None:
    now = timezone.now()
    code = account_codes.send_contact_code(email, purpose, now, ip)
    holder.code_hash = security.code_hash(code)
    holder.code_expires_at = now + CODE_TTL
    holder.code_attempts = 0
    holder.save(update_fields=["code_hash", "code_expires_at", "code_attempts"])


def _check_code(holder: Any, code: str) -> errors.ApiError | None:
    """A wrong attempt is counted and kept: the caller commits and answers with the error."""
    if not holder.code_hash or holder.code_expires_at is None:
        return errors.ApiError("CODE_EXPIRED", status=422, field="code")
    if holder.code_attempts >= MAX_ATTEMPTS:
        return errors.ApiError("CODE_ATTEMPTS_EXHAUSTED", status=422, field="code")
    if timezone.now() > holder.code_expires_at:
        return errors.ApiError("CODE_EXPIRED", status=422, field="code")
    if not security.code_matches(code, holder.code_hash):
        holder.code_attempts += 1
        holder.save(update_fields=["code_attempts"])
        left = MAX_ATTEMPTS - holder.code_attempts
        if left <= 0:
            return errors.ApiError("CODE_ATTEMPTS_EXHAUSTED", status=422, field="code")
        return errors.ApiError(
            "CODE_INVALID", status=422, field="code", params={"attempts_left": left}
        )
    holder.code_hash = ""
    holder.code_expires_at = None
    holder.save(update_fields=["code_hash", "code_expires_at"])
    return None


def _decide(
    signer: DocumentSigner,
    decision: str,
    comment: str,
    *,
    method: str,
    user_id: UUID | None,
    contact: str,
) -> None:
    if decision not in ("sign", "decline"):
        raise errors.validation_error("decision")
    signer.state = (
        DocumentSigner.State.SIGNED if decision == "sign" else DocumentSigner.State.DECLINED
    )
    signer.method = method
    signer.decided_at = timezone.now()
    signer.decided_by_user_id = user_id
    signer.confirmed_contact = contact
    signer.comment = comment
    signer.version += 1
    signer.save()


def _decision_after(signer: DocumentSigner) -> dict[str, Any]:
    return {
        "signer": str(signer.id),
        "role": signer.role,
        "version": signer.document_version.number,
        "state": signer.state,
        "method": signer.method,
        "contact": signer.confirmed_contact,
        "comment": signer.comment,
    }


# --- The company's signer inside c4rsys (§ 18.3.3, § 18.3.9) -------------------------------------


def _own_email(ctx: CommandContext, signer: DocumentSigner) -> str:
    if signer.role != Role.COMPANY or signer.employment_id != ctx.actor.employment_id:
        raise errors.business_rule("SIGNER_NOT_YOU")
    emails = accounts.confirmed_emails_of(ctx.actor.user_id)
    if not emails:
        raise errors.business_rule("SIGNER_HAS_NO_EMAIL")
    return emails[0]


def request_code(ctx: CommandContext, d: Document, signer: DocumentSigner) -> None:
    _require_open(d, signer)
    _allow(d, Method.IN_APP)
    _new_code(signer, _own_email(ctx, signer), "signing", None)
    ctx.audit(
        "document.signing_code_sent",
        object_type="document",
        object_id=d.id,
        after={"signer": str(signer.id)},
    )


def decide_in_app(
    ctx: CommandContext,
    d: Document,
    signer: DocumentSigner,
    code: str,
    decision: str,
    comment: str,
) -> errors.ApiError | None:
    """Sign or decline with the code; a wrong code is counted and returned, not raised."""
    _require_open(d, signer)
    _allow(d, Method.IN_APP)
    email = _own_email(ctx, signer)
    failed = _check_code(signer, code)
    if failed is not None:
        ctx.audit(
            "document.signing_code_rejected",
            object_type="document",
            object_id=d.id,
            after={"signer": str(signer.id), "error": failed.code},
        )
        return failed
    _decide(
        signer, decision, comment, method=Method.IN_APP, user_id=ctx.actor.user_id, contact=email
    )
    ctx.audit(
        f"document.{'signed' if decision == 'sign' else 'signing_declined'}",
        object_type="document",
        object_id=d.id,
        after=_decision_after(signer),
        reason=comment,
    )
    return None


# --- Personal link (§ 18.3.8–18.3.10) ------------------------------------------------------------


def emails_of(d: Document, signer: DocumentSigner) -> list[str]:
    """Emails of the signer's working card (§ 18.3.9; decision Р-4)."""
    if signer.role == Role.CLIENT:
        card = services.resolve(d.company_id, "client", signer.client_id)  # type: ignore[arg-type]
    elif signer.role == Role.COUNTERPARTY:
        card = services.resolve(d.company_id, "counterparty", signer.counterparty_id)  # type: ignore[arg-type]
    else:
        return []
    return [c.value for c in card.contacts.filter(kind="email").order_by("value")]


@dataclass(frozen=True)
class SentLink:
    invitation: SigningInvitation
    url: str


def invite(
    ctx: CommandContext, d: Document, signer: DocumentSigner, email: str, days: int | None
) -> SentLink:
    _require_open(d, signer)
    _allow(d, Method.LINK)
    if signer.role == Role.COMPANY:
        raise errors.business_rule("SIGNER_SIGNS_IN_APP")
    if email not in emails_of(d, signer):
        raise errors.validation_error("email", code="SIGNER_EMAIL_NOT_IN_CARD")
    term = LINK_DAYS if days is None else days
    if term < 1 or term > MAX_LINK_DAYS:
        raise errors.validation_error("days")
    current = active_invitation(signer)
    if current is not None:
        _revoke(ctx, current, "sent_again")
    token, token_hash = security.new_token()
    invitation = SigningInvitation.objects.create(
        company_id=d.company_id,
        signer=signer,
        token_hash=token_hash,
        email=email,
        expires_at=timezone.now() + timedelta(days=term),
        created_by_user_id=ctx.actor.user_id,
    )
    url = f"{settings.C4R_PUBLIC_URL.rstrip('/')}/sign/{token}"
    company = Company.objects.get(pk=d.company_id)
    title = d.title
    transaction.on_commit(lambda: mail.send_signing_link(email, company.display_name, title, url))
    ctx.audit(
        "document.signing_link_sent",
        object_type="document",
        object_id=d.id,
        after={
            "signer": str(signer.id),
            "version": signer.document_version.number,
            "email": email,
            "expires_at": invitation.expires_at,
        },
    )
    return SentLink(invitation, url)


def revoke(ctx: CommandContext, d: Document, signer: DocumentSigner) -> None:
    current = active_invitation(signer)
    if current is None:
        raise errors.business_rule("SIGNING_LINK_NOT_ACTIVE")
    _revoke(ctx, current, "revoked")


# --- Paper signature (§ 18.3.7) ------------------------------------------------------------------


def paper(
    ctx: CommandContext, d: Document, signer: DocumentSigner, file_id: UUID, signed_on: date
) -> None:
    _require_open(d, signer)
    _allow(d, Method.PAPER)
    if signed_on > timezone.localdate():
        raise errors.validation_error("signed_on", code="DATE_IN_FUTURE")
    files.usable(
        file_id, purpose=files.COMPANY_DOCUMENT, user_id=ctx.actor.user_id, company_id=d.company_id
    )
    files.attach_to_company(file_id, d.company_id)
    current = active_invitation(signer)
    if current is not None:
        _revoke(ctx, current, "paper")
    now = timezone.now()
    signer.state = DocumentSigner.State.SIGNED
    signer.method = Method.PAPER
    signer.decided_at = now
    signer.paper_file_id = file_id
    signer.paper_signed_on = signed_on
    signer.entered_by_user_id = ctx.actor.user_id
    signer.entered_at = now
    signer.version += 1
    signer.save()
    ctx.audit(
        "document.paper_signature_entered",
        object_type="document",
        object_id=d.id,
        after={**_decision_after(signer), "signed_on": signed_on},
    )


# --- On the screen of the employee's device (§ 10.2.2, § 18.4.2) ---------------------------------


def on_screen(
    ctx: CommandContext,
    d: Document,
    signer: DocumentSigner,
    *,
    version_number: int,
    decision: str,
    consent: bool,
    file_id: UUID | None,
    comment: str,
) -> None:
    """The client or the counterparty answers on the device the employee presents. The answer
    is to the version they read: a replaced version is refused (§ 10.2.2, § 18.3.5). A signature
    needs the consent and the drawn image; a refusal keeps its comment (§ 18.3.4)."""
    if decision not in ("sign", "decline"):
        raise errors.validation_error("decision")
    _require_open(d, signer)
    _allow(d, Method.ON_SCREEN)
    if signer.role == Role.COMPANY:
        raise errors.business_rule("SIGNER_SIGNS_IN_APP")
    if signer.document_version.number != version_number:
        raise errors.business_rule("SIGNING_VERSION_REPLACED")
    if decision == "sign":
        if not consent:
            raise errors.validation_error("consent", code="SIGNING_CONSENT_REQUIRED")
        if file_id is None:
            raise errors.validation_error("file_id", code="FIELD_REQUIRED")
        files.usable(
            file_id,
            purpose=files.DOCUMENT_SIGNATURE,
            user_id=ctx.actor.user_id,
            company_id=d.company_id,
        )
        files.attach_to_company(file_id, d.company_id)
    current = active_invitation(signer)
    if current is not None:
        _revoke(ctx, current, "on_screen")
    now = timezone.now()
    signer.state = (
        DocumentSigner.State.SIGNED if decision == "sign" else DocumentSigner.State.DECLINED
    )
    signer.method = Method.ON_SCREEN
    signer.decided_at = now
    signer.comment = comment
    signer.signature_file_id = file_id if decision == "sign" else None
    signer.entered_by_user_id = ctx.actor.user_id
    signer.entered_at = now
    signer.presented_session_id = ctx.actor.session_id
    signer.version += 1
    signer.save()
    ctx.audit(
        f"document.{'signed' if decision == 'sign' else 'signing_declined'}",
        object_type="document",
        object_id=d.id,
        after={
            **_decision_after(signer),
            "consent": consent if decision == "sign" else None,
            "signature_file": str(file_id) if signer.signature_file_id else None,
            "presented_by": str(ctx.actor.user_id),
            "session": str(ctx.actor.session_id) if ctx.actor.session_id else None,
        },
        reason=comment,
    )


def paper_reader(d: Document, file_id: UUID) -> bool:
    return DocumentSigner.objects.filter(document=d, paper_file_id=file_id).exists()


# --- The public page of a link (§ 18.3.10) -------------------------------------------------------


def _journal(invitation: SigningInvitation, action: str, after: dict[str, Any]) -> None:
    AuditEvent.objects.create(
        company_id=invitation.company_id,
        system_process=LINK_PROCESS,
        action=action,
        object_type="document",
        object_id=str(invitation.signer.document_id),
        after={"signer": str(invitation.signer_id), **after},
    )


def by_token(token: str, *, lock: bool = False) -> SigningInvitation:
    """An unknown token answers as not found; a used, revoked or expired link says so."""
    query = SigningInvitation.objects.select_related(
        "signer", "signer__document", "signer__document_version"
    )
    if lock:
        query = query.select_for_update(of=("self", "signer"))
    invitation = query.filter(token_hash=security.token_hash(token)).first()
    if invitation is None:
        raise errors.not_found()
    if invitation.state == SigningInvitation.State.USED:
        raise errors.business_rule("SIGNING_LINK_USED")
    if invitation.state == SigningInvitation.State.REVOKED:
        raise errors.business_rule("SIGNING_LINK_REVOKED")
    if timezone.now() > invitation.expires_at:
        raise errors.business_rule("SIGNING_LINK_EXPIRED")
    return invitation


def masked(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}"


def link_code(token: str, ip: str | None) -> SigningInvitation:
    with transaction.atomic():
        invitation = by_token(token, lock=True)
        _require_open(invitation.signer.document, invitation.signer)
        _new_code(invitation, invitation.email, "signing_link", ip)
        _journal(invitation, "document.signing_code_sent", {})
    return invitation


def link_decide(token: str, code: str, decision: str, comment: str) -> SigningInvitation:
    if decision not in ("sign", "decline"):
        raise errors.validation_error("decision")
    with transaction.atomic():
        invitation = by_token(token, lock=True)
        signer = invitation.signer
        d = signer.document
        _require_open(d, signer)
        failed = _check_code(invitation, code)
        if failed is None:
            _decide(
                signer,
                decision,
                comment,
                method=Method.LINK,
                user_id=None,
                contact=invitation.email,
            )
            invitation.state = SigningInvitation.State.USED
            invitation.save(update_fields=["state"])
            _journal(
                invitation,
                f"document.{'signed' if decision == 'sign' else 'signing_declined'}",
                _decision_after(signer),
            )
        else:
            _journal(invitation, "document.signing_code_rejected", {"error": failed.code})
    if failed is not None:
        raise failed
    return invitation


# --- Who may be assigned (§ 18.3.3) --------------------------------------------------------------


def company_candidates(d: Document) -> list[tuple[UUID, str]]:
    """Active employees with the right to sign in the document's scope."""
    from c4rsys.companies import access, staff
    from c4rsys.platform.commands import Actor

    resolver = access.RightsResolver()
    found = [
        e.id
        for e in Employment.objects.filter(
            company_id=d.company_id, state=Employment.State.ACTIVE, user__isnull=False
        )
        if services.can(
            Actor(user_id=e.user_id or e.id, company_id=d.company_id, employment_id=e.id),
            "documents.sign",
            d,
            resolver,
        )
    ]
    names = staff.employee_names(d.company_id, set(found))
    return sorted(((x, names[x]) for x in found), key=lambda pair: pair[1])


def party_candidates(d: Document) -> list[tuple[str, UUID, str]]:
    """The linked client cards and counterparties (§ 18.3.3)."""
    from c4rsys.parties import counterparties
    from c4rsys.parties import services as parties

    result = []
    for link in services.links_of(d):
        if link.object_type == Role.CLIENT:
            card = services.resolve(d.company_id, link.object_type, link.object_id)
            result.append((Role.CLIENT.value, link.object_id, parties.display_name(card)))
        elif link.object_type == Role.COUNTERPARTY:
            card = services.resolve(d.company_id, link.object_type, link.object_id)
            result.append(
                (Role.COUNTERPARTY.value, link.object_id, counterparties.display_name(card))
            )
    return result
