"""HTTP API of cash desks and bank accounts (ТЗ § 17.2.5, § 22.1.7, § 23.1.11–23.1.12)."""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.finance import services
from c4rsys.finance.models import CashAccount
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json

MANAGE = "finance.cash_account.manage"
RECEIVE = "finance.payment.receive"
NAME_MAX = 150


def _account_payload(account: CashAccount) -> dict[str, Any]:
    return {
        "id": str(account.id),
        "name": account.name,
        "type": account.type,
        "currency": account.currency_id,
        "location_id": str(account.location_id) if account.location_id else None,
        "state": account.state,
        "version": account.version,
    }


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _visible(actor: Actor, account_id: UUID) -> bool:
    """An account is visible within the scope of the employee's finance rights (decision
    22.10.2026): managing accounts or receiving payments."""
    resolver = access.RightsResolver()
    return any(resolver.has_permission(actor, p, resource_id=account_id) for p in (MANAGE, RECEIVE))


def _require_manage(actor: Actor, account_id: UUID) -> None:
    if not _visible(actor, account_id):
        raise errors.not_found()
    if not access.RightsResolver().has_permission(actor, MANAGE, resource_id=account_id):
        raise errors.permission_denied(MANAGE)


@require_http_methods(["GET", "POST"])
def account_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        items = [
            _account_payload(a)
            for a in services.accounts_of(context.company.id)
            if _visible(actor, a.id)
        ]
        return JsonResponse({"cash_accounts": items})

    if not access.RightsResolver().has_permission(actor, MANAGE):
        raise errors.permission_denied(MANAGE)
    data = parse_json(request)
    account_type = get_str(data, "type", max_length=10)
    if account_type not in CashAccount.Type.values:
        raise errors.validation_error("type")
    raw_location = data.get("location_id")
    try:
        location_id = UUID(str(raw_location)) if raw_location else None
    except ValueError as exc:
        raise errors.validation_error("location_id") from exc
    new = services.NewAccount(
        name=get_str(data, "name", max_length=NAME_MAX),
        type=account_type,
        currency=get_str(data, "currency", max_length=3, required=False).upper(),
        location_id=location_id,
    )

    def handler(ctx: CommandContext) -> CommandResult:
        account = services.create_account(ctx, context.company, new)
        # The new account must be within the creator's own scope (§ 23.1.11–23.1.12).
        if not access.RightsResolver().has_permission(actor, MANAGE, resource_id=account.id):
            raise errors.permission_denied(MANAGE)
        return CommandResult(201, {"cash_account": _account_payload(account)})

    payload = {
        "name": new.name,
        "type": new.type,
        "currency": new.currency,
        "location_id": str(location_id) if location_id else None,
    }
    return run_and_respond(request, "cash_account.create", actor, payload, handler)


@require_http_methods(["PATCH"])
def account_detail(request: HttpRequest, cmp: str, account_id: UUID) -> JsonResponse:
    """Renaming; currency, type and scope do not change (decision 22.10.2026)."""
    context, actor = _actor(request, cmp)
    _require_manage(actor, account_id)
    data = parse_json(request)
    name = get_str(data, "name", max_length=NAME_MAX)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        account = services.rename_account(
            ctx, context.company.id, account_id, name, expected_version
        )
        return CommandResult(200, {"cash_account": _account_payload(account)})

    payload = {"account_id": str(account_id), "name": name, "expected_version": expected_version}
    return run_and_respond(request, "cash_account.rename", actor, payload, handler)


@require_POST
def account_archive(request: HttpRequest, cmp: str, account_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require_manage(actor, account_id)
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        account = services.archive_account(ctx, context.company.id, account_id, expected_version)
        return CommandResult(200, {"cash_account": _account_payload(account)})

    payload = {"account_id": str(account_id), "expected_version": expected_version}
    return run_and_respond(request, "cash_account.archive", actor, payload, handler)
