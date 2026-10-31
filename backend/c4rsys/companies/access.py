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
    # For kind=set: the code of a system set or the id of a company's own set (block 14);
    # otherwise a permission code.
    code: str
    locations: frozenset[UUID] | None = None
    contexts: frozenset[UUID] | None = None
    cash_accounts: frozenset[UUID] | None = None

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


# --- Permission sets: system and the company's own (§ 23.1.5, block 14) ------------------------


def resolve_set(company_id: UUID, ref: str) -> PermissionSet | None:
    """A system set by its code or an own set of the company by its id."""
    try:
        own = UUID(ref)
    except ValueError:
        return PermissionSet.objects.filter(is_system=True, code=ref).first()
    return PermissionSet.objects.filter(is_system=False, company_id=company_id, pk=own).first()


def set_ref(permission_set: PermissionSet) -> str:
    return permission_set.code if permission_set.is_system else str(permission_set.id)


def set_codes(permission_set: PermissionSet) -> list[str]:
    return sorted(
        PermissionSetItem.objects.filter(permission_set=permission_set).values_list(
            "permission_id", flat=True
        )
    )


def codes_of(company_id: UUID, spec: RightSpec) -> list[str]:
    """The codes a requested assignment gives now."""
    if spec.kind != Kind.SET:
        return [spec.code]
    found = resolve_set(company_id, spec.code)
    return set_codes(found) if found is not None else []


def is_broad_codes(kind: str, ref: str, codes: list[str] | set[str]) -> bool:
    """Especially broad authority (§ 23.3.5): «Company administrator» or anything that gives a
    code of BROAD_PERMISSIONS (PROPOSAL)."""
    if kind == Kind.DENY:
        return False
    if kind == Kind.SET and ref in BROAD_SETS:
        return True
    return bool(set(codes) & BROAD_PERMISSIONS)


def is_broad(company_id: UUID, spec: RightSpec) -> bool:
    return is_broad_codes(spec.kind, spec.code, codes_of(company_id, spec))


def validate_rights(company_id: UUID, specs: list[RightSpec]) -> None:
    """Codes exist; selected locations, contexts and cash accounts belong to the company. Listed
    contexts belong to the listed locations. Cash accounts are not bound to locations, so any
    account of the company may be listed (§ 23.1.11–23.1.12, clarification of 23.10.2026). A set
    is a system set or an own set of this company (block 14)."""
    for spec in specs:
        if spec.kind == Kind.SET and resolve_set(company_id, spec.code) is None:
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
            if not finance.account_exists(company_id, account_id):
                raise errors.validation_error("rights", code="SCOPE_INVALID")


def require_not_archived(company_id: UUID, specs: list[RightSpec]) -> None:
    """An archived own set is not assigned anew; the assignments made before keep working
    (PROPOSAL)."""
    for spec in specs:
        if spec.kind != Kind.SET:
            continue
        found = resolve_set(company_id, spec.code)
        if found is not None and found.archived_at is not None:
            raise errors.business_rule("PERMISSION_SET_ARCHIVED", field="rights")


def require_broad_rights_authority(
    company_id: UUID, specs: list[RightSpec], *, is_top_authority: bool, code_confirmed: bool
) -> None:
    """For an invitation (block 14, PROPOSAL): especially broad rights are offered only by the
    top-authority director, confirmed by a one-time code (§ 2.1.1); an employee who manages rights
    assigns them after the acceptance, through the confirmation of § 23.3.5."""
    if not any(is_broad(company_id, s) for s in specs):
        return
    if not is_top_authority:
        raise errors.business_rule("BROAD_RIGHTS_REQUIRE_TOP_AUTHORITY", field="rights")
    if not code_confirmed:
        raise errors.business_rule("BROAD_RIGHTS_CODE_REQUIRED", field="rights")


# --- Stored assignments -----------------------------------------------------------------------


def _active_assignments(
    employment_id: UUID, *, ordinary_only: bool = False
) -> list[PermissionAssignment]:
    """`ordinary_only` leaves out the rights of a representative's dedicated position (§ 3.4.1),
    which the ordinary rights editor does not change."""
    query = PermissionAssignment.objects.filter(
        employment_id=employment_id, revoked_at__isnull=True
    )
    if ordinary_only:
        query = query.filter(owner_seat__isnull=True)
    return list(
        query.select_related("permission_set")
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
        code=set_ref(set_) if set_ is not None else assignment.permissions[0],
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


def rights_of(employment_id: UUID, *, owner_seat: bool = False) -> list[dict[str, Any]]:
    """Each assignment with its source, codes and scope (§ 23.1.13). `owner_seat` — the rights a
    representative has through the dedicated position of an owner company (§ 3.4.1), shown apart
    from the ordinary rights the editor changes."""
    result = []
    for a in _active_assignments(employment_id):
        if (a.owner_seat_id is not None) != owner_seat:
            continue
        update = None
        if a.permission_set is not None:
            # § 23.1.3: the set changed since the assignment; applied only by a separate action.
            current = set(set_codes(a.permission_set))
            held = set(a.permissions)
            if current != held:
                update = {"added": sorted(current - held), "removed": sorted(held - current)}
        result.append(
            {
                "id": str(a.id),
                **_spec_of(a).as_dict(),
                "permissions": sorted(a.permissions),
                "granted_at": a.granted_at.isoformat(),
                "owner_seat": str(a.owner_seat_id) if a.owner_seat_id else None,
                "set_update": update,
            }
        )
    return result


def assign(
    employment: Employment,
    specs: list[RightSpec],
    granted_by_user_id: UUID,
    owner_seat_id: UUID | None = None,
) -> list[PermissionAssignment]:
    now = timezone.now()
    created = []
    for spec in specs:
        permission_set = None
        if spec.kind == Kind.SET:
            permission_set = resolve_set(employment.company_id, spec.code)
            if permission_set is None:
                raise errors.validation_error("rights", code="PERMISSION_SET_UNKNOWN")
            codes = set_codes(permission_set)
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
            owner_seat_id=owner_seat_id,
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


def locked_target(ctx: CommandContext, company_id: UUID, employment_id: UUID) -> Employment:
    """The employee whose rights change, locked; never the actor himself (§ 23.3.2)."""
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
    return employment


def require_broad_allowed(actor_user_id: UUID, employment: Employment) -> None:
    """§ 2.3.1: the highest rights are not granted within 72 hours after a recovery through the
    support, neither by the recovered person nor to him (PROPOSAL: both directions)."""
    from c4rsys.accounts import services as accounts

    accounts.require_not_recently_recovered(actor_user_id)
    if employment.user_id is not None:
        accounts.require_not_recently_recovered(employment.user_id)


def broad_added(company_id: UUID, employment_id: UUID, specs: list[RightSpec]) -> bool:
    """Whether saving `specs` adds especially broad rights (read without locks, to ask for the
    one-time code before the command; the command checks again)."""
    current = {_spec_of(a) for a in _active_assignments(employment_id, ordinary_only=True)}
    return any(is_broad(company_id, s) for s in specs if s not in current)


def replace_rights(
    ctx: CommandContext,
    *,
    company_id: UUID,
    employment_id: UUID,
    specs: list[RightSpec],
    expected_version: int,
    is_top_authority: bool,
    code_confirmed: bool = False,
) -> tuple[Employment, Any]:
    """Sets the employee's rights to `specs`. Unchanged assignments are kept; removed ones are
    revoked with history (§ 23.3.3). Own rights cannot be changed (§ 23.3.2).

    Especially broad additions (§ 23.3.5): the top-authority director assigns them himself with a
    one-time code (§ 2.1.1); from another employee they wait for his confirmation as a rights
    request, while the rest of the change takes effect at once (PROPOSAL). Returns the employment
    and the request, if any."""
    from c4rsys.companies import rightsets

    employment = locked_target(ctx, company_id, employment_id)
    if employment.version != expected_version:
        raise errors.version_conflict()

    current = {_spec_of(a): a for a in _active_assignments(employment.id, ordinary_only=True)}
    wanted = set(specs)
    added = [s for s in specs if s not in current]
    removed = [a for s, a in current.items() if s not in wanted]
    require_not_archived(company_id, added)
    broad = [s for s in added if is_broad(company_id, s)]
    request = None
    if broad:
        require_broad_allowed(ctx.actor.user_id, employment)
        if is_top_authority:
            if not code_confirmed:
                raise errors.business_rule("BROAD_RIGHTS_CODE_REQUIRED", field="rights")
        else:
            request = rightsets.open_request(ctx, employment, broad, replaces=[])
            added = [s for s in added if s not in broad]

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
            "rights": [s.as_dict() for s in specs if s not in broad or is_top_authority],
            "added": [s.as_dict() for s in added],
            "revoked": [str(a.id) for a in removed],
            "awaiting_confirmation": str(request.id) if request is not None else None,
            "code_confirmed": bool(broad) and is_top_authority,
        },
    )
    return employment, request


# --- Permission check (A08, § 23.3.8) --------------------------------------------------------


def _covers_account(assignment: PermissionAssignment, account_id: UUID) -> bool:
    """«All» covers every cash account of the company whatever the locations of the assignment;
    otherwise the account must be listed (§ 23.1.11–23.1.12, clarification of 23.10.2026)."""
    if assignment.all_cash_accounts:
        return True
    return account_id in {x.cash_account_id for x in assignment.cash_accounts.all()}


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
    `whole_company` asks for the permission in every location of the company, present and future
    (§ 23.1.9): an assignment for the whole company and no denial anywhere.
    """

    def has_permission(
        self,
        actor: Actor,
        permission: str,
        *,
        location_id: UUID | None = None,
        context_id: UUID | None = None,
        resource_id: UUID | None = None,
        whole_company: bool = False,
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
        if resource_id is not None and not finance.account_exists(actor.company_id, resource_id):
            return False
        if employment.position.is_top_authority:
            return True
        covering = [
            a
            for a in _active_assignments(employment.id)
            if permission in a.permissions
            and _in_scope(a, location_id, context_id)
            and (resource_id is None or _covers_account(a, resource_id))
        ]
        if whole_company:
            if any(a.kind == Kind.DENY for a in covering):
                return False
            return any(a.kind != Kind.DENY and a.all_locations for a in covering)
        located = location_id is not None or resource_id is not None
        # A denial limited to some locations does not remove the permission elsewhere.
        if any(a.kind == Kind.DENY and (located or a.all_locations) for a in covering):
            return False
        return any(a.kind in (Kind.SET, Kind.GRANT) for a in covering)


# --- Full rights and the limit of a representative (§ 3.4.1) ---------------------------------


def has_full_rights(employment: Employment) -> bool:
    """The top-authority director, or an employee with «Company administrator» for the whole
    company, every context and cash account and no personal denial. This reading of «full rights
    of the company» (§ 3.4.1) is a PROPOSAL."""
    if employment.state != Employment.State.ACTIVE:
        return False
    if employment.position.is_top_authority:
        return True
    assignments = _active_assignments(employment.id)
    if any(a.kind == Kind.DENY for a in assignments):
        return False
    return any(
        a.kind == Kind.SET
        and a.permission_set is not None
        and a.permission_set.code in BROAD_SETS
        and a.all_locations
        and a.all_contexts
        and a.all_cash_accounts
        for a in assignments
    )


def _scope_within(inner: frozenset[UUID] | None, outer: frozenset[UUID] | None) -> bool:
    if outer is None:
        return True
    return inner is not None and inner <= outer


def within_limit(
    specs: list[RightSpec], limit: list[RightSpec], company_id: UUID | None = None
) -> bool:
    """Every requested set or permission is in the limit with no wider scope (§ 3.4.1). A
    permission inside a set of the limit counts as in the limit."""
    limit_sets = {spec.code: spec for spec in limit if spec.kind == Kind.SET}
    members: dict[str, set[str]] = {}
    for code, spec in limit_sets.items():
        members[code] = set(codes_of(company_id, spec)) if company_id else set()
    for spec in specs:
        if spec.kind == Kind.DENY:
            return False
        candidates = [x for x in limit if x.kind == spec.kind and x.code == spec.code]
        if spec.kind == Kind.GRANT:
            candidates += [limit_sets[c] for c, codes in members.items() if spec.code in codes]
        if not any(
            _scope_within(spec.locations, x.locations)
            and _scope_within(spec.contexts, x.contexts)
            and _scope_within(spec.cash_accounts, x.cash_accounts)
            for x in candidates
        ):
            return False
    return True


def seat_assignments(employment_id: UUID, seat_id: UUID) -> list[PermissionAssignment]:
    return [a for a in _active_assignments(employment_id) if a.owner_seat_id == seat_id]


def revoke(assignments: list[PermissionAssignment], by_user_id: UUID) -> None:
    now = timezone.now()
    for assignment in assignments:
        assignment.revoked_at = now
        assignment.revoked_by_user_id = by_user_id
        assignment.save(update_fields=["revoked_at", "revoked_by_user_id"])


def spec_of(assignment: PermissionAssignment) -> RightSpec:
    return _spec_of(assignment)
