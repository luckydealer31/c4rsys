"""System blocks of users and companies (ТЗ § 24.2.1–24.2.4).

* A block keeps the category, the internal comment, the initiator and the moment; it has an end
  moment or lasts until a manual restoration. Its end by the term is recorded by the periodic
  check, which also applies the effects of the end.
* A user block ends all his sessions at once and refuses new logins; the companies and other
  employees stay (§ 24.2.2).
* A company block stops the access of its employees to it, its commands, automatic actions and
  external signing links (c4rsys.platform.suspension); their accounts and other companies stay.
  Data, history and unfinished operations are not changed.
* The affected user or the owners get the reason that may be disclosed and the support contact;
  the notice may be deferred only by a separate system permission.
* The end of a company block applies § 24.2.3 through the modules (`suspension.resumed`). It
  does not activate a deactivated company (§ 24.2.4): deactivation is a separate state.
"""

from datetime import datetime
from functools import partial
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from c4rsys.accounts import mail
from c4rsys.accounts import services as accounts
from c4rsys.accounts.models import UserAccount, UserSession
from c4rsys.backoffice import catalog, directory
from c4rsys.backoffice.core import StaffContext, system_journal
from c4rsys.backoffice.models import Notice, SystemBlock
from c4rsys.companies import services as companies
from c4rsys.companies.models import Company
from c4rsys.platform import errors, suspension

PROCESS = "system_blocks"
Target = SystemBlock.Target


def support_contact() -> str:
    # TODO(spec): the support contact of c4rsys shown in a block notice; set by the server.
    return str(settings.C4R_SUPPORT_CONTACT)


class Provider:
    """What the other modules learn about the blocks (c4rsys.platform.suspension). A block is in
    force until its end is recorded."""

    def company_block(self, company_id: UUID) -> dict[str, Any] | None:
        found = SystemBlock.objects.filter(company_id=company_id, ended_at__isnull=True).exists()
        return {"support": support_contact()} if found else None

    def blocked_company_ids(self) -> set[UUID]:
        return set(
            SystemBlock.objects.filter(company__isnull=False, ended_at__isnull=True).values_list(
                "company_id", flat=True
            )
        )

    def user_block(self, user_id: UUID) -> dict[str, Any] | None:
        found = SystemBlock.objects.filter(user_id=user_id, ended_at__isnull=True).first()
        if found is None:
            return None
        info: dict[str, Any] = {"support": support_contact()}
        if found.notice != Notice.DEFERRED:
            info["category"] = found.category
            info["reason"] = found.disclosed_reason
        return info


def blocks(active_only: bool) -> list[SystemBlock]:
    query = SystemBlock.objects.select_related("company", "user").order_by("-started_at")
    if active_only:
        query = query.filter(ended_at__isnull=True)
    return list(query[:200])


def _lock_target(company: Company | None, user: UserAccount | None) -> None:
    """The row of the target is locked: commands in flight of the company and a login in flight
    of the user finish first, the ones after see the block."""
    if company is not None:
        companies.lock_company(company.id)
    if user is not None:
        accounts.lock_user(user.id)


def block(
    ctx: StaffContext,
    *,
    company: Company | None,
    user: UserAccount | None,
    category: str,
    comment: str,
    disclosed_reason: str,
    ends_at: datetime | None,
    defer_reason: str,
) -> SystemBlock:
    staff = ctx.staff
    if category not in catalog.BLOCK_CATEGORIES:
        raise errors.validation_error("category")
    now = timezone.now()
    if ends_at is not None and ends_at <= now:
        raise errors.validation_error("ends_at", code="BLOCK_END_IN_PAST")
    if defer_reason:
        staff.require("block.notice_defer")
    if user is not None and user.id == staff.user.id:
        raise errors.business_rule("BLOCK_SELF")
    _lock_target(company, user)
    target = {"company": company} if company else {"user": user}
    if SystemBlock.objects.filter(ended_at__isnull=True, **target).exists():
        raise errors.business_rule("BLOCK_EXISTS")
    found = SystemBlock.objects.create(
        target_type=Target.COMPANY if company else Target.USER,
        company=company,
        user=user,
        category=category,
        comment=comment,
        disclosed_reason=disclosed_reason,
        started_at=now,
        ends_at=ends_at,
        created_by_user_id=staff.user.id,
        notice=Notice.DEFERRED if defer_reason else Notice.PENDING,
        notice_deferred_by_user_id=staff.user.id if defer_reason else None,
        notice_deferral_reason=defer_reason,
    )
    ended = 0
    if user is not None:
        ended = accounts.end_all_sessions(user.id, UserSession.EndReason.BLOCKED, now)
    ctx.journal(
        "block.created",
        object_type="system_block",
        object_id=found.id,
        target_company_id=company.id if company else None,
        target_user_id=user.id if user else None,
        reason=comment,
        after={
            "target_type": found.target_type,
            "category": category,
            "disclosed_reason": disclosed_reason,
            "ends_at": ends_at,
            "sessions_ended": ended,
        },
    )
    if defer_reason:
        ctx.journal(
            "block.notice_deferred",
            object_type="system_block",
            object_id=found.id,
            reason=defer_reason,
        )
    elif deliver_notice(found, now):
        ctx.journal("block.notice_sent", object_type="system_block", object_id=found.id)
    return found


def locked(block_id: UUID) -> SystemBlock:
    found = (
        SystemBlock.objects.select_for_update(of=("self",))
        .select_related("company", "user")
        .filter(pk=block_id)
        .first()
    )
    if found is None:
        raise errors.not_found()
    return found


def _finish(found: SystemBlock, kind: str, reason: str, by: UUID | None, now: datetime) -> None:
    _lock_target(found.company, found.user)
    found.ended_at = now
    found.end_kind = kind
    found.end_reason = reason
    found.ended_by_user_id = by
    found.save(update_fields=["ended_at", "end_kind", "end_reason", "ended_by_user_id"])
    if found.company_id is not None:
        # § 24.2.3–24.2.4: the modules continue from now; a deactivated company stays so.
        suspension.resumed(found.company_id, found.started_at, now)


def restore(ctx: StaffContext, found: SystemBlock, reason: str) -> SystemBlock:
    if found.ended_at is not None:
        raise errors.business_rule("BLOCK_ENDED")
    now = timezone.now()
    _finish(found, SystemBlock.EndKind.MANUAL, reason, ctx.staff.user.id, now)
    ctx.journal(
        "block.restored",
        object_type="system_block",
        object_id=found.id,
        target_company_id=found.company_id,
        target_user_id=found.user_id,
        reason=reason,
        after={"ended_at": now, "end_kind": found.end_kind},
    )
    return found


def release_notice(ctx: StaffContext, found: SystemBlock) -> SystemBlock:
    ctx.staff.require("block.notice_defer")
    if found.notice != Notice.DEFERRED:
        raise errors.business_rule("NOTICE_NOT_DEFERRED", notice=found.notice)
    found.notice = Notice.PENDING
    found.save(update_fields=["notice"])
    ctx.journal("block.notice_released", object_type="system_block", object_id=found.id)
    if deliver_notice(found, timezone.now()):
        ctx.journal("block.notice_sent", object_type="system_block", object_id=found.id)
    return found


def deliver_notice(found: SystemBlock, now: datetime) -> bool:
    """Emails to the user or to the owners of the company, after the commit (§ 24.2.2)."""
    if found.notice != Notice.PENDING:
        return False
    reason = found.disclosed_reason or found.category
    support = support_contact()
    if found.user_id is not None:
        emails = accounts.confirmed_emails_of(found.user_id)
        company_name = None
    else:
        emails = (
            [
                e
                for uid in directory.owner_user_ids(found.company_id)
                for e in accounts.confirmed_emails_of(uid)
            ]
            if found.company_id
            else []
        )
        company_name = found.company.display_name if found.company else None

    def send(email: str) -> None:
        mail.send_block_notice(email, company_name=company_name, reason=reason, support=support)

    for email in emails:
        transaction.on_commit(partial(send, email))
    found.notice = Notice.SENT
    found.notified_at = now
    found.save(update_fields=["notice", "notified_at"])
    return True


def run(now: datetime) -> int:
    """Periodic: ends the blocks whose term has passed (§ 24.2.1)."""
    ended = 0
    for block_id in SystemBlock.objects.filter(
        ended_at__isnull=True, ends_at__isnull=False, ends_at__lte=now
    ).values_list("id", flat=True):
        with transaction.atomic():
            found = locked(block_id)
            if found.ended_at is not None:
                continue
            _finish(found, SystemBlock.EndKind.EXPIRED, "", None, now)
            system_journal(
                PROCESS,
                "block.expired",
                object_type="system_block",
                object_id=found.id,
                target_company_id=found.company_id,
                target_user_id=found.user_id,
                after={"ended_at": now, "ends_at": found.ends_at},
            )
            ended += 1
    return ended
