"""HTTP API of companies (docs/stage1/STAGE1_COMMANDS_API.md, «Компания и настройка»)."""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, locations, services
from c4rsys.companies.models import ActivityContext, Company, Location
from c4rsys.companies.permissions import PERMISSIONS
from c4rsys.platform import errors
from c4rsys.platform.access import require_permission
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, Handler, run_command
from c4rsys.platform.http import get_int, get_str, idempotency_key, parse_json, respond
from c4rsys.platform.middleware import get_request_id

DISPLAY_NAME_MAX = 200
LOCATION_NAME_MAX = 200
ADDRESS_MAX = 500


def run_and_respond(
    request: HttpRequest, command: str, actor: Actor, payload: dict[str, Any], handler: Handler
) -> JsonResponse:
    return respond(
        run_command(
            command=command,
            actor=actor,
            idempotency_key=idempotency_key(request),
            payload=payload,
            handler=handler,
            request_id=get_request_id(request),
        )
    )


def _company_payload(company: Company, context: services.CompanyContext | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "public_id": company.public_id,
        "display_name": company.display_name,
        "timezone": company.timezone or None,
        "main_currency": company.main_currency_id,
        "created_at": company.created_at.isoformat(),
        "version": company.version,
        "setup": services.setup_state(company),
    }
    if context is not None:
        body["employment"] = {
            "id": str(context.employment.id),
            "position_code": context.employment.position.code,
            "position_name": context.employment.position.name,
            "is_top_authority": context.is_top_authority,
        }
    return body


@require_http_methods(["GET", "POST"])
def companies(request: HttpRequest) -> JsonResponse:
    user, session = require_user(request)
    if request.method == "GET":
        items = [
            {
                "public_id": c.public_id,
                "display_name": c.display_name,
                "setup": services.setup_state(c),
            }
            for c in services.companies_of(user)
        ]
        return JsonResponse({"companies": items})

    data = parse_json(request)
    timezone = get_str(data, "timezone", max_length=64, required=False)
    currency = get_str(data, "main_currency", max_length=3, required=False)
    new = services.NewCompany(
        display_name=get_str(data, "display_name", max_length=DISPLAY_NAME_MAX),
        timezone=services.validate_timezone(timezone) if timezone else "",
        main_currency=currency.upper(),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        created = services.create_company(ctx, user, new)
        return CommandResult(201, {"company": _company_payload(created.company, created)})

    payload = {
        "display_name": new.display_name,
        "timezone": new.timezone,
        "main_currency": new.main_currency,
    }
    return run_and_respond(
        request, "company.create", Actor(user_id=user.id, session_id=session.id), payload, handler
    )


@require_http_methods(["GET", "PATCH"])
def company(request: HttpRequest, cmp: str) -> JsonResponse:
    user, session = require_user(request)
    context = services.resolve_company(user, cmp, session.id)
    actor = services.company_actor(user, context, session.id)
    if request.method == "GET":
        return JsonResponse(
            {
                "company": _company_payload(context.company, context),
                # For the interface to hide unavailable actions; the server still checks every
                # action (STAGE1_SCREENS «Права»). A permission counts if granted anywhere.
                "permissions": sorted(
                    code
                    for code in PERMISSIONS
                    if access.RightsResolver().has_permission(actor, code)
                ),
            }
        )

    require_permission(access.RightsResolver(), actor, "company.settings")

    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    changes: dict[str, str] = {}
    if "display_name" in data:
        changes["display_name"] = get_str(data, "display_name", max_length=DISPLAY_NAME_MAX)
    if "timezone" in data:
        changes["timezone"] = services.validate_timezone(get_str(data, "timezone", max_length=64))
    if "main_currency" in data:
        changes["main_currency"] = get_str(data, "main_currency", max_length=3).upper()
    if not changes:
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")

    def handler(ctx: CommandContext) -> CommandResult:
        updated = services.update_profile(ctx, context.company.id, changes, expected_version)
        return CommandResult(200, {"company": _company_payload(updated, context)})

    return run_and_respond(
        request,
        "company.update_profile",
        actor,
        {**changes, "expected_version": expected_version},
        handler,
    )


# --- Locations (ТЗ гл. 4) -------------------------------------------------------------------


def _location_payload(location: Location) -> dict[str, Any]:
    contexts = sorted(location.contexts.all(), key=lambda c: c.created_at)
    schedule = location.schedule
    return {
        "id": str(location.id),
        "name": location.name,
        "timezone": location.timezone or None,
        "address": location.address,
        "state": locations.location_state(location, contexts),
        "version": location.version,
        "schedule": {"weekly": schedule.weekly, "exceptions": schedule.exceptions},
        "contexts": [
            {
                "id": str(c.id),
                "type": c.type,
                "state": c.state,
                "ready": locations.context_ready(c),
            }
            for c in contexts
        ],
    }


def _settings_actor(request: HttpRequest, cmp: str) -> tuple[services.CompanyContext, Actor]:
    user, session = require_user(request)
    context = services.resolve_company(user, cmp, session.id)
    actor = services.company_actor(user, context, session.id)
    require_permission(access.RightsResolver(), actor, "company.settings")
    return context, actor


@require_http_methods(["GET", "POST"])
def location_list(request: HttpRequest, cmp: str) -> JsonResponse:
    """Every employee of the company sees all its locations (decision 22.10.2026)."""
    if request.method == "GET":
        user, session = require_user(request)
        context = services.resolve_company(user, cmp, session.id)
        items = [_location_payload(x) for x in locations.locations_of(context.company.id)]
        return JsonResponse({"locations": items})

    context, actor = _settings_actor(request, cmp)
    data = parse_json(request)
    timezone = (
        services.validate_timezone(get_str(data, "timezone", max_length=64))
        if "timezone" in data
        else None
    )
    new = locations.NewLocation(
        name=get_str(data, "name", max_length=LOCATION_NAME_MAX),
        timezone=timezone,
        address=get_str(data, "address", max_length=ADDRESS_MAX, required=False),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        created = locations.create_location(ctx, context.company, new)
        location = locations.get_location(context.company.id, created.id)
        return CommandResult(201, {"location": _location_payload(location)})

    payload = {"name": new.name, "timezone": new.timezone, "address": new.address}
    return run_and_respond(request, "location.create", actor, payload, handler)


@require_http_methods(["GET", "PATCH"])
def location_detail(request: HttpRequest, cmp: str, location_id: UUID) -> JsonResponse:
    if request.method == "GET":
        user, session = require_user(request)
        context = services.resolve_company(user, cmp, session.id)
        location = locations.get_location(context.company.id, location_id)
        return JsonResponse({"location": _location_payload(location)})

    context, actor = _settings_actor(request, cmp)
    locations.get_location(context.company.id, location_id)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    changes: dict[str, str] = {}
    if "name" in data:
        changes["name"] = get_str(data, "name", max_length=LOCATION_NAME_MAX)
    if "timezone" in data:
        # A functioning location needs a time zone (§ 4.2.1); it cannot be cleared.
        changes["timezone"] = services.validate_timezone(get_str(data, "timezone", max_length=64))
    if "address" in data:
        changes["address"] = get_str(data, "address", max_length=ADDRESS_MAX, required=False)
    if not changes:
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")

    def handler(ctx: CommandContext) -> CommandResult:
        locations.update_location(ctx, context.company.id, location_id, changes, expected_version)
        location = locations.get_location(context.company.id, location_id)
        return CommandResult(200, {"location": _location_payload(location)})

    payload = {"location_id": str(location_id), **changes, "expected_version": expected_version}
    return run_and_respond(request, "location.update", actor, payload, handler)


@require_http_methods(["POST"])
def location_contexts(request: HttpRequest, cmp: str, location_id: UUID) -> JsonResponse:
    """Adds a context of the system catalog: rental, service or warehouse (§ 4.1.6,
    decision 22.10.2026). A context of a module that is not implemented yet is never ready."""
    context, actor = _settings_actor(request, cmp)
    locations.get_location(context.company.id, location_id)
    data = parse_json(request)
    context_type = get_str(data, "type", max_length=20)
    if context_type not in ActivityContext.Type.values:
        raise errors.validation_error("type", code="CONTEXT_TYPE_INVALID")

    def handler(ctx: CommandContext) -> CommandResult:
        locations.add_context(ctx, context.company.id, location_id, context_type)
        location = locations.get_location(context.company.id, location_id)
        return CommandResult(201, {"location": _location_payload(location)})

    payload = {"location_id": str(location_id), "type": context_type}
    return run_and_respond(request, "location.add_context", actor, payload, handler)
