"""Who is a system staff member and the system journal (ТЗ § 24.1.1, § 24.1.6, § 24.4.5).

* The internal API answers only on the staff subdomain and only to a session opened there by an
  active member (decision В-11.1). Permissions are read from the current sets at every request.
* Every action of the staff is written to the system journal, apart from the journals of
  companies. A command writes it in its own transaction (`run_staff`); a logged read writes it
  before the data is returned (`logged_read`), so data is never shown without its entry.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.db import transaction
from django.http import HttpRequest, JsonResponse

from c4rsys.accounts.middleware import require_user
from c4rsys.accounts.models import UserAccount, UserSession
from c4rsys.backoffice.models import (
    SystemCounter,
    SystemJournalEntry,
    SystemRole,
    SystemRoleAssignment,
    SystemStaffMember,
)
from c4rsys.platform import errors, sites
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.platform.http import idempotency_key, respond
from c4rsys.platform.middleware import get_request_id


def active_member(user_id: UUID) -> SystemStaffMember | None:
    return SystemStaffMember.objects.filter(
        user_id=user_id, state=SystemStaffMember.State.ACTIVE
    ).first()


def roles_of(member: SystemStaffMember) -> list[SystemRole]:
    return [
        a.role
        for a in SystemRoleAssignment.objects.select_related("role")
        .filter(member=member, revoked_at__isnull=True)
        .order_by("granted_at")
    ]


@dataclass(frozen=True)
class Staff:
    user: UserAccount
    session: UserSession
    member: SystemStaffMember
    roles: tuple[str, ...]
    permissions: frozenset[str]

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise errors.permission_denied(permission)

    @property
    def actor(self) -> Actor:
        return Actor(user_id=self.user.id, session_id=self.session.id)


def staff_of(member: SystemStaffMember, user: UserAccount, session: UserSession) -> Staff:
    roles = roles_of(member)
    return Staff(
        user=user,
        session=session,
        member=member,
        roles=tuple(r.code for r in roles),
        permissions=frozenset(p for r in roles for p in r.permissions),
    )


def require_staff(request: HttpRequest, *permissions: str) -> Staff:
    """The staff member of the request with every one of `permissions`."""
    if not sites.is_staff_host(request):
        raise errors.not_found()
    user, session = require_user(request)
    if session.site != sites.STAFF:
        raise errors.ApiError("SESSION_REQUIRED", status=401)
    member = active_member(user.id)
    if member is None:
        raise errors.ApiError("STAFF_ACCESS_DENIED", status=403)
    staff = staff_of(member, user, session)
    for permission in permissions:
        staff.require(permission)
    return staff


# --- The system journal ---------------------------------------------------------------------------


def _entry(
    staff: Staff | None,
    action: str,
    *,
    object_type: str,
    object_id: str | UUID,
    request_id: str = "",
    idempotency: str = "",
    system_process: str = "",
    **values: Any,
) -> SystemJournalEntry:
    return SystemJournalEntry(
        actor_user_id=staff.user.id if staff else None,
        staff_member_id=staff.member.id if staff else None,
        roles=list(staff.roles) if staff else [],
        session_id=staff.session.id if staff else None,
        system_process=system_process,
        request_id=request_id,
        idempotency_key=idempotency,
        action=action,
        object_type=object_type,
        object_id=str(object_id)[:64],
        **values,
    )


@dataclass
class StaffContext:
    """The context of a staff command: the platform context and the system journal entries."""

    ctx: CommandContext
    staff: Staff
    entries: list[SystemJournalEntry] = field(default_factory=list)

    def journal(
        self, action: str, *, object_type: str, object_id: str | UUID, **values: Any
    ) -> None:
        self.entries.append(
            _entry(
                self.staff,
                action,
                object_type=object_type,
                object_id=object_id,
                request_id=self.ctx.request_id,
                idempotency=self.ctx.idempotency_key,
                **values,
            )
        )


StaffHandler = Callable[[StaffContext], CommandResult]


def run_staff(
    request: HttpRequest,
    command: str,
    staff: Staff,
    payload: dict[str, Any],
    handler: StaffHandler,
) -> JsonResponse:
    """A command of the internal interface (A07): one transaction with its system journal
    entries and the Idempotency-Key; if the journal is not written, nothing is."""
    holder: list[StaffContext] = []

    def inner(ctx: CommandContext) -> CommandResult:
        context = StaffContext(ctx, staff)
        holder.append(context)
        return handler(context)

    def writer(_ctx: CommandContext) -> int:
        entries = holder[0].entries if holder else []
        SystemJournalEntry.objects.bulk_create(entries)
        return len(entries)

    return respond(
        run_command(
            command=f"staff.{command}",
            actor=staff.actor,
            idempotency_key=idempotency_key(request),
            payload=payload,
            handler=inner,
            request_id=get_request_id(request),
            journal_writer=writer,
        )
    )


def logged_read(
    request: HttpRequest,
    staff: Staff,
    action: str,
    *,
    object_type: str,
    object_id: str | UUID,
    **values: Any,
) -> None:
    """A read of the internal interface that must be journaled (a search, a view of a user or a
    company, a reveal of contacts, a section of a privileged session)."""
    _entry(
        staff,
        action,
        object_type=object_type,
        object_id=object_id,
        request_id=get_request_id(request),
        **values,
    ).save(force_insert=True)


def system_journal(
    process: str, action: str, *, object_type: str, object_id: str | UUID, **values: Any
) -> None:
    """An entry of a system process (the end of a block by its term, a sent notice)."""
    _entry(
        None, action, object_type=object_type, object_id=object_id, system_process=process, **values
    ).save(force_insert=True)


def next_number(kind: str) -> int:
    with transaction.atomic():
        counter, _ = SystemCounter.objects.select_for_update().get_or_create(kind=kind)
        counter.last_value += 1
        counter.save(update_fields=["last_value"])
        return counter.last_value
