"""HTTP API of cash desks and bank accounts (ТЗ § 17.2.5, § 22.1.7, § 23.1.11–23.1.12)."""

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.finance import payments, services
from c4rsys.finance.models import CashAccount, MoneyMovement
from c4rsys.parties import services as parties
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money

MANAGE = "finance.cash_account.manage"
RECEIVE = "finance.payment.receive"
NAME_MAX = 150


def _account_payload(account: CashAccount) -> dict[str, Any]:
    return {
        "id": str(account.id),
        "name": account.name,
        "type": account.type,
        "currency": account.currency_id,
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
    new = services.NewAccount(
        name=get_str(data, "name", max_length=NAME_MAX),
        type=account_type,
        currency=get_str(data, "currency", max_length=3, required=False).upper(),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        account = services.create_account(ctx, context.company, new)
        # The new account must be within the creator's own access (§ 23.1.11–23.1.12).
        if not access.RightsResolver().has_permission(actor, MANAGE, resource_id=account.id):
            raise errors.permission_denied(MANAGE)
        return CommandResult(201, {"cash_account": _account_payload(account)})

    payload = {
        "name": new.name,
        "type": new.type,
        "currency": new.currency,
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


# --- Payments (§ 12.2.5, § 17.2) ------------------------------------------------------------


def _amount(value: Any, field: str, places: int) -> Decimal:
    """A positive decimal string or integer within the minor units of the currency."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise errors.validation_error(field, code="AMOUNT_INVALID")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise errors.validation_error(field, code="AMOUNT_INVALID") from exc
    exponent = number.as_tuple().exponent
    if not number.is_finite() or number <= 0:
        raise errors.validation_error(field, code="AMOUNT_INVALID")
    if isinstance(exponent, int) and -exponent > places:
        raise errors.validation_error(field, code="AMOUNT_TOO_PRECISE", max_places=places)
    return number


def _uuid(value: Any, field: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(field) from exc


def _movement_payload(movement: MoneyMovement) -> dict[str, Any]:
    places = Currency.objects.get(code=movement.currency_id).minor_units
    return {
        "id": str(movement.id),
        "number": movement.number,
        "cash_account_id": str(movement.cash_account_id),
        "client_id": str(movement.client_id) if movement.client_id else None,
        "amount": str(round_money(movement.amount, places)),
        "currency": movement.currency_id,
        "state": movement.state,
        "method": movement.method,
        "posted_at": movement.posted_at.isoformat() if movement.posted_at else None,
        "allocations": [
            {
                "order_id": str(a.order_id),
                "amount": str(round_money(a.amount, places)),
                "purpose": a.purpose,
                "state": a.state,
            }
            for a in movement.allocations.order_by("created_at")
        ],
    }


@require_POST
def payment_cash(request: HttpRequest, cmp: str) -> JsonResponse:
    """Cash received to an account within the employee's finance.payment.receive scope."""
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    account_id = _uuid(data.get("cash_account_id"), "cash_account_id")
    if not _visible(actor, account_id):
        raise errors.not_found()
    if not access.RightsResolver().has_permission(actor, RECEIVE, resource_id=account_id):
        raise errors.permission_denied(RECEIVE)
    currency_code = get_str(data, "currency", max_length=3).upper()
    currency = Currency.objects.filter(code=currency_code).first()
    if currency is None:
        raise errors.validation_error("currency", code="CURRENCY_INVALID")
    raw_allocations = data.get("allocations", [])
    if not isinstance(raw_allocations, list):
        raise errors.validation_error("allocations")
    allocations = []
    for index, item in enumerate(raw_allocations):
        where = f"allocations[{index}]"
        if not isinstance(item, dict):
            raise errors.validation_error(where)
        allocations.append(
            payments.Allocation(
                order_id=_uuid(item.get("order_id"), f"{where}.order_id"),
                amount=_amount(item.get("amount"), f"{where}.amount", currency.minor_units),
            )
        )
    if len({a.order_id for a in allocations}) != len(allocations):
        raise errors.validation_error("allocations", code="ORDER_DUPLICATED")
    receipt = payments.CashReceipt(
        cash_account_id=account_id,
        client_id=_uuid(data.get("client_id"), "client_id"),
        amount=_amount(data.get("amount"), "amount", currency.minor_units),
        currency=currency.code,
        allocations=tuple(allocations),
        note=get_str(data, "note", max_length=500, required=False),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        movement = payments.receive(ctx, receipt)
        return CommandResult(201, {"payment": _movement_payload(movement)})

    payload = {
        "cash_account_id": str(account_id),
        "client_id": str(receipt.client_id),
        "amount": str(receipt.amount),
        "currency": receipt.currency,
        "allocations": [
            {"order_id": str(a.order_id), "amount": str(a.amount)} for a in receipt.allocations
        ],
        "note": receipt.note,
    }
    return run_and_respond(request, "payment.receive_cash", actor, payload, handler)


@require_http_methods(["GET"])
def payment_list(request: HttpRequest, cmp: str) -> JsonResponse:
    """Payments to accounts visible to the employee."""
    context, actor = _actor(request, cmp)
    movements = MoneyMovement.objects.filter(company=context.company).order_by("-number")[:100]
    items = [_movement_payload(m) for m in movements if _visible(actor, m.cash_account_id)]
    return JsonResponse({"payments": items})


@require_http_methods(["GET"])
def client_balance(request: HttpRequest, cmp: str, client_id: UUID) -> JsonResponse:
    """Settlement balance of the client (§ 6.7.2): positive — advance or overpayment, negative —
    debt. Visible to those who may find the client."""
    context, actor = _actor(request, cmp)
    resolver = access.RightsResolver()
    if not any(resolver.has_permission(actor, p) for p in ("client.find", "client.list")):
        raise errors.permission_denied("client.find")
    parties.get_client(context.company.id, client_id)
    balance = payments.client_balance(context.company.id, client_id)
    places = {c.code: c.minor_units for c in Currency.objects.filter(code__in=balance)}
    return JsonResponse(
        {"balance": {c: str(round_money(v, places[c])) for c, v in sorted(balance.items())}}
    )
