"""HTTP API of companies (docs/stage1/STAGE1_COMMANDS_API.md, «Компания и настройка»)."""

from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import services
from c4rsys.companies.models import Company
from c4rsys.platform import errors
from c4rsys.platform.access import require_permission
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.platform.http import get_int, get_str, idempotency_key, parse_json, respond
from c4rsys.platform.middleware import get_request_id

DISPLAY_NAME_MAX = 200


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
    return respond(
        run_command(
            command="company.create",
            actor=Actor(user_id=user.id, session_id=session.id),
            idempotency_key=idempotency_key(request),
            payload=payload,
            handler=handler,
            request_id=get_request_id(request),
        )
    )


@require_http_methods(["GET", "PATCH"])
def company(request: HttpRequest, cmp: str) -> JsonResponse:
    user, session = require_user(request)
    context = services.resolve_company(user, cmp, session.id)
    if request.method == "GET":
        return JsonResponse({"company": _company_payload(context.company, context)})

    actor = services.company_actor(user, context, session.id)
    require_permission(services.PositionAuthorityResolver(), actor, "company.settings")

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

    return respond(
        run_command(
            command="company.update_profile",
            actor=actor,
            idempotency_key=idempotency_key(request),
            payload={**changes, "expected_version": expected_version},
            handler=handler,
            request_id=get_request_id(request),
        )
    )
