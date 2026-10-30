"""HTTP API of employees, their rights and invitations (ТЗ § 5.1, гл. 23)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts import services as accounts
from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, history, services, staff
from c4rsys.companies.models import Employment, Invitation
from c4rsys.companies.permissions import PERMISSIONS, SYSTEM_SETS
from c4rsys.companies.views import run_and_respond
from c4rsys.platform import errors
from c4rsys.platform.access import require_permission
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json

POSITION_NAME_MAX = 150

# The catalog of stage 1 has no separate permission to view employee cards; any of the employee
# management permissions opens them (decision 22.10.2026).
EMPLOYEE_VIEW_PERMISSIONS = (
    "employee.invite",
    "employee.rights.manage",
    "employee.access.block",
    "employee.access.restore",
)


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


def _employee_payload(employment: Employment) -> dict[str, Any]:
    user = employment.user
    person = None
    if user is not None:
        # First and last name always; middle name and email only if the user chose so (§ 5.1.7).
        person = {
            "public_id": user.public_id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "middle_name": user.middle_name if employment.shares_middle_name else None,
            "email": _first(accounts.confirmed_emails(user)) if employment.shares_email else None,
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
        },
        "user": person,
        "started_at": employment.started_at.isoformat() if employment.started_at else None,
        "version": employment.version,
        "rights": access.rights_of(employment.id),
        "owner_seat_rights": access.rights_of(employment.id, owner_seat=True),
        "invitation": staff.invitation_payload(last_invitation, timezone.now())
        if last_invitation
        else None,
    }


# --- Company side ---------------------------------------------------------------------------


@require_GET
def employee_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, _actor = _company_actor(request, cmp, *EMPLOYEE_VIEW_PERMISSIONS)
    items = [_employee_payload(e) for e in staff.employments_of(context.company.id)]
    return JsonResponse({"employees": items})


@require_GET
def employee_detail(request: HttpRequest, cmp: str, employment_id: UUID) -> JsonResponse:
    context, _actor = _company_actor(request, cmp, *EMPLOYEE_VIEW_PERMISSIONS)
    employment = staff.get_employment(context.company.id, employment_id)
    return JsonResponse({"employee": _employee_payload(employment)})


@require_POST
def invitation_create(request: HttpRequest, cmp: str) -> JsonResponse:
    """Invitation by user ID or email (§ 5.1.3). Offering rights additionally needs the right to
    manage rights (§ 23.3.1)."""
    context, actor = _company_actor(request, cmp, "employee.invite")
    data = parse_json(request)
    rights = access.parse_rights(data.get("rights", []))
    if rights:
        require_permission(access.RightsResolver(), actor, "employee.rights.manage")
    access.validate_rights(context.company.id, rights)
    access.require_broad_rights_authority(rights, is_top_authority=context.is_top_authority)

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


@require_http_methods(["PUT"])
def employee_rights(request: HttpRequest, cmp: str, employment_id: UUID) -> JsonResponse:
    """Replaces the rights of an employee (§ 23.1.2, § 23.1.13, § 23.3.2–23.3.5)."""
    context, actor = _company_actor(request, cmp, "employee.rights.manage")
    data = parse_json(request)
    rights = access.parse_rights(data.get("rights"))
    expected_version = get_int(data, "expected_version")
    access.validate_rights(context.company.id, rights)

    def handler(ctx: CommandContext) -> CommandResult:
        access.replace_rights(
            ctx,
            company_id=context.company.id,
            employment_id=employment_id,
            specs=rights,
            expected_version=expected_version,
            is_top_authority=context.is_top_authority,
        )
        employment = staff.get_employment(context.company.id, employment_id)
        return CommandResult(200, {"employee": _employee_payload(employment)})

    payload = {
        "employment_id": str(employment_id),
        "rights": [r.as_dict() for r in rights],
        "expected_version": expected_version,
    }
    return run_and_respond(request, "employee.change_rights", actor, payload, handler)


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
            "optional": ["middle_name", "email"],
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
        middle_name=_flag(data, "share_middle_name"), email=_flag(data, "share_email")
    )

    def handler(ctx: CommandContext) -> CommandResult:
        invitation = staff.accept(ctx, user, invitation_id, shared)
        return CommandResult(200, {"invitation": _my_invitation_payload(invitation)})

    payload = {
        "invitation_id": str(invitation_id),
        "share_middle_name": shared.middle_name,
        "share_email": shared.email,
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
def permission_catalog(request: HttpRequest, cmp: str) -> JsonResponse:
    """System sets and permissions of the stage, for the rights editor (§ 23.1.14)."""
    _company_actor(request, cmp, *EMPLOYEE_VIEW_PERMISSIONS)
    return JsonResponse(
        {
            "sets": [
                {"code": code, "permissions": list(codes)} for code, codes in SYSTEM_SETS.items()
            ],
            "permissions": list(PERMISSIONS),
        }
    )
