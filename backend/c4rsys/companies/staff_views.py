"""HTTP API of employees, their rights and invitations (ТЗ § 5.1, гл. 23)."""

import csv
import io
from datetime import datetime
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts import services as accounts
from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, history, orgstructure, rightsets, services, staff
from c4rsys.companies.models import Employment, Invitation, Position, RightsRequest
from c4rsys.companies.permissions import BROAD_PERMISSIONS, BROAD_SETS, PERMISSIONS
from c4rsys.companies.views import run_and_respond
from c4rsys.platform import audit, errors
from c4rsys.platform.access import require_permission
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.platform.middleware import get_request_id

POSITION_NAME_MAX = 150

# The catalog of stage 1 has no separate permission to view employee cards; any of the employee
# management permissions opens them (decision 22.10.2026).
EMPLOYEE_VIEW_PERMISSIONS = (
    "employee.invite",
    "employee.rights.manage",
    "employee.access.block",
    "employee.access.restore",
    "employee.access.terminate",
    "employee.card.edit",
    "employee.positions.manage",
    "org.manage",
    "employee.sets.manage",
)
REASON_MAX = 500
BROAD_ACTION = "grant_broad_rights"


def _company_actor(
    request: HttpRequest, cmp: str, *permissions: str
) -> tuple[services.CompanyContext, Actor]:
    """Active company and actor; the actor needs at least one of `permissions`."""
    user, session = require_user(request)
    context = services.resolve_company(user, cmp, session.id)
    actor = services.company_actor(user, context, session.id)
    resolver = access.RightsResolver()
    if not any(resolver.has_permission(actor, p) for p in permissions):
        raise errors.permission_denied(permissions[0])
    return context, actor


def _first(values: list[str]) -> str | None:
    return values[0] if values else None


def _personal_phone(user: Any) -> str | None:
    """The contact phone of the profile, otherwise the first confirmed phone (PROPOSAL)."""
    return user.contact_phone or _first(accounts.confirmed_phones(user))


def _employee_payload(employment: Employment, *, masked: bool = False) -> dict[str, Any]:
    """`masked`: the list of employees shows the personal contacts masked; the card shows them
    to whoever may view it (§ 24.4.3, PROPOSAL)."""
    user = employment.user
    person = None
    if user is not None:
        email = _first(accounts.confirmed_emails(user)) if employment.shares_email else None
        phone = _personal_phone(user) if employment.shares_phone else None
        if masked:
            email = accounts.mask_contact("email", email) if email else email
            phone = accounts.mask_contact("phone", phone) if phone else phone
        # First and last name always; middle name and email only if the user chose so (§ 5.1.7).
        person = {
            "public_id": user.public_id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "middle_name": user.middle_name if employment.shares_middle_name else None,
            "email": email,
            "photo": str(user.avatar_id) if employment.shares_photo and user.avatar_id else None,
            "phone": phone,
            "contacts_masked": masked,
        }
    last_invitation = employment.invitations.order_by("-created_at").first()
    position = employment.position
    return {
        "id": str(employment.id),
        "state": employment.state,
        "position": {
            "id": str(position.id),
            "code": position.code,
            "name": position.name,
            "is_top_authority": position.is_top_authority,
            "is_dedicated": position.is_top_authority or position.is_owner_position,
        },
        "user": person,
        "started_at": employment.started_at.isoformat() if employment.started_at else None,
        # Work contacts are kept apart from the contacts used to sign in (§ 5.1.7).
        "work_email": employment.work_email,
        "work_phone": employment.work_phone,
        "responsibles": orgstructure.responsibles_payload(employment.id),
        "departments": orgstructure.departments_of(employment.id),
        "termination": {
            "at": employment.terminated_at.isoformat(),
            "reason": employment.termination_reason,
        }
        if employment.terminated_at
        else None,
        "version": employment.version,
        "rights": access.rights_of(employment.id),
        "owner_seat_rights": access.rights_of(employment.id, owner_seat=True),
        # § 23.3.5: especially broad rights waiting for the director's confirmation.
        "rights_requests": [
            rightsets.request_payload(r)
            for r in rightsets.requests_of(employment.company_id, employment_id=employment.id)
            if r.state == RightsRequest.State.PENDING
        ],
        "invitation": staff.invitation_payload(last_invitation, timezone.now())
        if last_invitation
        else None,
    }


# --- Company side ---------------------------------------------------------------------------


@require_GET
def employee_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, _actor = _company_actor(request, cmp, *EMPLOYEE_VIEW_PERMISSIONS)
    items = [_employee_payload(e, masked=True) for e in staff.employments_of(context.company.id)]
    return JsonResponse({"employees": items})


@require_http_methods(["GET", "PATCH"])
def employee_detail(request: HttpRequest, cmp: str, employment_id: UUID) -> JsonResponse:
    """GET: the card (§ 5.1.4). PATCH: position from the handbook and work contacts
    (§ 5.1.5, § 5.1.7) with `employee.card.edit`; a new position added from the card also needs
    `employee.positions.manage` (§ 23.3.9, PROPOSAL)."""
    if request.method == "GET":
        context, _actor = _company_actor(request, cmp, *EMPLOYEE_VIEW_PERMISSIONS)
        employment = staff.get_employment(context.company.id, employment_id)
        return JsonResponse({"employee": _employee_payload(employment)})

    context, actor = _company_actor(request, cmp, "employee.card.edit")
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    position: staff.PositionChoice | None = None
    if data.get("position_id"):
        try:
            position = staff.PositionChoice(position_id=UUID(str(data["position_id"])))
        except ValueError as exc:
            raise errors.validation_error("position_id") from exc
    elif "position_name" in data:
        name = get_str(data, "position_name", max_length=POSITION_NAME_MAX)
        require_permission(access.RightsResolver(), actor, "employee.positions.manage")
        position = staff.PositionChoice(name=name)
    work_email = None
    if "work_email" in data:
        work_email = get_str(data, "work_email", max_length=254, required=False).lower()
        if work_email:
            try:
                validate_email(work_email)
            except ValidationError as exc:
                raise errors.validation_error("work_email", code="EMAIL_INVALID") from exc
    work_phone = (
        get_str(data, "work_phone", max_length=40, required=False) if "work_phone" in data else None
    )
    changes = staff.CardChanges(position=position, work_email=work_email, work_phone=work_phone)
    if changes == staff.CardChanges():
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")

    def handler(ctx: CommandContext) -> CommandResult:
        staff.update_card(ctx, context.company, employment_id, changes, expected_version)
        employment = staff.get_employment(context.company.id, employment_id)
        return CommandResult(200, {"employee": _employee_payload(employment)})

    payload = {
        "employment_id": str(employment_id),
        "position_id": str(position.position_id) if position and position.position_id else None,
        "position_name": position.name if position else None,
        "work_email": work_email,
        "work_phone": work_phone,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "employee.update_card", actor, payload, handler)


@require_POST
def employee_terminate(request: HttpRequest, cmp: str, employment_id: UUID) -> JsonResponse:
    """«Terminate access» (§ 5.3.1), a right of its own (§ 23.3.1)."""
    context, actor = _company_actor(request, cmp, "employee.access.terminate")
    reason = get_str(parse_json(request), "reason", max_length=REASON_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        staff.terminate(ctx, context.company.id, employment_id, reason)
        employment = staff.get_employment(context.company.id, employment_id)
        return CommandResult(200, {"employee": _employee_payload(employment)})

    payload = {"employment_id": str(employment_id), "reason": reason}
    return run_and_respond(request, "employee.terminate", actor, payload, handler)


@require_http_methods(["PUT"])
def employee_responsibles(request: HttpRequest, cmp: str, employment_id: UUID) -> JsonResponse:
    """Replaces the responsibles of an employee: employees or departments (§ 5.2.1, § 5.2.4)."""
    context, actor = _company_actor(request, cmp, "org.manage")
    data = parse_json(request)
    raw = data.get("responsibles")
    if not isinstance(raw, list) or len(raw) > 50:
        raise errors.validation_error("responsibles")
    wanted = [orgstructure.parse_node(x, f"responsibles[{i}]") for i, x in enumerate(raw)]

    def handler(ctx: CommandContext) -> CommandResult:
        orgstructure.set_responsibles(ctx, context.company.id, employment_id, wanted)
        employment = staff.get_employment(context.company.id, employment_id)
        return CommandResult(200, {"employee": _employee_payload(employment)})

    payload = {"employment_id": str(employment_id), "responsibles": [n.as_dict() for n in wanted]}
    return run_and_respond(request, "org.set_responsibles", actor, payload, handler)


# --- Positions handbook (§ 5.1.5) ------------------------------------------------------------


def _position_payload(position: Position, used: int) -> dict[str, Any]:
    return {
        "id": str(position.id),
        "code": position.code,
        "name": position.name,
        "is_dedicated": position.is_top_authority or position.is_owner_position,
        "employees": used,
    }


def _positions_body(company_id: UUID) -> dict[str, Any]:
    used: dict[UUID, int] = {}
    for position_id in (
        Employment.objects.filter(company_id=company_id)
        .exclude(state=Employment.State.TERMINATED)
        .values_list("position_id", flat=True)
    ):
        used[position_id] = used.get(position_id, 0) + 1
    return {
        "positions": [
            _position_payload(p, used.get(p.id, 0)) for p in staff.positions_of(company_id)
        ]
    }


@require_http_methods(["GET", "POST"])
def positions(request: HttpRequest, cmp: str) -> JsonResponse:
    if request.method == "GET":
        context, _actor = _company_actor(request, cmp, *EMPLOYEE_VIEW_PERMISSIONS)
        return JsonResponse(_positions_body(context.company.id))
    context, actor = _company_actor(request, cmp, "employee.positions.manage")
    name = get_str(parse_json(request), "name", max_length=POSITION_NAME_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        staff.create_position(ctx, context.company.id, name)
        return CommandResult(201, _positions_body(context.company.id))

    return run_and_respond(request, "position.create", actor, {"name": name}, handler)


@require_http_methods(["PATCH"])
def position_detail(request: HttpRequest, cmp: str, position_id: UUID) -> JsonResponse:
    context, actor = _company_actor(request, cmp, "employee.positions.manage")
    name = get_str(parse_json(request), "name", max_length=POSITION_NAME_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        staff.rename_position(ctx, context.company.id, position_id, name)
        return CommandResult(200, _positions_body(context.company.id))

    payload = {"position_id": str(position_id), "name": name}
    return run_and_respond(request, "position.rename", actor, payload, handler)


@require_POST
def invitation_create(request: HttpRequest, cmp: str) -> JsonResponse:
    """Invitation by user ID or email (§ 5.1.3). Offering rights additionally needs the right to
    manage rights (§ 23.3.1)."""
    user, _session = require_user(request)
    context, actor = _company_actor(request, cmp, "employee.invite")
    data = parse_json(request)
    rights = access.parse_rights(data.get("rights", []))
    if rights:
        require_permission(access.RightsResolver(), actor, "employee.rights.manage")
    access.validate_rights(context.company.id, rights)
    access.require_not_archived(context.company.id, rights)
    confirmed = (
        _broad_code(user, data, context.is_top_authority)
        if any(access.is_broad(context.company.id, s) for s in rights)
        else False
    )
    access.require_broad_rights_authority(
        context.company.id,
        rights,
        is_top_authority=context.is_top_authority,
        code_confirmed=confirmed,
    )

    raw_position = data.get("position_id")
    if raw_position:
        try:
            choice = staff.PositionChoice(position_id=UUID(str(raw_position)))
        except ValueError as exc:
            raise errors.validation_error("position_id") from exc
    else:
        name = get_str(data, "position_name", max_length=POSITION_NAME_MAX, required=False)
        if not name:
            raise errors.validation_error("position_id", code="POSITION_REQUIRED")
        choice = staff.PositionChoice(name=name)
    target_value = get_str(data, "target")

    def handler(ctx: CommandContext) -> CommandResult:
        target = staff.resolve_target(target_value)
        invitation = staff.invite(ctx, context.company, target, choice, rights)
        return CommandResult(
            201, {"invitation": staff.invitation_payload(invitation, timezone.now())}
        )

    payload = {
        "target": target_value,
        "position_id": str(choice.position_id) if choice.position_id else None,
        "position_name": choice.name,
        "rights": [r.as_dict() for r in rights],
    }
    return run_and_respond(request, "employee.invite", actor, payload, handler)


@require_POST
def invitation_cancel(request: HttpRequest, cmp: str, invitation_id: UUID) -> JsonResponse:
    context, actor = _company_actor(request, cmp, "employee.invite")

    def handler(ctx: CommandContext) -> CommandResult:
        invitation = staff.cancel_invitation(ctx, context.company.id, invitation_id)
        return CommandResult(
            200, {"invitation": staff.invitation_payload(invitation, timezone.now())}
        )

    return run_and_respond(
        request, "employee.cancel_invitation", actor, {"invitation_id": str(invitation_id)}, handler
    )


def _broad_code(user: Any, data: dict[str, Any], is_top_authority: bool) -> bool:
    """The director's own assignment of especially broad rights is confirmed by a one-time code
    (§ 2.1.1, block 14). Without `flow` and `code` the answer asks for them before any command;
    an employee's assignment goes to the confirmation instead (§ 23.3.5)."""
    if not is_top_authority:
        return False
    if not data.get("flow"):
        raise errors.business_rule("BROAD_RIGHTS_CODE_REQUIRED", field="rights")
    accounts.confirm_critical(
        user, BROAD_ACTION, get_str(data, "flow"), get_str(data, "code", max_length=20)
    )
    return True


@require_http_methods(["PUT"])
def employee_rights(request: HttpRequest, cmp: str, employment_id: UUID) -> JsonResponse:
    """Replaces the rights of an employee (§ 23.1.2, § 23.1.13, § 23.3.2–23.3.5). Especially
    broad additions need the director's one-time code or wait for his confirmation."""
    user, _session = require_user(request)
    context, actor = _company_actor(request, cmp, "employee.rights.manage")
    data = parse_json(request)
    rights = access.parse_rights(data.get("rights"))
    expected_version = get_int(data, "expected_version")
    access.validate_rights(context.company.id, rights)
    confirmed = (
        _broad_code(user, data, context.is_top_authority)
        if access.broad_added(context.company.id, employment_id, rights)
        else False
    )

    def handler(ctx: CommandContext) -> CommandResult:
        _employment, pending = access.replace_rights(
            ctx,
            company_id=context.company.id,
            employment_id=employment_id,
            specs=rights,
            expected_version=expected_version,
            is_top_authority=context.is_top_authority,
            code_confirmed=confirmed,
        )
        employment = staff.get_employment(context.company.id, employment_id)
        return CommandResult(
            200,
            {
                "employee": _employee_payload(employment),
                "rights_request": rightsets.request_payload(pending) if pending else None,
            },
        )

    payload = {
        "employment_id": str(employment_id),
        "rights": [r.as_dict() for r in rights],
        "expected_version": expected_version,
        "flow": data.get("flow") if confirmed else None,
    }
    return run_and_respond(request, "employee.change_rights", actor, payload, handler)


# --- Permission sets (§ 23.1.3, § 23.1.5, § 23.3.9; block 14) -----------------------------------


def _codes(data: dict[str, Any], key: str = "permissions") -> list[str] | None:
    if key not in data:
        return None
    value = data[key]
    if (
        not isinstance(value, list)
        or len(value) > len(PERMISSIONS)
        or not all(isinstance(x, str) for x in value)
    ):
        raise errors.validation_error(key)
    return [str(x) for x in value]


def _sets_body(company_id: UUID) -> dict[str, Any]:
    return {"sets": [rightsets.set_payload(s) for s in rightsets.sets_of(company_id)]}


@require_http_methods(["GET", "POST"])
def permission_sets(request: HttpRequest, cmp: str) -> JsonResponse:
    """GET: system and own sets. POST: a new own set — empty, from codes or based on another
    set (§ 23.1.5); it is assigned to nobody (§ 23.1.15)."""
    if request.method == "GET":
        context, _actor = _company_actor(request, cmp, *EMPLOYEE_VIEW_PERMISSIONS)
        return JsonResponse(_sets_body(context.company.id))
    context, actor = _company_actor(request, cmp, "employee.sets.manage")
    data = parse_json(request)
    name = get_str(data, "name", max_length=rightsets.NAME_MAX)
    based_on = get_str(data, "based_on", max_length=40, required=False) or None
    codes = _codes(data)

    def handler(ctx: CommandContext) -> CommandResult:
        created = rightsets.create_set(
            ctx, context.company.id, name=name, based_on=based_on, codes=codes
        )
        return CommandResult(201, {"set": rightsets.set_payload(created)})

    payload = {"name": name, "based_on": based_on, "permissions": codes}
    return run_and_respond(request, "permission_set.create", actor, payload, handler)


@require_http_methods(["PATCH"])
def permission_set_detail(request: HttpRequest, cmp: str, set_id: UUID) -> JsonResponse:
    """Renames an own set or changes its codes; holders keep their copies (§ 23.1.3)."""
    context, actor = _company_actor(request, cmp, "employee.sets.manage")
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    name = get_str(data, "name", max_length=rightsets.NAME_MAX) if "name" in data else None
    codes = _codes(data)

    def handler(ctx: CommandContext) -> CommandResult:
        changed = rightsets.change_set(
            ctx,
            context.company.id,
            set_id,
            name=name,
            codes=codes,
            expected_version=expected_version,
        )
        return CommandResult(200, {"set": rightsets.set_payload(changed)})

    payload = {
        "set_id": str(set_id),
        "name": name,
        "permissions": codes,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "permission_set.change", actor, payload, handler)


def _set_state(request: HttpRequest, cmp: str, set_id: UUID, *, archived: bool) -> JsonResponse:
    context, actor = _company_actor(request, cmp, "employee.sets.manage")
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        found = rightsets.set_archived(
            ctx, context.company.id, set_id, archived=archived, expected_version=expected_version
        )
        return CommandResult(200, {"set": rightsets.set_payload(found)})

    command = "permission_set.archive" if archived else "permission_set.restore"
    payload = {"set_id": str(set_id), "expected_version": expected_version}
    return run_and_respond(request, command, actor, payload, handler)


@require_POST
def permission_set_archive(request: HttpRequest, cmp: str, set_id: UUID) -> JsonResponse:
    return _set_state(request, cmp, set_id, archived=True)


@require_POST
def permission_set_restore(request: HttpRequest, cmp: str, set_id: UUID) -> JsonResponse:
    return _set_state(request, cmp, set_id, archived=False)


@require_GET
def permission_set_holders(request: HttpRequest, cmp: str, ref: str) -> JsonResponse:
    """The holders of a set and what applying the current set would add and remove (§ 23.1.3)."""
    context, actor = _company_actor(request, cmp, "employee.rights.manage")
    return JsonResponse(rightsets.holders(context.company.id, ref[:40], actor.employment_id))


@require_POST
def permission_set_apply(request: HttpRequest, cmp: str, ref: str) -> JsonResponse:
    """Applies the updated set to the chosen holders (§ 23.1.3, § 23.1.15)."""
    user, _session = require_user(request)
    context, actor = _company_actor(request, cmp, "employee.rights.manage")
    data = parse_json(request)
    raw = data.get("employment_ids")
    if not isinstance(raw, list) or not raw or len(raw) > 500:
        raise errors.validation_error("employment_ids")
    try:
        employment_ids = [UUID(str(x)) for x in raw]
    except ValueError as exc:
        raise errors.validation_error("employment_ids") from exc
    ref = ref[:40]
    confirmed = (
        _broad_code(user, data, context.is_top_authority)
        if rightsets.broad_apply(context.company.id, ref, employment_ids)
        else False
    )

    def handler(ctx: CommandContext) -> CommandResult:
        done = rightsets.apply_set(
            ctx,
            context.company.id,
            ref,
            employment_ids,
            is_top_authority=context.is_top_authority,
            code_confirmed=confirmed,
        )
        return CommandResult(
            200,
            {
                "applied": done.applied,
                "rights_requests": [rightsets.request_payload(r) for r in done.requests],
                **rightsets.holders(context.company.id, ref, actor.employment_id),
            },
        )

    payload = {
        "set": ref,
        "employment_ids": sorted(map(str, employment_ids)),
        "flow": data.get("flow") if confirmed else None,
    }
    return run_and_respond(request, "permission_set.apply", actor, payload, handler)


# --- Confirmation of especially broad rights (§ 23.3.5) -----------------------------------------


@require_GET
def rights_requests(request: HttpRequest, cmp: str) -> JsonResponse:
    """Requests of especially broad rights; `?state=pending` for the waiting ones."""
    context, _actor = _company_actor(request, cmp, *EMPLOYEE_VIEW_PERMISSIONS)
    state = request.GET.get("state", "")
    items = [
        rightsets.request_payload(r)
        for r in rightsets.requests_of(context.company.id)
        if not state or r.state == state
    ]
    return JsonResponse({"requests": items})


def _top_actor(request: HttpRequest, cmp: str) -> tuple[Any, services.CompanyContext, Actor]:
    """Only the acting top-authority director confirms or declines (§ 23.3.5)."""
    user, session = require_user(request)
    context = services.resolve_company(user, cmp, session.id)
    actor = services.company_actor(user, context, session.id)
    if not context.is_top_authority:
        raise errors.business_rule("RIGHTS_CONFIRMATION_REQUIRES_TOP_AUTHORITY")
    return user, context, actor


@require_POST
def rights_request_confirm(request: HttpRequest, cmp: str, request_id: UUID) -> JsonResponse:
    user, context, actor = _top_actor(request, cmp)
    data = parse_json(request)
    accounts.confirm_critical(
        user, BROAD_ACTION, get_str(data, "flow"), get_str(data, "code", max_length=20)
    )

    def handler(ctx: CommandContext) -> CommandResult:
        found = rightsets.confirm(ctx, context.company.id, request_id)
        return CommandResult(200, {"request": rightsets.request_payload(found)})

    payload = {"request_id": str(request_id), "flow": get_str(data, "flow")}
    return run_and_respond(request, "employee.confirm_rights", actor, payload, handler)


@require_POST
def rights_request_decline(request: HttpRequest, cmp: str, request_id: UUID) -> JsonResponse:
    _user, context, actor = _top_actor(request, cmp)
    note = get_str(parse_json(request), "reason", max_length=REASON_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        found = rightsets.decline(ctx, context.company.id, request_id, note)
        return CommandResult(200, {"request": rightsets.request_payload(found)})

    payload = {"request_id": str(request_id), "reason": note}
    return run_and_respond(request, "employee.decline_rights", actor, payload, handler)


@require_POST
def rights_request_cancel(request: HttpRequest, cmp: str, request_id: UUID) -> JsonResponse:
    context, actor = _company_actor(request, cmp, "employee.rights.manage")

    def handler(ctx: CommandContext) -> CommandResult:
        found = rightsets.cancel(ctx, context.company.id, request_id)
        return CommandResult(200, {"request": rightsets.request_payload(found)})

    payload = {"request_id": str(request_id)}
    return run_and_respond(request, "employee.cancel_rights_request", actor, payload, handler)


def _set_blocked(
    request: HttpRequest, cmp: str, employment_id: UUID, *, blocked: bool
) -> JsonResponse:
    permission = "employee.access.block" if blocked else "employee.access.restore"
    context, actor = _company_actor(request, cmp, permission)

    def handler(ctx: CommandContext) -> CommandResult:
        staff.set_blocked(ctx, context.company.id, employment_id, blocked=blocked)
        employment = staff.get_employment(context.company.id, employment_id)
        return CommandResult(200, {"employee": _employee_payload(employment)})

    command = "employee.block" if blocked else "employee.restore"
    return run_and_respond(request, command, actor, {"employment_id": str(employment_id)}, handler)


@require_POST
def employee_block(request: HttpRequest, cmp: str, employment_id: UUID) -> JsonResponse:
    return _set_blocked(request, cmp, employment_id, blocked=True)


@require_POST
def employee_restore(request: HttpRequest, cmp: str, employment_id: UUID) -> JsonResponse:
    return _set_blocked(request, cmp, employment_id, blocked=False)


# --- Invited user ---------------------------------------------------------------------------


def _my_invitation_payload(invitation: Invitation) -> dict[str, Any]:
    return {
        **staff.invitation_payload(invitation, timezone.now()),
        "company": {
            "public_id": invitation.company.public_id,
            "display_name": invitation.company.display_name,
        },
        # Shown before acceptance (§ 5.1.7).
        "data_given": {
            "required": ["first_name", "last_name"],
            "optional": ["middle_name", "photo", "email", "phone"],
        },
    }


@require_GET
def my_invitations(request: HttpRequest) -> JsonResponse:
    user, _session = require_user(request)
    now = timezone.now()
    items = [
        _my_invitation_payload(i)
        for i in staff.invitations_for(user).order_by("-created_at")
        if staff.effective_state(i, now) == Invitation.State.PENDING
    ]
    return JsonResponse({"invitations": items})


def _flag(data: dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise errors.validation_error(key)
    return value


@require_POST
def invitation_accept(request: HttpRequest, invitation_id: UUID) -> JsonResponse:
    user, session = require_user(request)
    data = parse_json(request)
    shared = staff.SharedData(
        middle_name=_flag(data, "share_middle_name"),
        email=_flag(data, "share_email"),
        photo=_flag(data, "share_photo"),
        phone=_flag(data, "share_phone"),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        invitation = staff.accept(ctx, user, invitation_id, shared)
        return CommandResult(200, {"invitation": _my_invitation_payload(invitation)})

    payload = {
        "invitation_id": str(invitation_id),
        "share_middle_name": shared.middle_name,
        "share_email": shared.email,
        "share_photo": shared.photo,
        "share_phone": shared.phone,
    }
    actor = Actor(user_id=user.id, session_id=session.id)
    return run_and_respond(request, "invitation.accept", actor, payload, handler)


@require_POST
def invitation_decline(request: HttpRequest, invitation_id: UUID) -> JsonResponse:
    user, session = require_user(request)

    def handler(ctx: CommandContext) -> CommandResult:
        invitation = staff.decline(ctx, user, invitation_id)
        return CommandResult(200, {"invitation": _my_invitation_payload(invitation)})

    actor = Actor(user_id=user.id, session_id=session.id)
    return run_and_respond(
        request, "invitation.decline", actor, {"invitation_id": str(invitation_id)}, handler
    )


def _moment(value: str, field: str) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise errors.validation_error(field, code="DATETIME_INVALID") from exc
    if moment.tzinfo is None:
        raise errors.validation_error(field, code="DATETIME_WITHOUT_OFFSET")
    return moment


@require_GET
def employee_journal(request: HttpRequest, cmp: str, employment_id: UUID) -> JsonResponse:
    """Journal of the employee's actions in this company with filters by period, section and
    action (§ 23.10.1–23.10.2). A separate permission; money details of financial actions are
    shown only to holders of a finance right (§ 23.10.3, decision 22.10.2026)."""
    context, actor = _company_actor(request, cmp, "employee.journal.view")
    if not history.employment_exists(context.company.id, employment_id):
        raise errors.not_found()
    resolver = access.RightsResolver()
    show_financial = any(
        resolver.has_permission(actor, p)
        for p in ("finance.payment.receive", "finance.cash_account.manage")
    )
    events = history.employee_journal(
        context.company.id,
        employment_id,
        since=_moment(request.GET.get("from", ""), "from"),
        until=_moment(request.GET.get("to", ""), "to"),
        section=request.GET.get("section", "")[:40],
        action=request.GET.get("action", "")[:100],
    )
    return JsonResponse({"journal": history.payload(events, show_financial=show_financial)})


@require_GET
def employee_journal_export(request: HttpRequest, cmp: str, employment_id: UUID) -> HttpResponse:
    """The journal of an employee as CSV (§ 24.4.5: the export needs its own right besides the
    view and keeps the scope of the view). The export itself is journaled before the file."""
    context, actor = _company_actor(request, cmp, "employee.journal.view")
    resolver = access.RightsResolver()
    require_permission(resolver, actor, "employee.journal.export")
    if not history.employment_exists(context.company.id, employment_id):
        raise errors.not_found()
    show_financial = any(
        resolver.has_permission(actor, p)
        for p in ("finance.payment.receive", "finance.cash_account.manage")
    )
    events = history.employee_journal(
        context.company.id,
        employment_id,
        since=_moment(request.GET.get("from", ""), "from"),
        until=_moment(request.GET.get("to", ""), "to"),
        section=request.GET.get("section", "")[:40],
        action=request.GET.get("action", "")[:100],
    )
    rows = history.payload(events, show_financial=show_financial)
    audit.record(
        "employee.journal_exported",
        object_type="employment",
        object_id=employment_id,
        actor=actor,
        request_id=get_request_id(request),
        after={"rows": len(rows), "filters": {k: v for k, v in request.GET.items()}},
    )
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["occurred_at", "actor", "action", "object_type", "object_id", "reason"])
    for row in rows:
        writer.writerow(
            [
                row["occurred_at"],
                row["actor"].get("name") or row["actor"].get("system_process") or "",
                row["action"],
                row["object"]["type"],
                row["object"]["id"],
                row.get("reason") or "",
            ]
        )
    response = HttpResponse(out.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="employee-journal.csv"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
def permission_catalog(request: HttpRequest, cmp: str) -> JsonResponse:
    """System and own sets with their current codes and the permissions by section, for the
    rights editor (§ 23.1.5, § 23.1.14). `broad` — the codes of especially broad rights."""
    context, _actor = _company_actor(request, cmp, *EMPLOYEE_VIEW_PERMISSIONS)
    return JsonResponse(
        {
            **_sets_body(context.company.id),
            "permissions": list(PERMISSIONS),
            "sections": PERMISSIONS,
            "broad": sorted(BROAD_PERMISSIONS),
            "broad_sets": sorted(BROAD_SETS),
        }
    )
