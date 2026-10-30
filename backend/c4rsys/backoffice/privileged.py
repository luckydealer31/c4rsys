"""Privileged sessions of the system staff (ТЗ § 24.3.1–24.3.2, § 24.1.4; decision В-11.3).

* A session is opened for one company or one user with a reason category, a reason, a ticket if
  there is one, and the areas of data. The view is the default; finances, documents and file
  download need separate system permissions.
* A session lasts 1 hour (В-11.3); after it a new session needs a new reason.
* It only shows data: there is no command of a company behind it (§ 24.1.4). Every opened
  section and object and every download is recorded and journaled.
* After the end the top-authority director and the employees with the security right of the
  company are told; the company journal gets the moment, the unit of the staff member, the reason
  category and the viewed sections, without his person and the content of the ticket. The notice
  may be deferred only by a separate system permission and is sent once the deferral is lifted.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from c4rsys.accounts.models import UserAccount
from c4rsys.backoffice import catalog
from c4rsys.backoffice.core import Staff, StaffContext, logged_read, system_journal
from c4rsys.backoffice.models import (
    Notice,
    PrivilegedAccess,
    PrivilegedSession,
    SupportTicket,
)
from c4rsys.companies.models import Company, Employment
from c4rsys.platform import errors
from c4rsys.platform.models import AuditEvent

TTL = timedelta(hours=1)  # decision В-11.3
PROCESS = "privileged_sessions"
COMPANY_ACTION = "security.privileged_session"
EVENT = "security.privileged_session"
Target = PrivilegedSession.Target


def payload(s: PrivilegedSession, now: datetime | None = None) -> dict[str, Any]:
    moment = now or timezone.now()
    return {
        "id": str(s.id),
        "target_type": s.target_type,
        "company": {"public_id": s.company.public_id, "display_name": s.company.display_name}
        if s.company
        else None,
        "user": {
            "public_id": s.user.public_id,
            "name": " ".join(x for x in (s.user.last_name, s.user.first_name) if x),
        }
        if s.user
        else None,
        "reason_category": s.reason_category,
        "reason": s.reason,
        "ticket_number": s.ticket.number if s.ticket else None,
        "scopes": list(s.scopes),
        "started_at": s.started_at.isoformat(),
        "expires_at": s.expires_at.isoformat(),
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "end_reason": s.end_reason or None,
        "active": s.ended_at is None and s.expires_at > moment,
        "notice": s.notice,
        "notified_at": s.notified_at.isoformat() if s.notified_at else None,
        "sections": sections_of(s),
    }


def sections_of(s: PrivilegedSession) -> list[str]:
    return sorted(set(PrivilegedAccess.objects.filter(session=s).values_list("section", flat=True)))


def sessions_of(staff: Staff) -> list[PrivilegedSession]:
    return list(
        PrivilegedSession.objects.select_related("company", "user", "ticket")
        .filter(member=staff.member)
        .order_by("-started_at")[:20]
    )


def check_scopes(staff: Staff, scopes: list[str]) -> list[str]:
    chosen = sorted(set(scopes) | {"view"})
    for scope in chosen:
        if scope not in catalog.SCOPES:
            raise errors.validation_error("scopes", code="PRIVILEGED_SCOPE_UNKNOWN")
        needed = catalog.SCOPES[scope]
        if needed is not None:
            staff.require(needed)
    if "files" in chosen and "documents" not in chosen:
        # Files are the files of the documents (PROPOSAL).
        raise errors.validation_error("scopes", code="PRIVILEGED_FILES_NEED_DOCUMENTS")
    return chosen


def _end(s: PrivilegedSession, reason: str, now: datetime) -> None:
    s.ended_at = min(now, s.expires_at) if reason == PrivilegedSession.EndReason.EXPIRED else now
    s.end_reason = reason
    s.save(update_fields=["ended_at", "end_reason"])


def open_session(
    ctx: StaffContext,
    *,
    company: Company | None,
    user: UserAccount | None,
    category: str,
    reason: str,
    ticket: SupportTicket | None,
    scopes: list[str],
) -> PrivilegedSession:
    staff = ctx.staff
    if category not in catalog.SESSION_REASONS:
        raise errors.validation_error("reason_category")
    now = timezone.now()
    for found in PrivilegedSession.objects.select_for_update().filter(
        member=staff.member, ended_at__isnull=True
    ):
        if found.expires_at > now:
            raise errors.business_rule("PRIVILEGED_SESSION_OPEN")
        _end(found, PrivilegedSession.EndReason.EXPIRED, now)
        ctx.journal(
            "privileged.ended",
            object_type="privileged_session",
            object_id=found.id,
            privileged_session_id=found.id,
            after={"reason": PrivilegedSession.EndReason.EXPIRED},
        )
    s = PrivilegedSession.objects.create(
        member=staff.member,
        target_type=Target.COMPANY if company else Target.USER,
        company=company,
        user=user,
        reason_category=category,
        reason=reason,
        ticket=ticket,
        scopes=scopes,
        roles=list(staff.roles),
        unit=staff.member.unit,
        started_at=now,
        expires_at=now + TTL,
        # TODO(spec): § 24.3.2 tells the company; whether a user is told of a session for him.
        notice=Notice.PENDING if company else Notice.NONE,
    )
    ctx.journal(
        "privileged.opened",
        object_type="privileged_session",
        object_id=s.id,
        privileged_session_id=s.id,
        ticket_id=ticket.id if ticket else None,
        target_company_id=company.id if company else None,
        target_user_id=user.id if user else None,
        reason=reason,
        after={
            "reason_category": category,
            "scopes": scopes,
            "expires_at": s.expires_at,
            "ticket_number": ticket.number if ticket else None,
        },
    )
    return s


def _own(staff: Staff, session_id: UUID, *, lock: bool = False) -> PrivilegedSession:
    query = PrivilegedSession.objects.select_related("company", "user", "ticket")
    if lock:
        query = query.select_for_update(of=("self",))
    found = query.filter(pk=session_id, member=staff.member).first()
    if found is None:
        raise errors.not_found()
    return found


def get(staff: Staff, session_id: UUID) -> PrivilegedSession:
    return _own(staff, session_id)


def locked(staff: Staff, session_id: UUID) -> PrivilegedSession:
    return _own(staff, session_id, lock=True)


def close(ctx: StaffContext, s: PrivilegedSession) -> PrivilegedSession:
    if s.ended_at is not None:
        raise errors.business_rule("PRIVILEGED_SESSION_ENDED")
    now = timezone.now()
    reason = (
        PrivilegedSession.EndReason.EXPIRED
        if s.expires_at <= now
        else PrivilegedSession.EndReason.CLOSED
    )
    _end(s, reason, now)
    ctx.journal(
        "privileged.ended",
        object_type="privileged_session",
        object_id=s.id,
        privileged_session_id=s.id,
        after={"reason": reason, "sections": sections_of(s)},
    )
    if deliver_notice(s, now):
        ctx.journal(
            "privileged.notice_sent",
            object_type="privileged_session",
            object_id=s.id,
            privileged_session_id=s.id,
            target_company_id=s.company_id,
        )
    return s


def defer_notice(ctx: StaffContext, s: PrivilegedSession, reason: str) -> PrivilegedSession:
    ctx.staff.require("privileged.notice_defer")
    if s.notice != Notice.PENDING:
        raise errors.business_rule("NOTICE_NOT_PENDING", notice=s.notice)
    s.notice = Notice.DEFERRED
    s.notice_deferred_by_user_id = ctx.staff.user.id
    s.notice_deferral_reason = reason
    s.save(update_fields=["notice", "notice_deferred_by_user_id", "notice_deferral_reason"])
    ctx.journal(
        "privileged.notice_deferred",
        object_type="privileged_session",
        object_id=s.id,
        privileged_session_id=s.id,
        target_company_id=s.company_id,
        reason=reason,
    )
    return s


def release_notice(ctx: StaffContext, s: PrivilegedSession) -> PrivilegedSession:
    ctx.staff.require("privileged.notice_defer")
    if s.notice != Notice.DEFERRED:
        raise errors.business_rule("NOTICE_NOT_DEFERRED", notice=s.notice)
    s.notice = Notice.PENDING
    s.save(update_fields=["notice"])
    ctx.journal(
        "privileged.notice_released",
        object_type="privileged_session",
        object_id=s.id,
        privileged_session_id=s.id,
        target_company_id=s.company_id,
    )
    if s.ended_at is not None and deliver_notice(s, timezone.now()):
        ctx.journal(
            "privileged.notice_sent",
            object_type="privileged_session",
            object_id=s.id,
            privileged_session_id=s.id,
            target_company_id=s.company_id,
        )
    return s


# --- Access inside a session ----------------------------------------------------------------------


def require_active(staff: Staff, session_id: UUID) -> PrivilegedSession:
    s = _own(staff, session_id)
    if s.ended_at is not None:
        raise errors.business_rule("PRIVILEGED_SESSION_ENDED")
    if s.expires_at <= timezone.now():
        # The end is recorded by the periodic check or by the close; the data is not shown.
        raise errors.business_rule("PRIVILEGED_SESSION_EXPIRED")
    return s


def record(
    request: HttpRequest,
    staff: Staff,
    s: PrivilegedSession,
    section: str,
    *,
    scope: str = "view",
    object_id: str | UUID = "",
    download: bool = False,
) -> None:
    """Checks the area of the session and records the opened section, object or download in the
    session and in the system journal before the data is returned."""
    if scope not in s.scopes:
        raise errors.ApiError("PRIVILEGED_SCOPE_DENIED", status=403, params={"scope": scope})
    needed = catalog.SCOPES.get(scope)
    if needed is not None:
        staff.require(needed)  # the permission is read again at every request
    kind = PrivilegedAccess.Kind.DOWNLOAD if download else PrivilegedAccess.Kind.VIEW
    with transaction.atomic():
        PrivilegedAccess.objects.create(
            session=s, kind=kind, section=section, object_id=str(object_id)
        )
        logged_read(
            request,
            staff,
            "privileged.downloaded" if download else "privileged.viewed",
            object_type=section,
            object_id=object_id or (s.company_id or s.user_id or ""),
            privileged_session_id=s.id,
            ticket_id=s.ticket_id,
            target_company_id=s.company_id,
            target_user_id=s.user_id,
            reason=s.reason,
        )


def company_of(s: PrivilegedSession) -> Company:
    if s.company is None:
        raise errors.not_found()
    return s.company


def user_of(s: PrivilegedSession) -> UserAccount:
    if s.user is None:
        raise errors.not_found()
    return s.user


# --- The notice to the company (§ 24.3.2) ---------------------------------------------------------


def recipients(company_id: UUID) -> list[UUID]:
    """The top-authority director (§ 3.4.5) and the employees with the security right."""
    from c4rsys.notifications import services as notifications

    tops = Employment.objects.filter(
        company_id=company_id,
        state=Employment.State.ACTIVE,
        position__is_top_authority=True,
        user__isnull=False,
    ).values_list("id", flat=True)
    return sorted(
        set(tops) | set(notifications.with_permission(company_id, "company.security", None))
    )


def company_entry(s: PrivilegedSession) -> dict[str, Any]:
    """What the company sees: no person of the staff and no content of the ticket."""
    return {
        "started_at": s.started_at.isoformat(),
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "unit": s.unit,
        "roles": list(s.roles),
        "reason_category": s.reason_category,
        "sections": sections_of(s),
        "downloads": PrivilegedAccess.objects.filter(
            session=s, kind=PrivilegedAccess.Kind.DOWNLOAD
        ).count(),
    }


def deliver_notice(s: PrivilegedSession, now: datetime) -> bool:
    """Writes the company journal entry and tells the company, once the session has ended and
    the notice is not deferred. Runs in the caller's transaction."""
    from c4rsys.notifications import rules

    if s.notice != Notice.PENDING or s.ended_at is None or s.company_id is None:
        return False
    entry = company_entry(s)
    AuditEvent.objects.create(
        company_id=s.company_id,
        system_process="c4rsys_staff",
        action=COMPANY_ACTION,
        object_type="privileged_session",
        object_id=str(s.id),
        after=entry,
        reason=s.reason_category,
    )
    people = recipients(s.company_id)
    if people:
        rules.raise_event(
            s.company_id,
            EVENT,
            condition=f"{EVENT}:{s.id}",
            object_type="privileged_session",
            object_id=s.id,
            params={
                "unit": s.unit,
                "reason_category": s.reason_category,
                "sections": entry["sections"],
                "started_at": entry["started_at"],
                "ended_at": entry["ended_at"],
            },
            recipients=people,
            now=now,
            done=True,
        )
    s.notice = Notice.SENT
    s.notified_at = now
    s.save(update_fields=["notice", "notified_at"])
    return True


def run(now: datetime) -> int:
    """Periodic: ends the sessions whose hour has passed and sends the due notices."""
    changed = 0
    for session_id in PrivilegedSession.objects.filter(
        ended_at__isnull=True, expires_at__lte=now
    ).values_list("id", flat=True):
        with transaction.atomic():
            s = PrivilegedSession.objects.select_for_update().get(pk=session_id)
            if s.ended_at is not None:
                continue
            _end(s, PrivilegedSession.EndReason.EXPIRED, now)
            system_journal(
                PROCESS,
                "privileged.ended",
                object_type="privileged_session",
                object_id=s.id,
                privileged_session_id=s.id,
                after={"reason": PrivilegedSession.EndReason.EXPIRED, "sections": sections_of(s)},
            )
            changed += 1
    for session_id in PrivilegedSession.objects.filter(
        ended_at__isnull=False, notice=Notice.PENDING
    ).values_list("id", flat=True):
        with transaction.atomic():
            s = PrivilegedSession.objects.select_for_update().get(pk=session_id)
            if deliver_notice(s, now):
                system_journal(
                    PROCESS,
                    "privileged.notice_sent",
                    object_type="privileged_session",
                    object_id=s.id,
                    privileged_session_id=s.id,
                    target_company_id=s.company_id,
                )
                changed += 1
    return changed


def company_journal(company_id: UUID) -> list[dict[str, Any]]:
    """The entries of finished privileged sessions in the journal of the company."""
    return [
        {"id": str(e.id), "occurred_at": e.occurred_at.isoformat(), **(e.after or {})}
        for e in AuditEvent.objects.filter(company_id=company_id, action=COMPANY_ACTION).order_by(
            "-occurred_at"
        )[:200]
    ]
