"""Employee rights: assignments with scopes and the permission check (ТЗ § 23.1–23.3).

Effective permissions are the union of all active set and grant assignments, each within its own
scope, minus personal denials (§ 23.1.2, § 23.1.13). The director with top authority may do
everything in the company (§ 3.1.4). Checks always read the current assignments (§ 23.3.8).
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import Prefetch
from django.utils import timezone

from c4rsys.companies.models import (
    ActivityContext,
    AssignmentCashAccount,
    AssignmentContext,
    AssignmentLocation,
    Employment,
    Location,
    Permission,
    PermissionAssignment,
    PermissionSet,
    PermissionSetItem,
)
from c4rsys.companies.permissions import BROAD_PERMISSIONS, BROAD_SETS
from c4rsys.finance import services as finance
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext

Kind = PermissionAssignment.Kind


@dataclass(frozen=True)
class RightSpec:
    """One requested assignment. `locations` / `contexts` / `cash_accounts` None means «all»
    (§ 23.1.9–23.1.11)."""

    kind: str
    code: str  # set code for kind=set, permission code otherwise
    locations: frozenset[UUID] | None = None
    contexts: frozenset[UUID] | None = None
    cash_accounts: frozenset[UUID] | None = None

    @property
    def is_broad(self) -> bool:
        if self.kind == Kind.SET:
            return self.code in BROAD_SETS
        return self.kind == Kind.GRANT and self.code in BROAD_PERMISSIONS

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            ("set" if self.kind == Kind.SET else "permission"): self.code,
            "locations": "all" if self.locations is None else sorted(map(str, self.locations)),
            "contexts": "all" if self.contexts is None else sorted(map(str, self.contexts)),
            "cash_accounts": "all"
            if self.cash_accounts is None
            else sorted(map(str, self.cash_accounts)),
        }


def _uuid_list(value: Any, field: str) -> frozenset[UUID] | None:
    if value == "all":
        return None
    if not isinstance(value, list) or not value:
        raise errors.validation_error(field, code="SCOPE_INVALID")
    try:
        return frozenset(UUID(str(item)) for item in value)
    except ValueError as exc:
        raise errors.validation_error(field, code="SCOPE_INVALID") from exc


def parse_rights(value: Any, field: str = "rights") -> list[RightSpec]:
    """Parse `[{type, set|permission, locations, contexts, cash_accounts}]` from a request."""
    if not isinstance(value, list):
        raise errors.validation_error(field)
    specs: list[RightSpec] = []
    for index, item in enumerate(value):
        where = f"{field}[{index}]"
        if not isinstance(item, dict) or item.get("type") not in Kind.values:
            raise errors.validation_error(where)
        kind = str(item["type"])
        code = item.get("set" if kind == Kind.SET else "permission")
        if not isinstance(code, str) or not code:
            raise errors.validation_error(where)
        specs.append(
            RightSpec(
                kind=kind,
                code=code,
                locations=_uuid_list(item.get("locations", "all"), f"{where}.locations"),
                contexts=_uuid_list(item.get("contexts", "all"), f"{where}.contexts"),
                cash_accounts=_uuid_list(
                    item.get("cash_accounts", "all"), f"{where}.cash_accounts"
                ),
            )
        )
    if len(set(specs)) != len(specs):
        raise errors.validation_error(field, code="RIGHTS_DUPLICATED")
    return specs


def validate_rights(company_id: UUID, specs: list[RightSpec]) -> None:
    """Codes exist; selected locations, contexts and cash accounts belong to the company. Listed
    contexts belong to the listed locations; a listed cash account is shared by the company or
    belongs to a listed location (§ 23.1.12)."""
    sets = {s.code for s in specs if s.kind == Kind.SET}
    known_sets = set(PermissionSet.objects.filter(is_system=True).values_list("code", flat=True))
    if sets - known_sets:
        raise errors.validation_error("rights", code="PERMISSION_SET_UNKNOWN")
    perms = {s.code for s in specs if s.kind != Kind.SET}
    if perms - set(Permission.objects.filter(code__in=perms).values_list("code", flat=True)):
        raise errors.validation_error("rights", code="PERMISSION_UNKNOWN")
    for spec in specs:
        if spec.locations is not None:
            found = Location.objects.filter(company_id=company_id, pk__in=spec.locations).count()
            if found != len(spec.locations):
                raise errors.validation_error("rights", code="SCOPE_INVALID")
        if spec.contexts is not None:
            contexts = ActivityContext.objects.filter(company_id=company_id, pk__in=spec.contexts)
            if spec.locations is not None:
                contexts = contexts.filter(location_id__in=spec.locations)
            if contexts.count() != len(spec.contexts):
                raise errors.validation_error("rights", code="SCOPE_INVALID")
        for account_id in spec.cash_accounts or ():
            place = finance.account_place(company_id, account_id)
            if place is None or (
                spec.locations is not None
                and place.location_id is not None
                and place.location_id not in spec.locations
            ):
                raise errors.validation_error("rights", code="SCOPE_INVALID")


def require_broad_rights_authority(specs: list[RightSpec], *, is_top_authority: bool) -> None:
    """At stage 1 especially broad rights are assigned only by the director with top authority
    (§ 23.3.5, decision 22.10.2026); the confirmation procedure comes later."""
    if not is_top_authority and any(s.is_broad for s in specs):
        raise errors.business_rule("BROAD_RIGHTS_REQUIRE_TOP_AUTHORITY", field="rights")


# --- Stored assignments -----------------------------------------------------------------------


def _active_assignments(employment_id: UUID) -> list[PermissionAssignment]:
    return list(
        PermissionAssignment.objects.filter(employment_id=employment_id, revoked_at__isnull=True)
        .select_related("permission_set")
        .prefetch_related(
            Prefetch("locations", queryset=Location.objects.only("id")),
            Prefetch("contexts", queryset=ActivityContext.objects.only("id")),
            "cash_accounts",
        )
        .order_by("granted_at")
    )


def _spec_of(assignment: PermissionAssignment) -> RightSpec:
    set_ = assignment.permission_set
    return RightSpec(
        kind=assignment.kind,
        code=set_.code if set_ is not None else assignment.permissions[0],
        locations=None
        if assignment.all_locations
        else frozenset(x.id for x in assignment.locations.all()),
        contexts=None
        if assignment.all_contexts
        else frozenset(x.id for x in assignment.contexts.all()),
        cash_accounts=None
        if assignment.all_cash_accounts
        else frozenset(x.cash_account_id for x in assignment.cash_accounts.all()),
    )


def rights_of(employment_id: UUID) -> list[dict[str, Any]]:
    """Each assignment with its source, codes and scope (§ 23.1.13)."""
    return [
        {
            "id": str(a.id),
            **_spec_of(a).as_dict(),
            "permissions": sorted(a.permissions),
            "granted_at": a.granted_at.isoformat(),
        }
        for a in _active_assignments(employment_id)
    ]


def assign(
    employment: Employment, specs: list[RightSpec], granted_by_user_id: UUID
) -> list[PermissionAssignment]:
    now = timezone.now()
    created = []
    for spec in specs:
        permission_set = None
        if spec.kind == Kind.SET:
            permission_set = PermissionSet.objects.get(is_system=True, code=spec.code)
            codes = sorted(
                PermissionSetItem.objects.filter(permission_set=permission_set).values_list(
                    "permission_id", flat=True
                )
            )
        else:
            codes = [spec.code]
        assignment = PermissionAssignment.objects.create(
            company_id=employment.company_id,
            employment=employment,
            kind=spec.kind,
            permission_set=permission_set,
            permissions=codes,
            all_locations=spec.locations is None,
            all_contexts=spec.contexts is None,
            all_cash_accounts=spec.cash_accounts is None,
            granted_by_user_id=granted_by_user_id,
            granted_at=now,
        )
        AssignmentLocation.objects.bulk_create(
            AssignmentLocation(
                company_id=employment.company_id, assignment=assignment, location_id=x
            )
            for x in spec.locations or ()
        )
        AssignmentContext.objects.bulk_create(
            AssignmentContext(company_id=employment.company_id, assignment=assignment, context_id=x)
            for x in spec.contexts or ()
        )
        AssignmentCashAccount.objects.bulk_create(
            AssignmentCashAccount(
                company_id=employment.company_id, assignment=assignment, cash_account_id=x
            )
            for x in spec.cash_accounts or ()
        )
        created.append(assignment)
    return created


def replace_rights(
    ctx: CommandContext,
    *,
    company_id: UUID,
    employment_id: UUID,
    specs: list[RightSpec],
    expected_version: int,
    is_top_authority: bool,
) -> Employment:
    """Sets the employee's rights to exactly `specs`. Unchanged assignments are kept; removed ones
    are revoked with history (§ 23.3.3). Own rights cannot be changed (§ 23.3.2)."""
    employment = (
        Employment.objects.select_for_update()
        .filter(company_id=company_id, pk=employment_id)
        .first()
    )
    if employment is None:
        raise errors.not_found()
    if employment.id == ctx.actor.employment_id:
        raise errors.business_rule("SELF_RIGHTS_CHANGE_FORBIDDEN")
    if employment.state not in (Employment.State.ACTIVE, Employment.State.BLOCKED):
        raise errors.business_rule("EMPLOYMENT_NOT_ACTIVE")
    if employment.version != expected_version:
        raise errors.version_conflict()

    current = {_spec_of(a): a for a in _active_assignments(employment.id)}
    wanted = set(specs)
    added = [s for s in specs if s not in current]
    removed = [a for s, a in current.items() if s not in wanted]
    require_broad_rights_authority(added, is_top_authority=is_top_authority)

    now = timezone.now()
    for assignment in removed:
        assignment.revoked_at = now
        assignment.revoked_by_user_id = ctx.actor.user_id
        assignment.save(update_fields=["revoked_at", "revoked_by_user_id"])
    assign(employment, added, ctx.actor.user_id)
    employment.version += 1
    employment.save(update_fields=["version"])
    ctx.audit(
        "employee.rights_changed",
        object_type="employment",
        object_id=employment.id,
        before={"rights": [s.as_dict() for s in current]},
        after={
            "rights": [s.as_dict() for s in specs],
            "added": [s.as_dict() for s in added],
            "revoked": [str(a.id) for a in removed],
        },
    )
    return employment


# --- Permission check (A08, § 23.3.8) --------------------------------------------------------


def _covers_account(assignment: PermissionAssignment, account: finance.AccountPlace) -> bool:
    """«All» covers the accounts of the locations in scope, and accounts shared by the company only
    when the scope is the whole company; otherwise the account must be listed (decision 22.10.2026,
    § 23.1.11–23.1.12)."""
    if not assignment.all_cash_accounts:
        return account.id in {x.cash_account_id for x in assignment.cash_accounts.all()}
    if account.location_id is None:
        return assignment.all_locations
    return _in_scope(assignment, account.location_id, None)


def _in_scope(
    assignment: PermissionAssignment, location_id: UUID | None, context_id: UUID | None
) -> bool:
    if location_id is not None and not assignment.all_locations:
        if location_id not in {x.id for x in assignment.locations.all()}:
            return False
    if context_id is not None and not assignment.all_contexts:
        if context_id not in {x.id for x in assignment.contexts.all()}:
            return False
    return True


class RightsResolver:
    """Checks a permission against the current assignments of the actor's employment.

    Without a location the check asks whether the permission is granted anywhere in the company
    and not denied for the whole company; with a location (and context) the assignment's scope must
    cover it. With a cash account (`resource_id`) the account must be in the assignment's scope.
    """

    def has_permission(
        self,
        actor: Actor,
        permission: str,
        *,
        location_id: UUID | None = None,
        context_id: UUID | None = None,
        resource_id: UUID | None = None,
    ) -> bool:
        if actor.company_id is None or actor.employment_id is None:
            return False
        employment = (
            Employment.objects.select_related("position")
            .filter(
                pk=actor.employment_id,
                company_id=actor.company_id,
                state=Employment.State.ACTIVE,
            )
            .first()
        )
        if employment is None:
            return False
        account = None
        if resource_id is not None:
            account = finance.account_place(actor.company_id, resource_id)
            if account is None:
                return False
        if employment.position.is_top_authority:
            return True
        covering = [
            a
            for a in _active_assignments(employment.id)
            if permission in a.permissions
            and _in_scope(a, location_id, context_id)
            and (account is None or _covers_account(a, account))
        ]
        located = location_id is not None or account is not None
        # A denial limited to some locations does not remove the permission elsewhere.
        if any(a.kind == Kind.DENY and (located or a.all_locations) for a in covering):
            return False
        return any(a.kind in (Kind.SET, Kind.GRANT) for a in covering)
