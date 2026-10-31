"""The company's own permission sets, applying an updated set and the confirmation of especially
broad rights (ТЗ § 23.1.3, § 23.1.5, § 23.1.15, § 23.3.5, § 23.3.9; block 14).

* An own set is created empty or from another set, changed and archived by «Manage permission
  sets»; none of it changes anybody's access (§ 23.1.3, § 23.3.9). Archived sets are not
  assigned anew; given assignments keep working (PROPOSAL).
* An assignment keeps a copy of the codes of its set. When the set differs (an own set was
  changed or c4rsys extended a system set), the holders are shown what would be added and
  removed, and the updated set is applied to the chosen ones by a separate action of a rights
  manager (§ 23.1.3, § 23.1.15). Applying never touches the actor's own rights (§ 23.3.2) nor the
  rights of a representative's dedicated position (§ 3.4.1).
* Especially broad additions from an employee wait as a rights request for the top-authority
  director (§ 23.3.5), who confirms with a one-time code (§ 2.1.1, PROPOSAL) or declines; the
  initiator may cancel. A mandatory notification and a system task go to the director
  (§ 19.1.9, § 20.3.4). A request is checked again on fresh data when confirmed.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from c4rsys.companies import access
from c4rsys.companies.models import (
    Employment,
    Permission,
    PermissionAssignment,
    PermissionSet,
    PermissionSetItem,
    RightsRequest,
)
from c4rsys.companies.permissions import BROAD_PERMISSIONS, SYSTEM_SETS
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

Kind = PermissionAssignment.Kind
EVENT = "employee.rights_confirmation"
NAME_MAX = 150


# --- Own sets ------------------------------------------------------------------------------------


def sets_of(company_id: UUID) -> list[PermissionSet]:
    """System sets first in the order of § 23.1.14, then the company's own, oldest first."""
    order = {code: i for i, code in enumerate(SYSTEM_SETS)}
    found = PermissionSet.objects.filter(
        Q(is_system=True) | Q(company_id=company_id, is_system=False)
    ).order_by("created_at", "id")
    return sorted(found, key=lambda s: (not s.is_system, order.get(s.code, len(order))))


def set_payload(permission_set: PermissionSet) -> dict[str, Any]:
    return {
        "code": access.set_ref(permission_set),
        "id": str(permission_set.id),
        "is_system": permission_set.is_system,
        "name": permission_set.name,
        "based_on": access.set_ref(permission_set.based_on) if permission_set.based_on else None,
        "archived": permission_set.archived_at is not None,
        "permissions": access.set_codes(permission_set),
        "version": permission_set.version,
    }


def _clean_codes(codes: list[str]) -> list[str]:
    unique = sorted(set(codes))
    known = set(Permission.objects.filter(code__in=unique).values_list("code", flat=True))
    if set(unique) - known:
        raise errors.validation_error("permissions", code="PERMISSION_UNKNOWN")
    return unique


def _name_free(company_id: UUID, name: str, own_id: UUID | None = None) -> None:
    taken = PermissionSet.objects.filter(
        company_id=company_id, is_system=False, archived_at__isnull=True, name__iexact=name
    )
    if own_id is not None:
        taken = taken.exclude(pk=own_id)
    if taken.exists():
        raise errors.business_rule("PERMISSION_SET_NAME_TAKEN", field="name")


def _write_items(permission_set: PermissionSet, codes: list[str]) -> None:
    PermissionSetItem.objects.filter(permission_set=permission_set).exclude(
        permission_id__in=codes
    ).delete()
    held = set(
        PermissionSetItem.objects.filter(permission_set=permission_set).values_list(
            "permission_id", flat=True
        )
    )
    PermissionSetItem.objects.bulk_create(
        PermissionSetItem(permission_set=permission_set, permission_id=c)
        for c in codes
        if c not in held
    )


def create_set(
    ctx: CommandContext,
    company_id: UUID,
    *,
    name: str,
    based_on: str | None,
    codes: list[str] | None,
) -> PermissionSet:
    """A new own set, empty, from the given codes or from another set (§ 23.1.5). Nobody gets it
    automatically (§ 23.1.15)."""
    base = None
    if based_on:
        base = access.resolve_set(company_id, based_on)
        if base is None:
            raise errors.validation_error("based_on", code="PERMISSION_SET_UNKNOWN")
    chosen = _clean_codes(codes if codes is not None else access.set_codes(base) if base else [])
    _name_free(company_id, name)
    created = PermissionSet.objects.create(
        company_id=company_id,
        is_system=False,
        name=name,
        based_on=base,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    _write_items(created, chosen)
    ctx.audit(
        "permission_set.created",
        object_type="permission_set",
        object_id=created.id,
        after={"name": name, "based_on": based_on, "permissions": chosen},
    )
    return created


def _locked_own(company_id: UUID, set_id: UUID, expected_version: int | None) -> PermissionSet:
    found = (
        PermissionSet.objects.select_for_update()
        .filter(company_id=company_id, is_system=False, pk=set_id)
        .first()
    )
    if found is None:
        raise errors.not_found()
    if expected_version is not None and found.version != expected_version:
        raise errors.version_conflict()
    return found


def change_set(
    ctx: CommandContext,
    company_id: UUID,
    set_id: UUID,
    *,
    name: str | None,
    codes: list[str] | None,
    expected_version: int,
) -> PermissionSet:
    """Renames the set or changes its codes. The holders keep their copies until the updated set
    is applied to them (§ 23.1.3)."""
    found = _locked_own(company_id, set_id, expected_version)
    if found.archived_at is not None:
        raise errors.business_rule("PERMISSION_SET_ARCHIVED")
    before = {"name": found.name, "permissions": access.set_codes(found)}
    if name is not None and name != found.name:
        _name_free(company_id, name, found.id)
        found.name = name
    if codes is not None:
        _write_items(found, _clean_codes(codes))
    after = {"name": found.name, "permissions": access.set_codes(found)}
    if after == before:
        raise errors.business_rule("NOTHING_CHANGED")
    found.version += 1
    found.updated_at = timezone.now()
    found.save(update_fields=["name", "version", "updated_at"])
    ctx.audit(
        "permission_set.changed",
        object_type="permission_set",
        object_id=found.id,
        before=before,
        after={
            **after,
            "added": sorted(set(after["permissions"]) - set(before["permissions"])),
            "removed": sorted(set(before["permissions"]) - set(after["permissions"])),
        },
    )
    return found


def set_archived(
    ctx: CommandContext, company_id: UUID, set_id: UUID, *, archived: bool, expected_version: int
) -> PermissionSet:
    """Archiving hides the set from new assignments and does not revoke the given ones
    (PROPOSAL); restoring needs a free name."""
    found = _locked_own(company_id, set_id, expected_version)
    if (found.archived_at is not None) == archived:
        raise errors.business_rule("NOTHING_CHANGED")
    if not archived:
        _name_free(company_id, found.name, found.id)
    found.archived_at = timezone.now() if archived else None
    found.version += 1
    found.save(update_fields=["archived_at", "version"])
    ctx.audit(
        "permission_set.archived" if archived else "permission_set.restored",
        object_type="permission_set",
        object_id=found.id,
        after={"name": found.name, "holders": len(_held(company_id, found))},
    )
    return found


# --- Applying an updated set (§ 23.1.3, § 23.1.15) -----------------------------------------------


def _held(company_id: UUID, permission_set: PermissionSet) -> list[PermissionAssignment]:
    return list(
        PermissionAssignment.objects.filter(
            company_id=company_id,
            permission_set=permission_set,
            revoked_at__isnull=True,
            owner_seat__isnull=True,
        )
        .exclude(employment__state=Employment.State.TERMINATED)
        .select_related("employment", "employment__user", "employment__position")
        .order_by("granted_at")
    )


def _person(employment: Employment) -> dict[str, Any]:
    user = employment.user
    return {
        "employment_id": str(employment.id),
        "name": f"{user.first_name} {user.last_name}".strip() if user else "",
        "public_id": user.public_id if user else None,
        "state": employment.state,
    }


def holders(company_id: UUID, ref: str, actor_employment_id: UUID | None) -> dict[str, Any]:
    """Every holder of the set with what applying it would add and remove (§ 23.1.3)."""
    permission_set = access.resolve_set(company_id, ref)
    if permission_set is None:
        raise errors.not_found()
    current = set(access.set_codes(permission_set))
    items = []
    for a in _held(company_id, permission_set):
        held = set(a.permissions)
        added = sorted(current - held)
        items.append(
            {
                **_person(a.employment),
                "assignment_id": str(a.id),
                "scope": access.spec_of(a).as_dict(),
                "added": added,
                "removed": sorted(held - current),
                "broad": bool(set(added) & BROAD_PERMISSIONS),
                "own": a.employment_id == actor_employment_id,
            }
        )
    return {"set": set_payload(permission_set), "holders": items}


@dataclass(frozen=True)
class Applied:
    applied: list[str]
    requests: list[RightsRequest]


def broad_apply(company_id: UUID, ref: str, employment_ids: list[UUID]) -> bool:
    """Whether applying adds especially broad rights to any chosen holder (read without locks,
    to ask for the code first; the command checks again)."""
    permission_set = access.resolve_set(company_id, ref)
    if permission_set is None:
        return False
    current = set(access.set_codes(permission_set))
    return any(
        (current - set(a.permissions)) & BROAD_PERMISSIONS
        for a in _held(company_id, permission_set)
        if a.employment_id in employment_ids
    )


def apply_set(
    ctx: CommandContext,
    company_id: UUID,
    ref: str,
    employment_ids: list[UUID],
    *,
    is_top_authority: bool,
    code_confirmed: bool,
) -> Applied:
    """Replaces the outdated copies of the set held by the chosen employees with the current set,
    each in the scope of its assignment; the old assignment is revoked with history. An update
    that adds an especially broad right follows § 23.3.5 like the rights editor."""
    permission_set = access.resolve_set(company_id, ref)
    if permission_set is None:
        raise errors.not_found()
    if not employment_ids:
        raise errors.validation_error("employment_ids")
    current = access.set_codes(permission_set)
    applied: list[str] = []
    requests: list[RightsRequest] = []
    for employment_id in sorted(set(employment_ids)):
        employment = access.locked_target(ctx, company_id, employment_id)
        outdated = [
            a
            for a in _held(company_id, permission_set)
            if a.employment_id == employment.id and set(a.permissions) != set(current)
        ]
        if not outdated:
            raise errors.business_rule("PERMISSION_SET_UP_TO_DATE", field="employment_ids")
        for a in outdated:
            spec = access.spec_of(a)
            added = sorted(set(current) - set(a.permissions))
            removed = sorted(set(a.permissions) - set(current))
            if set(added) & BROAD_PERMISSIONS:
                access.require_broad_allowed(ctx.actor.user_id, employment)
                if not is_top_authority:
                    requests.append(open_request(ctx, employment, [spec], replaces=[a.id]))
                    continue
                if not code_confirmed:
                    raise errors.business_rule("BROAD_RIGHTS_CODE_REQUIRED", field="rights")
            access.revoke([a], ctx.actor.user_id)
            created = access.assign(employment, [spec], ctx.actor.user_id)
            applied.append(str(created[0].id))
            ctx.audit(
                "employee.rights_set_applied",
                object_type="employment",
                object_id=employment.id,
                before={"assignment": str(a.id), "permissions": sorted(a.permissions)},
                after={
                    "assignment": str(created[0].id),
                    "set": ref,
                    "added": added,
                    "removed": removed,
                    "scope": spec.as_dict(),
                },
            )
        employment.version += 1
        employment.save(update_fields=["version"])
    return Applied(applied, requests)


# --- Confirmation of especially broad rights (§ 23.3.5) -----------------------------------------


def top_authorities(company_id: UUID) -> list[Employment]:
    return list(
        Employment.objects.filter(
            company_id=company_id,
            state=Employment.State.ACTIVE,
            position__is_top_authority=True,
            user__isnull=False,
        )
    )


def _condition(request: RightsRequest) -> str:
    return f"{EVENT}:{request.id}"


def open_request(
    ctx: CommandContext,
    employment: Employment,
    specs: list[access.RightSpec],
    *,
    replaces: list[UUID],
) -> RightsRequest:
    """A request of especially broad rights for `employment`, initiated by the actor."""
    if ctx.actor.employment_id is None:
        raise errors.permission_denied("employee.rights.manage")
    company_id = employment.company_id
    request = RightsRequest.objects.create(
        company_id=company_id,
        employment=employment,
        rights=[s.as_dict() for s in specs],
        codes=[access.codes_of(company_id, s) for s in specs],
        replaces=list(replaces),
        initiated_by_user_id=ctx.actor.user_id,
        initiated_by_employment_id=ctx.actor.employment_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "employee.rights_confirmation_requested",
        object_type="employment",
        object_id=employment.id,
        after={
            "request": str(request.id),
            "rights": request.rights,
            "replaces": [str(x) for x in replaces],
        },
    )
    _tell(request)
    return request


def _tell(request: RightsRequest) -> None:
    """The mandatory notification and the system task for the director (§ 19.1.9, § 20.3.4)."""
    from c4rsys.notifications import rules
    from c4rsys.tasks import services as tasks

    tops = top_authorities(request.company_id)
    target = _person(request.employment)
    params = {"employee": target["name"] or target["public_id"] or ""}
    if tops:
        rules.raise_event(
            request.company_id,
            EVENT,
            condition=_condition(request),
            object_type="employment",
            object_id=request.employment_id,
            params=params,
            recipients=[e.id for e in tops],
        )
    task = tasks.ensure_system(
        request.company_id,
        event=EVENT,
        condition=_condition(request),
        location_id=None,
        object_type="employment",
        object_id=request.employment_id,
        params=params,
    )
    if task is not None and len(tops) == 1:
        # PROPOSAL: the task is assigned to the director at once, not left in the common queue.
        task.assignee_id = tops[0].id
        task.save(update_fields=["assignee"])


def _close(request: RightsRequest) -> None:
    from c4rsys.notifications import services as notifications
    from c4rsys.tasks import services as tasks

    notifications.resolve(request.company_id, _condition(request), request.state)
    tasks.close_system(
        request.company_id,
        _condition(request),
        done=request.state == RightsRequest.State.CONFIRMED,
        result=request.state,
    )


def requests_of(company_id: UUID, *, employment_id: UUID | None = None) -> list[RightsRequest]:
    query = RightsRequest.objects.filter(company_id=company_id).select_related(
        "employment", "employment__user"
    )
    if employment_id is not None:
        query = query.filter(employment_id=employment_id)
    return list(query.order_by("-created_at")[:100])


def request_payload(request: RightsRequest) -> dict[str, Any]:
    from c4rsys.accounts.models import UserAccount

    users = UserAccount.objects.in_bulk(
        [x for x in (request.initiated_by_user_id, request.decided_by_user_id) if x]
    )

    def name(user_id: UUID | None) -> str:
        user = users.get(user_id) if user_id else None
        return f"{user.first_name} {user.last_name}".strip() if user else ""

    return {
        "id": str(request.id),
        "state": request.state,
        "employee": _person(request.employment),
        "rights": [
            {**r, "permissions": c} for r, c in zip(request.rights, request.codes, strict=True)
        ],
        "replaces": [str(x) for x in request.replaces],
        "initiated_by": {
            "name": name(request.initiated_by_user_id),
            "employment_id": str(request.initiated_by_employment_id),
        },
        "created_at": request.created_at.isoformat(),
        "decided_by": name(request.decided_by_user_id) or None,
        "decided_at": request.decided_at.isoformat() if request.decided_at else None,
        "note": request.decision_note,
    }


def _locked_request(company_id: UUID, request_id: UUID) -> RightsRequest:
    found = (
        RightsRequest.objects.select_for_update(of=("self",))
        .select_related("employment", "employment__user")
        .filter(company_id=company_id, pk=request_id)
        .first()
    )
    if found is None:
        raise errors.not_found()
    if found.state != RightsRequest.State.PENDING:
        raise errors.business_rule("RIGHTS_REQUEST_NOT_PENDING")
    return found


def _finish(ctx: CommandContext, request: RightsRequest, state: str, note: str = "") -> None:
    request.state = state
    request.decided_by_user_id = ctx.actor.user_id
    request.decided_at = timezone.now()
    request.decision_note = note
    request.save(update_fields=["state", "decided_by_user_id", "decided_at", "decision_note"])
    _close(request)


def _obstacle(request: RightsRequest, specs: list[access.RightSpec]) -> str:
    """Why the request no longer matches the fresh data, or ''."""
    employment = request.employment
    if employment.state not in (Employment.State.ACTIVE, Employment.State.BLOCKED):
        return "employment_not_active"
    active = set(
        PermissionAssignment.objects.filter(
            pk__in=request.replaces, revoked_at__isnull=True
        ).values_list("id", flat=True)
    )
    if active != set(request.replaces):
        return "rights_changed"
    try:
        access.validate_rights(request.company_id, specs)
    except errors.ApiError:
        return "scope_changed"
    for spec, codes in zip(specs, request.codes, strict=True):
        if sorted(access.codes_of(request.company_id, spec)) != sorted(codes):
            return "set_changed"
    return ""


def confirm(ctx: CommandContext, company_id: UUID, request_id: UUID) -> RightsRequest:
    """The top-authority director confirms; the view has checked his one-time code. The
    initiator never confirms his own request, and nobody confirms rights for himself."""
    request = _locked_request(company_id, request_id)
    if ctx.actor.employment_id in (request.initiated_by_employment_id, request.employment_id):
        raise errors.business_rule("RIGHTS_REQUEST_OWN")
    employment = Employment.objects.select_for_update().filter(pk=request.employment_id).first()
    if employment is None:
        raise errors.not_found()
    request.employment = employment
    specs = access.parse_rights(request.rights)
    obstacle = _obstacle(request, specs)
    if obstacle:
        _finish(ctx, request, RightsRequest.State.OUTDATED, obstacle)
        ctx.audit(
            "employee.rights_confirmation_outdated",
            object_type="employment",
            object_id=employment.id,
            after={"request": str(request.id), "reason": obstacle},
        )
        return request
    access.require_broad_allowed(ctx.actor.user_id, employment)
    replaced = list(PermissionAssignment.objects.filter(pk__in=request.replaces))
    access.revoke(replaced, ctx.actor.user_id)
    held = {
        access.spec_of(a)
        for a in PermissionAssignment.objects.filter(
            employment=employment, revoked_at__isnull=True, owner_seat__isnull=True
        )
    }
    created = access.assign(
        employment, [s for s in specs if s not in held], request.initiated_by_user_id
    )
    employment.version += 1
    employment.save(update_fields=["version"])
    _finish(ctx, request, RightsRequest.State.CONFIRMED)
    ctx.audit(
        "employee.rights_confirmed",
        object_type="employment",
        object_id=employment.id,
        after={
            "request": str(request.id),
            "rights": request.rights,
            "assignments": [str(a.id) for a in created],
            "revoked": [str(a.id) for a in replaced],
            "initiated_by": str(request.initiated_by_user_id),
        },
    )
    return request


def decline(ctx: CommandContext, company_id: UUID, request_id: UUID, note: str) -> RightsRequest:
    request = _locked_request(company_id, request_id)
    if ctx.actor.employment_id == request.initiated_by_employment_id:
        raise errors.business_rule("RIGHTS_REQUEST_OWN")
    _finish(ctx, request, RightsRequest.State.DECLINED, note)
    ctx.audit(
        "employee.rights_declined",
        object_type="employment",
        object_id=request.employment_id,
        after={"request": str(request.id)},
        reason=note,
    )
    return request


def cancel(ctx: CommandContext, company_id: UUID, request_id: UUID) -> RightsRequest:
    """The initiator withdraws his request (PROPOSAL)."""
    request = _locked_request(company_id, request_id)
    if ctx.actor.employment_id != request.initiated_by_employment_id:
        raise errors.permission_denied("employee.rights.manage")
    _finish(ctx, request, RightsRequest.State.CANCELLED)
    ctx.audit(
        "employee.rights_request_cancelled",
        object_type="employment",
        object_id=request.employment_id,
        after={"request": str(request.id)},
    )
    return request
