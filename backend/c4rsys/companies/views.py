"""HTTP API of companies (docs/stage1/STAGE1_COMMANDS_API.md, «Компания и настройка»)."""

import re
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, history, locations, ownership, schedules, services
from c4rsys.companies.models import ActivityContext, Company, Location
from c4rsys.companies.permissions import PERMISSIONS
from c4rsys.platform import errors
from c4rsys.platform.access import require_permission
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, Handler, run_command
from c4rsys.platform.http import get_int, get_str, idempotency_key, parse_json, respond
from c4rsys.platform.ids import normalize_public_id
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


COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
MAX_IDENTIFIERS = 10
MAX_PHONES = 10


def _requisites(data: dict[str, Any]) -> dict[str, Any]:
    """Optional profile data of § 3.1.5 present in the request."""
    changes: dict[str, Any] = {}
    for key, max_length in services.REQUISITES.items():
        if key in data:
            changes[key] = get_str(data, key, max_length=max_length, required=False)
    if changes.get("country"):
        changes["country"] = changes["country"].upper()
        if not COUNTRY_RE.match(changes["country"]):
            raise errors.validation_error("country", code="COUNTRY_INVALID")
    if changes.get("email"):
        try:
            validate_email(changes["email"])
        except ValidationError as exc:
            raise errors.validation_error("email", code="EMAIL_INVALID") from exc
        changes["email"] = changes["email"].lower()
    if "identifiers" in data:
        items = data["identifiers"]
        if not isinstance(items, list) or len(items) > MAX_IDENTIFIERS:
            raise errors.validation_error("identifiers")
        identifiers = []
        for item in items:
            if not isinstance(item, dict):
                raise errors.validation_error("identifiers")
            kind = get_str(item, "type", max_length=60, required=False)
            value = get_str(item, "value", max_length=100, required=False)
            if kind or value:
                if not (kind and value):
                    raise errors.validation_error("identifiers", code="IDENTIFIER_INCOMPLETE")
                identifiers.append({"type": kind, "value": value})
        changes["identifiers"] = identifiers
    if "extra_phones" in data:
        # Further numbers of the company, each with a caption of whose it is (decision of
        # 14.11.2026). The caption is optional, the number is not.
        items = data["extra_phones"]
        if not isinstance(items, list) or len(items) > MAX_PHONES:
            raise errors.validation_error("extra_phones")
        phones = []
        for item in items:
            if not isinstance(item, dict):
                raise errors.validation_error("extra_phones")
            number = get_str(item, "number", max_length=40, required=False)
            label = get_str(item, "label", max_length=100, required=False)
            if number or label:
                if not number:
                    raise errors.validation_error("extra_phones", code="PHONE_INCOMPLETE")
                phones.append({"number": number, "label": label})
        changes["extra_phones"] = phones
    return changes


def _file_id(data: dict[str, Any], key: str) -> UUID | None:
    value = get_str(data, key, max_length=36, required=False)
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _company_payload(company: Company, context: services.CompanyContext | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "public_id": company.public_id,
        "display_name": company.display_name,
        "logo": str(company.logo_id) if company.logo_id else None,
        "timezone": company.timezone or None,
        # § 4.1.9: the connected modules; the interface offers only their context types.
        "modules": list(company.modules),
        "main_currency": company.main_currency_id,
        "created_at": company.created_at.isoformat(),
        "version": company.version,
        "setup": services.setup_state(company),
        "setup_completed_at": (
            company.setup_completed_at.isoformat() if company.setup_completed_at else None
        ),
        "context_types": services.context_types(company),
        "deactivated_at": company.deactivated_at.isoformat() if company.deactivated_at else None,
        **{key: getattr(company, key) for key in services.REQUISITES},
        "identifiers": company.identifiers,
        "extra_phones": company.extra_phones,
        "public_fields": list(company.public_fields),
    }
    if context is not None:
        body["employment"] = {
            "id": str(context.employment.id),
            "position_code": context.employment.position.code,
            "position_name": context.employment.position.name,
            "is_top_authority": context.is_top_authority,
            # For the interface to show the owners' pages; the server checks each request.
            "is_owner": ownership.is_owner(company.id, context.employment.user_id)
            if context.employment.user_id
            else False,
            "has_full_rights": access.has_full_rights(context.employment),
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
                "logo": str(c.logo_id) if c.logo_id else None,
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
        logo=_file_id(data, "logo"),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        created = services.create_company(ctx, user, new)
        return CommandResult(201, {"company": _company_payload(created.company, created)})

    payload = {
        "display_name": new.display_name,
        "timezone": new.timezone,
        "main_currency": new.main_currency,
        "logo": str(new.logo) if new.logo else None,
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
    changes: dict[str, Any] = _requisites(data)
    if "display_name" in data:
        changes["display_name"] = get_str(data, "display_name", max_length=DISPLAY_NAME_MAX)
    if "timezone" in data:
        changes["timezone"] = services.validate_timezone(get_str(data, "timezone", max_length=64))
    if "main_currency" in data:
        changes["main_currency"] = get_str(data, "main_currency", max_length=3).upper()
    if "logo" in data:
        logo = _file_id(data, "logo")
        changes["logo"] = str(logo) if logo else ""
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


@require_http_methods(["PUT"])
def public_fields(request: HttpRequest, cmp: str) -> JsonResponse:
    """§ 3.1.8: the top authority or an employee with full administrative access chooses which
    additional profile fields are public."""
    user, session = require_user(request)
    context = services.resolve_company(user, cmp, session.id)
    actor = services.company_actor(user, context, session.id)
    if not access.has_full_rights(context.employment):
        raise errors.permission_denied("company.full_access")
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    fields = data.get("fields")
    if not isinstance(fields, list) or any(f not in services.PUBLIC_FIELDS for f in fields):
        raise errors.validation_error("fields")

    def handler(ctx: CommandContext) -> CommandResult:
        # Fresh check inside the transaction: the rights may have changed meanwhile (§ 23.3.8).
        employment = services.resolve_company(user, cmp, session.id).employment
        if not access.has_full_rights(employment):
            raise errors.permission_denied("company.full_access")
        updated = services.set_public_fields(ctx, context.company.id, fields, expected_version)
        return CommandResult(200, {"company": _company_payload(updated, context)})

    payload = {"fields": sorted(fields), "expected_version": expected_version}
    return run_and_respond(request, "company.public_fields", actor, payload, handler)


@require_GET
def profile_history(request: HttpRequest, cmp: str) -> JsonResponse:
    """§ 3.5.2: who changed the profile, when, which fields from which values to which. Shown to
    those who may edit the profile (PROPOSAL)."""
    user, session = require_user(request)
    context = services.resolve_company(user, cmp, session.id)
    actor = services.company_actor(user, context, session.id)
    require_permission(access.RightsResolver(), actor, "company.settings")
    events = services.profile_history(context.company)
    return JsonResponse({"history": history.payload(events)})


@require_GET
def company_lookup(request: HttpRequest) -> JsonResponse:
    """§ 3.5.3: search by the exact company ID shows only the minimal card and the fields the
    company made public, so the initiator can check the recipient of a request."""
    require_user(request)
    public_id = normalize_public_id(request.GET.get("id", "")[:64])
    company = (
        services.find_company(public_id)
        if public_id is not None and public_id.startswith("CMP-")
        else None
    )
    if company is None:
        raise errors.not_found()
    return JsonResponse(
        {"company": ownership.search_card(ownership.Party(user=None, company=company))}
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
        "schedule": {
            "weekly": schedule.weekly,
            "exceptions": schedule.exceptions,
            "version": schedule.version,
        },
        "contexts": [
            {
                "id": str(c.id),
                "type": c.type,
                "state": c.state,
                "ready": locations.context_ready(c),
                # § 4.2.4: the source of the schedule in force — the location or an own one.
                "schedule": schedules.payload_of_context(c),
            }
            for c in contexts
        ],
    }


def _settings_actor(
    request: HttpRequest, cmp: str, permission: str, location_id: UUID | None = None
) -> tuple[services.CompanyContext, Actor]:
    """Since block 14 the locations have rights of their own (§ 23.5.3): `location.create`,
    `location.edit` (deactivation, restoration and deletion included, PROPOSAL) and
    `location.contexts.manage`, checked in the location concerned."""
    user, session = require_user(request)
    context = services.resolve_company(user, cmp, session.id)
    actor = services.company_actor(user, context, session.id)
    require_permission(access.RightsResolver(), actor, permission, location_id=location_id)
    return context, actor


@require_http_methods(["GET", "POST"])
def location_list(request: HttpRequest, cmp: str) -> JsonResponse:
    """Every employee of the company sees all its locations (decision 22.10.2026)."""
    if request.method == "GET":
        user, session = require_user(request)
        context = services.resolve_company(user, cmp, session.id)
        items = [_location_payload(x) for x in locations.locations_of(context.company.id)]
        return JsonResponse({"locations": items})

    context, actor = _settings_actor(request, cmp, "location.create")
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
    # Contexts chosen at creation, any combination or none (§ 4.1.1, § 4.1.3); created in the
    # same command, so a location never remains without the contexts that were asked for.
    context_types = data.get("contexts", [])
    if (
        not isinstance(context_types, list)
        or any(c not in ActivityContext.Type.values for c in context_types)
        or len(set(context_types)) != len(context_types)
    ):
        raise errors.validation_error("contexts", code="CONTEXT_TYPE_INVALID")

    def handler(ctx: CommandContext) -> CommandResult:
        created = locations.create_location(ctx, context.company, new)
        for context_type in context_types:
            locations.add_context(ctx, context.company.id, created.id, context_type)
        location = locations.get_location(context.company.id, created.id)
        return CommandResult(201, {"location": _location_payload(location)})

    payload = {
        "name": new.name,
        "timezone": new.timezone,
        "address": new.address,
        "contexts": context_types,
    }
    return run_and_respond(request, "location.create", actor, payload, handler)


@require_http_methods(["GET", "PATCH", "DELETE"])
def location_detail(request: HttpRequest, cmp: str, location_id: UUID) -> JsonResponse:
    if request.method == "GET":
        user, session = require_user(request)
        context = services.resolve_company(user, cmp, session.id)
        location = locations.get_location(context.company.id, location_id)
        return JsonResponse({"location": _location_payload(location)})

    if request.method == "DELETE":
        # Only a location without history (decision 23.10.2026); otherwise LOCATION_HAS_HISTORY.
        context, actor = _settings_actor(request, cmp, "location.edit", location_id)
        locations.get_location(context.company.id, location_id)

        def delete(ctx: CommandContext) -> CommandResult:
            locations.delete_location(ctx, context.company.id, location_id)
            return CommandResult(200, {"deleted": str(location_id)})

        target = {"location_id": str(location_id)}
        return run_and_respond(request, "location.delete", actor, target, delete)

    context, actor = _settings_actor(request, cmp, "location.edit", location_id)
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

    payload: dict[str, Any] = {
        "location_id": str(location_id),
        **changes,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "location.update", actor, payload, handler)


@require_http_methods(["POST"])
def location_contexts(request: HttpRequest, cmp: str, location_id: UUID) -> JsonResponse:
    """Adds a context of the system catalog: rental, service or warehouse (§ 4.1.6,
    decision 22.10.2026). A context of a module that is not implemented yet is never ready."""
    context, actor = _settings_actor(request, cmp, "location.contexts.manage", location_id)
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


def _location_state_command(
    request: HttpRequest, cmp: str, location_id: UUID, deactivate: bool
) -> JsonResponse:
    """§ 4.4.1: deactivation and restoration of a location, each journaled."""
    context, actor = _settings_actor(request, cmp, "location.edit", location_id)
    locations.get_location(context.company.id, location_id)
    expected_version = get_int(parse_json(request), "expected_version")
    action = locations.deactivate_location if deactivate else locations.restore_location

    def handler(ctx: CommandContext) -> CommandResult:
        action(ctx, context.company.id, location_id, expected_version)
        location = locations.get_location(context.company.id, location_id)
        return CommandResult(200, {"location": _location_payload(location)})

    payload = {"location_id": str(location_id), "expected_version": expected_version}
    name = "location.deactivate" if deactivate else "location.restore"
    return run_and_respond(request, name, actor, payload, handler)


@require_POST
def location_deactivate(request: HttpRequest, cmp: str, location_id: UUID) -> JsonResponse:
    return _location_state_command(request, cmp, location_id, deactivate=True)


@require_POST
def location_restore(request: HttpRequest, cmp: str, location_id: UUID) -> JsonResponse:
    return _location_state_command(request, cmp, location_id, deactivate=False)


def _context_state_command(
    request: HttpRequest, cmp: str, location_id: UUID, context_id: UUID, enabled: bool
) -> JsonResponse:
    """§ 4.4.2: a context is switched off without deletion and switched on again."""
    context, actor = _settings_actor(request, cmp, "location.contexts.manage", location_id)
    locations.get_location(context.company.id, location_id)

    def handler(ctx: CommandContext) -> CommandResult:
        locations.set_context_enabled(ctx, context.company.id, location_id, context_id, enabled)
        location = locations.get_location(context.company.id, location_id)
        return CommandResult(200, {"location": _location_payload(location)})

    payload = {"location_id": str(location_id), "context_id": str(context_id)}
    name = "location.enable_context" if enabled else "location.disable_context"
    return run_and_respond(request, name, actor, payload, handler)


@require_POST
def context_disable(
    request: HttpRequest, cmp: str, location_id: UUID, context_id: UUID
) -> JsonResponse:
    return _context_state_command(request, cmp, location_id, context_id, enabled=False)


@require_POST
def context_enable(
    request: HttpRequest, cmp: str, location_id: UUID, context_id: UUID
) -> JsonResponse:
    return _context_state_command(request, cmp, location_id, context_id, enabled=True)


# --- Schedules (§ 4.2.3, § 4.2.4, § 22.1.6; block 16 B) -----------------------------------------


def _schedule_body(data: dict[str, Any]) -> tuple[dict[str, list[dict[str, str]]], list[Any]]:
    return (
        schedules.validate_weekly(data.get("weekly")),
        schedules.validate_exceptions(data.get("exceptions", [])),
    )


@require_http_methods(["GET", "PUT"])
def location_schedule(request: HttpRequest, cmp: str, location_id: UUID) -> JsonResponse:
    """GET — the conflicts of future operations with the schedules in force; PUT — a new weekly
    schedule and date exceptions `{weekly, exceptions, expected_version}`. Both by the right
    «location.edit» in the location (PROPOSAL: the conflicts name bookings and service records)."""
    context, actor = _settings_actor(request, cmp, "location.edit", location_id)
    location = locations.get_location(context.company.id, location_id)
    if request.method == "GET":
        return JsonResponse({"conflicts": schedules.conflicts(location)})
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    weekly, exceptions = _schedule_body(data)

    def handler(ctx: CommandContext) -> CommandResult:
        found = schedules.set_location_schedule(
            ctx, context.company.id, location_id, weekly, exceptions, expected_version
        )
        location = locations.get_location(context.company.id, location_id)
        return CommandResult(200, {"location": _location_payload(location), "conflicts": found})

    payload = {
        "location_id": str(location_id),
        "weekly": weekly,
        "exceptions": exceptions,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "location.set_schedule", actor, payload, handler)


@require_http_methods(["PUT", "DELETE"])
def context_schedule(
    request: HttpRequest, cmp: str, location_id: UUID, context_id: UUID
) -> JsonResponse:
    """PUT — an own schedule of the context `{weekly, exceptions, expected_version}`; DELETE —
    «restore inheritance» `{expected_version}` (§ 4.2.4). `expected_version` is 0 while the
    context inherits. Right «location.edit» in the location (PROPOSAL)."""
    context, actor = _settings_actor(request, cmp, "location.edit", location_id)
    locations.get_location(context.company.id, location_id)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    body = _schedule_body(data) if request.method == "PUT" else None

    def handler(ctx: CommandContext) -> CommandResult:
        found = schedules.set_context_schedule(
            ctx, context.company.id, location_id, context_id, body, expected_version
        )
        location = locations.get_location(context.company.id, location_id)
        return CommandResult(200, {"location": _location_payload(location), "conflicts": found})

    payload: dict[str, Any] = {
        "location_id": str(location_id),
        "context_id": str(context_id),
        "schedule": {"weekly": body[0], "exceptions": body[1]} if body else None,
        "expected_version": expected_version,
    }
    name = "location.set_context_schedule" if body else "location.inherit_context_schedule"
    return run_and_respond(request, name, actor, payload, handler)
