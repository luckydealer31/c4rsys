"""Accounts of the system staff and their system sets (ТЗ § 24.1.5–24.1.6)."""

import re
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from c4rsys.accounts.models import UserAccount
from c4rsys.backoffice import catalog
from c4rsys.backoffice.core import StaffContext, roles_of
from c4rsys.backoffice.models import (
    SystemJournalEntry,
    SystemRole,
    SystemRoleAssignment,
    SystemStaffMember,
)
from c4rsys.platform import errors

ROLE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,59}$")


def ensure_starter_roles() -> None:
    """The starter sets exist with their current composition."""
    for code, permissions in catalog.STARTER_SETS.items():
        SystemRole.objects.update_or_create(
            code=code, defaults={"permissions": list(permissions), "is_starter": True}
        )


def role_payload(role: SystemRole) -> dict[str, Any]:
    return {
        "code": role.code,
        "name": role.name,
        "permissions": sorted(role.permissions),
        "is_starter": role.is_starter,
    }


def member_payload(member: SystemStaffMember) -> dict[str, Any]:
    user = member.user
    return {
        "id": str(member.id),
        "user": {
            "public_id": user.public_id,
            "name": " ".join(x for x in (user.last_name, user.first_name) if x),
        },
        "unit": member.unit,
        "state": member.state,
        "roles": [r.code for r in roles_of(member)],
        "created_at": member.created_at.isoformat(),
        "revoked_at": member.revoked_at.isoformat() if member.revoked_at else None,
        "version": member.version,
    }


def members() -> list[SystemStaffMember]:
    return list(SystemStaffMember.objects.select_related("user").order_by("created_at"))


def _roles(codes: list[str]) -> list[SystemRole]:
    found = {r.code: r for r in SystemRole.objects.filter(code__in=codes)}
    if set(codes) - set(found):
        raise errors.validation_error("roles", code="SYSTEM_ROLE_UNKNOWN")
    return [found[c] for c in codes]


def _set_roles(
    member: SystemStaffMember, codes: list[str], by: UUID | None
) -> tuple[list[str], list[str]]:
    wanted = {r.code: r for r in _roles(codes)}
    current = {
        a.role.code: a
        for a in SystemRoleAssignment.objects.select_for_update()
        .select_related("role")
        .filter(member=member, revoked_at__isnull=True)
    }
    now = timezone.now()
    for code, assignment in current.items():
        if code not in wanted:
            assignment.revoked_at = now
            assignment.revoked_by_user_id = by
            assignment.save(update_fields=["revoked_at", "revoked_by_user_id"])
    for code, role in wanted.items():
        if code not in current:
            SystemRoleAssignment.objects.create(member=member, role=role, granted_by_user_id=by)
    return sorted(current), sorted(wanted)


def add(ctx: StaffContext, user: UserAccount, unit: str, codes: list[str]) -> SystemStaffMember:
    member = SystemStaffMember.objects.select_for_update().filter(user=user).first()
    if member is not None and member.state == SystemStaffMember.State.ACTIVE:
        raise errors.business_rule("STAFF_MEMBER_EXISTS")
    if member is None:
        member = SystemStaffMember.objects.create(
            user=user, unit=unit, created_by_user_id=ctx.staff.user.id
        )
    else:
        member.state = SystemStaffMember.State.ACTIVE
        member.unit = unit
        member.revoked_at = None
        member.version += 1
        member.save()
    _before, after = _set_roles(member, codes, ctx.staff.user.id)
    ctx.journal(
        "staff.member_added",
        object_type="system_staff_member",
        object_id=member.id,
        target_user_id=user.id,
        after={"user": user.public_id, "unit": unit, "roles": after},
    )
    return member


def locked(member_id: UUID, expected: int | None) -> SystemStaffMember:
    member = SystemStaffMember.objects.select_for_update().filter(pk=member_id).first()
    if member is None:
        raise errors.not_found()
    if expected is not None and member.version != expected:
        raise errors.version_conflict()
    return member


def change(
    ctx: StaffContext, member: SystemStaffMember, unit: str | None, codes: list[str] | None
) -> SystemStaffMember:
    if member.state != SystemStaffMember.State.ACTIVE:
        raise errors.business_rule("STAFF_MEMBER_REVOKED")
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if codes is not None:
        if member.id == ctx.staff.member.id and catalog.PLATFORM_ADMIN not in codes:
            # PROPOSAL: an administrator does not take his own management away by mistake.
            raise errors.business_rule("STAFF_OWN_ADMIN_REMOVAL")
        before["roles"], after["roles"] = _set_roles(member, codes, ctx.staff.user.id)
    if unit is not None and unit != member.unit:
        before["unit"], after["unit"] = member.unit, unit
        member.unit = unit
    member.version += 1
    member.save()
    ctx.journal(
        "staff.member_changed",
        object_type="system_staff_member",
        object_id=member.id,
        target_user_id=member.user_id,
        before=before,
        after=after,
    )
    return member


def revoke(ctx: StaffContext, member: SystemStaffMember, reason: str) -> SystemStaffMember:
    if member.state != SystemStaffMember.State.ACTIVE:
        raise errors.business_rule("STAFF_MEMBER_REVOKED")
    if member.id == ctx.staff.member.id:
        raise errors.business_rule("STAFF_OWN_ADMIN_REMOVAL")
    before = _set_roles(member, [], ctx.staff.user.id)[0]
    member.state = SystemStaffMember.State.REVOKED
    member.revoked_at = timezone.now()
    member.version += 1
    member.save()
    ctx.journal(
        "staff.member_revoked",
        object_type="system_staff_member",
        object_id=member.id,
        target_user_id=member.user_id,
        before={"roles": before},
        reason=reason,
    )
    return member


def create_role(ctx: StaffContext, code: str, name: str, permissions: list[str]) -> SystemRole:
    if not ROLE_CODE_RE.match(code):
        raise errors.validation_error("code", code="SYSTEM_ROLE_CODE_INVALID")
    if not permissions or set(permissions) - set(catalog.PERMISSIONS):
        raise errors.validation_error("permissions", code="SYSTEM_PERMISSION_UNKNOWN")
    try:
        with transaction.atomic():
            role = SystemRole.objects.create(
                code=code,
                name=name,
                permissions=sorted(set(permissions)),
                created_by_user_id=ctx.staff.user.id,
            )
    except IntegrityError as exc:
        raise errors.business_rule("SYSTEM_ROLE_EXISTS", field="code") from exc
    ctx.journal(
        "staff.role_created",
        object_type="system_role",
        object_id=role.code,
        after=role_payload(role),
    )
    return role


def bootstrap_admin(user: UserAccount, unit: str) -> SystemStaffMember:
    """The first platform administrator, by the management command on the server. Written to
    the system journal as an action of the process `bootstrap`."""
    with transaction.atomic():
        ensure_starter_roles()
        member, _ = SystemStaffMember.objects.select_for_update().get_or_create(
            user=user, defaults={"unit": unit}
        )
        if member.state != SystemStaffMember.State.ACTIVE:
            member.state = SystemStaffMember.State.ACTIVE
            member.revoked_at = None
            member.version += 1
            member.save()
        role = SystemRole.objects.get(code=catalog.PLATFORM_ADMIN)
        if not SystemRoleAssignment.objects.filter(
            member=member, role=role, revoked_at__isnull=True
        ).exists():
            SystemRoleAssignment.objects.create(member=member, role=role)
        SystemJournalEntry.objects.create(
            system_process="bootstrap",
            action="staff.platform_admin_granted",
            object_type="system_staff_member",
            object_id=str(member.id),
            target_user_id=user.id,
            after={"user": user.public_id, "roles": [r.code for r in roles_of(member)]},
        )
    return member
