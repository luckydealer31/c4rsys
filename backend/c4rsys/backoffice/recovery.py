"""Recovery of access through the support (ТЗ § 2.3.1, § 2.3.3; decision В-11.2).

* The whole procedure is switched off by the platform setting `C4R_SUPPORT_RECOVERY_ENABLED`
  until the regulation of the check is ready (В-11.2). Switched off, the public form says that
  the recovery is not available yet and shows the support contact; no request is taken.
* The applicant names the account by its c4rsys ID or username and a contact he knows; a new
  email is confirmed by a code but attached only after the check. He is never told whether the
  account exists or what it holds.
* The support assesses the profile, the earlier links and the history and proposes a decision,
  asks for more information, or refuses with a reason (journaled without closed data). Ownership
  of a phone or an email alone does not replace the check: the staff decides, nothing is
  automatic.
* Two authorized staff members (`recovery.approve`) confirm the decision; its initiator does not.
  24 hours after the second confirmation the recovery takes effect: the new email is attached,
  the unavailable contacts are marked, all sessions and earlier recovery links end, the person
  gets a link to set a new cloud password, the previous contacts are told if available, and for
  72 hours the transfer of ownership and the highest rights are restricted. An urgent recovery
  without the wait needs its own basis and the same two confirmations.
"""

from datetime import datetime, timedelta
from functools import partial
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from c4rsys.accounts import mail
from c4rsys.accounts import services as accounts
from c4rsys.accounts.models import UserAccount
from c4rsys.backoffice import directory
from c4rsys.backoffice.blocking import support_contact
from c4rsys.backoffice.core import StaffContext, next_number, system_journal
from c4rsys.backoffice.models import (
    RecoveryApproval,
    RecoveryCase,
    SupportTicket,
    SystemJournalEntry,
)
from c4rsys.platform import errors

PROCESS = "support_recovery"
State = RecoveryCase.State
WAIT = timedelta(hours=24)  # § 2.3.1
APPROVALS = 2  # § 2.3.1
OPEN = (State.NEW, State.IN_REVIEW, State.INFO_REQUESTED, State.PROPOSED)


def enabled() -> bool:
    return bool(settings.C4R_SUPPORT_RECOVERY_ENABLED)


def require_enabled() -> None:
    if not enabled():
        raise errors.ApiError(
            "RECOVERY_DISABLED", status=403, params={"support": support_contact()}
        )


# --- The public request --------------------------------------------------------------------------


def submit(
    *, identifier: str, known_contact: str, new_email: str, description: str, ip: str | None
) -> RecoveryCase:
    """Run in a transaction. The account is looked up for the support; the applicant is told
    only the number of his request."""
    user = accounts.find_for_recovery(identifier)
    case = RecoveryCase.objects.create(
        number=next_number("recovery"),
        identifier=identifier,
        known_contact=known_contact,
        new_email=new_email,
        description=description,
        user=user if user is not None and user.state == UserAccount.State.ACTIVE else None,
        ip=ip,
    )
    system_journal(
        PROCESS,
        "recovery.requested",
        object_type="recovery_case",
        object_id=case.id,
        target_user_id=case.user_id,
        after={"number": case.number, "new_email": bool(new_email)},
    )
    return case


# --- The support ---------------------------------------------------------------------------------


def cases(state: str = "") -> list[RecoveryCase]:
    query = RecoveryCase.objects.select_related("user").order_by("-created_at")
    if state:
        query = query.filter(state=state)
    return list(query[:200])


def get(case_id: UUID) -> RecoveryCase:
    found = RecoveryCase.objects.select_related("user", "proposed_by").filter(pk=case_id).first()
    if found is None:
        raise errors.not_found()
    return found


def locked(case_id: UUID, expected_version: int | None) -> RecoveryCase:
    found = (
        RecoveryCase.objects.select_for_update(of=("self",))
        .select_related("user", "proposed_by")
        .filter(pk=case_id)
        .first()
    )
    if found is None:
        raise errors.not_found()
    if expected_version is not None and found.version != expected_version:
        raise errors.version_conflict()
    return found


def _bump(case: RecoveryCase, *fields: str) -> None:
    case.version += 1
    case.save(update_fields=[*fields, "version"])


def _require_open(case: RecoveryCase) -> None:
    if case.state not in OPEN:
        raise errors.business_rule("RECOVERY_CLOSED", state=case.state)


def _void_approvals(case: RecoveryCase, now: datetime) -> int:
    return RecoveryApproval.objects.filter(case=case, void_at__isnull=True).update(void_at=now)


def set_account(ctx: StaffContext, case: RecoveryCase, user: UserAccount) -> RecoveryCase:
    """The support names the account the request is about."""
    _require_open(case)
    if user.state != UserAccount.State.ACTIVE:
        raise errors.business_rule("RECOVERY_ACCOUNT_NOT_ACTIVE")
    before = case.user.public_id if case.user else None
    case.user = user
    if case.state == State.PROPOSED:
        _void_approvals(case, timezone.now())
    case.state = State.IN_REVIEW
    _bump(case, "user", "state")
    ctx.journal(
        "recovery.account_set",
        object_type="recovery_case",
        object_id=case.id,
        target_user_id=user.id,
        before={"user": before},
        after={"user": user.public_id},
    )
    return case


def set_ticket(ctx: StaffContext, case: RecoveryCase, ticket: SupportTicket) -> RecoveryCase:
    """§ 2.3.1: documents are kept in a closed ticket with limited access."""
    case.ticket = ticket
    _bump(case, "ticket")
    ctx.journal(
        "recovery.ticket_linked",
        object_type="recovery_case",
        object_id=case.id,
        ticket_id=ticket.id,
    )
    return case


def request_info(ctx: StaffContext, case: RecoveryCase, comment: str) -> RecoveryCase:
    """§ 2.3.1: with too little information more is asked (through the applicant's channel)."""
    _require_open(case)
    if case.state == State.PROPOSED:
        _void_approvals(case, timezone.now())
    case.state = State.INFO_REQUESTED
    _bump(case, "state")
    ctx.journal(
        "recovery.info_requested",
        object_type="recovery_case",
        object_id=case.id,
        target_user_id=case.user_id,
        reason=comment,
    )
    return case


def propose(
    ctx: StaffContext,
    case: RecoveryCase,
    *,
    assessment: str,
    unavailable: list[UUID],
    urgent: bool,
    urgent_basis: str,
) -> RecoveryCase:
    """The decision to recover, with the assessment of the profile, the earlier links and the
    history; a new decision makes the earlier confirmations void."""
    _require_open(case)
    if case.user is None:
        raise errors.business_rule("RECOVERY_ACCOUNT_REQUIRED")
    if case.user.state != UserAccount.State.ACTIVE:
        raise errors.business_rule("RECOVERY_ACCOUNT_NOT_ACTIVE")
    if urgent and not urgent_basis:
        raise errors.validation_error("urgent_basis", code="FIELD_REQUIRED")
    contacts = accounts.contacts_of(case.user.id)
    known = {c.id for c in contacts}
    if not set(unavailable) <= known:
        raise errors.validation_error("unavailable_contacts")
    usable_emails = [
        c
        for c in contacts
        if c.kind == "email" and c.unavailable_at is None and c.id not in set(unavailable)
    ]
    if not usable_emails and not case.new_email:
        # § 2.1.2: the account keeps a confirmed email.
        raise errors.business_rule("RECOVERY_EMAIL_REQUIRED")
    now = timezone.now()
    voided = _void_approvals(case, now)
    case.state = State.PROPOSED
    case.proposed_by = ctx.staff.member
    case.proposed_at = now
    case.assessment = assessment
    case.unavailable_contact_ids = list(unavailable)
    case.urgent = urgent
    case.urgent_basis = urgent_basis if urgent else ""
    _bump(
        case,
        "state",
        "proposed_by",
        "proposed_at",
        "assessment",
        "unavailable_contact_ids",
        "urgent",
        "urgent_basis",
    )
    ctx.journal(
        "recovery.proposed",
        object_type="recovery_case",
        object_id=case.id,
        target_user_id=case.user_id,
        reason=assessment,
        after={
            "unavailable_contacts": [str(x) for x in unavailable],
            "urgent": urgent,
            "urgent_basis": case.urgent_basis,
            "approvals_voided": voided,
        },
    )
    return case


def approve(ctx: StaffContext, case: RecoveryCase, comment: str) -> RecoveryCase:
    """A confirmation by an authorized member; the initiator does not confirm his own decision.
    The second one approves: the 24 hours start, or the urgent recovery takes effect at once."""
    ctx.staff.require("recovery.approve")
    if case.state != State.PROPOSED:
        raise errors.business_rule("RECOVERY_NOT_PROPOSED", state=case.state)
    if case.proposed_by_id == ctx.staff.member.id:
        raise errors.business_rule("RECOVERY_INITIATOR_CONFIRMS")
    current = RecoveryApproval.objects.filter(case=case, void_at__isnull=True)
    if current.filter(member=ctx.staff.member).exists():
        raise errors.business_rule("RECOVERY_ALREADY_CONFIRMED")
    now = timezone.now()
    RecoveryApproval.objects.create(
        case=case, member=ctx.staff.member, comment=comment, approved_at=now
    )
    count = current.count()
    ctx.journal(
        "recovery.confirmed",
        object_type="recovery_case",
        object_id=case.id,
        target_user_id=case.user_id,
        reason=comment,
        after={"confirmations": count, "needed": APPROVALS},
    )
    if count < APPROVALS:
        _bump(case)
        return case
    case.state = State.APPROVED
    case.approved_at = now
    case.effective_at = now if case.urgent else now + WAIT
    _bump(case, "state", "approved_at", "effective_at")
    ctx.journal(
        "recovery.approved",
        object_type="recovery_case",
        object_id=case.id,
        target_user_id=case.user_id,
        after={"effective_at": case.effective_at, "urgent": case.urgent},
    )
    if case.urgent:
        _complete(case, now, journal=ctx.journal)
    elif case.user_id is not None:
        _notify_previous(case.user_id, done=False, when=case.effective_at)
    return case


def refuse(ctx: StaffContext, case: RecoveryCase, reason: str) -> RecoveryCase:
    """A refusal with its reason; the applicant learns nothing of the account (§ 2.3.1). A
    refusal may come before the recovery took effect, also after the approval (PROPOSAL)."""
    if case.state not in (*OPEN, State.APPROVED):
        raise errors.business_rule("RECOVERY_CLOSED", state=case.state)
    now = timezone.now()
    _void_approvals(case, now)
    before = case.state
    case.state = State.REFUSED
    case.refused_at = now
    case.refused_by_user_id = ctx.staff.user.id
    case.refusal_reason = reason
    _bump(case, "state", "refused_at", "refused_by_user_id", "refusal_reason")
    ctx.journal(
        "recovery.refused",
        object_type="recovery_case",
        object_id=case.id,
        target_user_id=case.user_id,
        reason=reason,
        before={"state": before},
    )
    if case.new_email:
        # PROPOSAL: only to the address the applicant confirmed himself.
        number, email, support = case.number, case.new_email, support_contact()
        transaction.on_commit(
            lambda: mail.send_recovery_refused(email, number=number, reason=reason, support=support)
        )
    return case


def _notify_previous(user_id: UUID, *, done: bool, when: datetime | None) -> None:
    text = when.strftime("%Y-%m-%d %H:%M") if when else ""
    support = support_contact()
    for email in accounts.confirmed_emails_of(user_id):
        transaction.on_commit(
            partial(mail.send_recovery_notice, email, done=done, when=text, support=support)
        )


def _complete(case: RecoveryCase, now: datetime, *, journal: Any) -> None:
    if case.user_id is None:
        raise errors.business_rule("RECOVERY_ACCOUNT_REQUIRED")
    done = accounts.recover(
        case.user_id,
        new_email=case.new_email,
        unavailable_ids=list(case.unavailable_contact_ids),
        now=now,
    )
    case.state = State.COMPLETED
    case.completed_at = now
    _bump(case, "state", "completed_at")
    for email in done.previous_emails:
        support = support_contact()
        transaction.on_commit(
            partial(mail.send_recovery_notice, email, done=True, when="", support=support)
        )
    journal(
        "recovery.completed",
        object_type="recovery_case",
        object_id=case.id,
        target_user_id=case.user_id,
        after={
            "email": done.email,
            "new_email_attached": bool(case.new_email),
            "unavailable_marked": done.unavailable,
            "sessions_ended": done.sessions_ended,
            "restricted_until": now + accounts.RECOVERY_RESTRICTION,
        },
    )


def _process_journal(action: str, **values: Any) -> None:
    system_journal(PROCESS, action, **values)


def run(now: datetime) -> int:
    """Periodic: the approved recoveries whose 24 hours have passed take effect. A failure (the
    new email was taken meanwhile) returns the request to the review with the journaled code."""
    if not enabled():
        return 0
    done = 0
    for case_id in RecoveryCase.objects.filter(
        state=State.APPROVED, effective_at__lte=now
    ).values_list("id", flat=True):
        try:
            with transaction.atomic():
                case = locked(case_id, None)
                if case.state != State.APPROVED:
                    continue
                _complete(case, now, journal=_process_journal)
                done += 1
        except errors.ApiError as exc:
            with transaction.atomic():
                case = locked(case_id, None)
                case.state = State.IN_REVIEW
                _void_approvals(case, now)
                _bump(case, "state")
                system_journal(
                    PROCESS,
                    "recovery.failed",
                    object_type="recovery_case",
                    object_id=case.id,
                    target_user_id=case.user_id,
                    result="failed",
                    after={"code": exc.code},
                )
    return done


# --- Payloads ------------------------------------------------------------------------------------


def _member_name(user_id: UUID | None, names: dict[UUID, str]) -> str | None:
    return names.get(user_id) if user_id else None


def payload(case: RecoveryCase, *, detail: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(case.id),
        "number": case.number,
        "state": case.state,
        "identifier": case.identifier,
        "known_contact": case.known_contact,
        "new_email": case.new_email,
        "created_at": case.created_at.isoformat(),
        "user": directory.user_summary(case.user) if case.user else None,
        "urgent": case.urgent,
        "approved_at": case.approved_at.isoformat() if case.approved_at else None,
        "effective_at": case.effective_at.isoformat() if case.effective_at else None,
        "completed_at": case.completed_at.isoformat() if case.completed_at else None,
        "version": case.version,
    }
    if not detail:
        return body
    approvals = list(
        RecoveryApproval.objects.select_related("member__user")
        .filter(case=case)
        .order_by("approved_at")
    )
    entries = list(
        SystemJournalEntry.objects.filter(
            object_type="recovery_case", object_id=str(case.id)
        ).order_by("occurred_at")
    )
    names = accounts.names(
        {e.actor_user_id for e in entries if e.actor_user_id}
        | {a.member.user_id for a in approvals}
        | ({case.proposed_by.user_id} if case.proposed_by else set())
    )
    contacts = accounts.contacts_of(case.user.id) if case.user else []
    body.update(
        {
            "description": case.description,
            "ticket_id": str(case.ticket_id) if case.ticket_id else None,
            "known_contact_matches": accounts.known_contact_matches(
                case.user.id, case.known_contact
            )
            if case.user
            else None,
            "account_contacts": [
                {
                    "id": str(c.id),
                    "kind": c.kind,
                    "value": directory.mask_email(c.value)
                    if c.kind == "email"
                    else directory.mask_phone(c.value),
                    "unavailable": c.unavailable_at is not None,
                }
                for c in contacts
            ],
            "proposed_by": _member_name(
                case.proposed_by.user_id if case.proposed_by else None, names
            ),
            "proposed_by_member_id": str(case.proposed_by_id) if case.proposed_by_id else None,
            "proposed_at": case.proposed_at.isoformat() if case.proposed_at else None,
            "assessment": case.assessment,
            "unavailable_contact_ids": [str(x) for x in case.unavailable_contact_ids],
            "urgent_basis": case.urgent_basis,
            "approvals": [
                {
                    "member_id": str(a.member_id),
                    "name": names.get(a.member.user_id, ""),
                    "comment": a.comment,
                    "at": a.approved_at.isoformat(),
                    "void": a.void_at is not None,
                }
                for a in approvals
            ],
            "refusal_reason": case.refusal_reason,
            "history": [
                {
                    "at": e.occurred_at.isoformat(),
                    "action": e.action,
                    "actor": _member_name(e.actor_user_id, names),
                    "process": e.system_process or None,
                    "reason": e.reason,
                }
                for e in entries
            ],
        }
    )
    return body
