"""HTTP API of finance: accounts and their records, articles, payments (ТЗ гл. 17, § 22.1.7,
§ 23.1.11–23.1.12, § 23.7.1)."""

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.finance import articles, payments, records, services
from c4rsys.finance.models import CashAccount, FinanceArticle, MoneyMovement
from c4rsys.parties import services as parties
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money

MANAGE = "finance.cash_account.manage"
RECEIVE = "finance.payment.receive"
VIEW = "finance.account.view"
INCOME = "finance.income.record"
PAYOUT = "finance.payout"
ARTICLES = "finance.article.manage"
ACCOUNT_RIGHTS = (MANAGE, RECEIVE, VIEW, INCOME, PAYOUT)
NAME_MAX = 150
NOTE_MAX = 500


def _account_payload(
    account: CashAccount, balance: Decimal | None = None, *, with_balance: bool = False
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(account.id),
        "name": account.name,
        "type": account.type,
        "currency": account.currency_id,
        "state": account.state,
        "version": account.version,
    }
    if with_balance:
        places = Currency.objects.get(code=account.currency_id).minor_units
        payload["balance"] = str(round_money(balance or Decimal(0), places))
    return payload


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _visible(actor: Actor, account_id: UUID) -> bool:
    """An account is visible within the scope of the employee's finance rights (decision
    22.10.2026): managing accounts, receiving payments, viewing, recording money."""
    resolver = access.RightsResolver()
    return any(resolver.has_permission(actor, p, resource_id=account_id) for p in ACCOUNT_RIGHTS)


def _can(actor: Actor, permission: str, account_id: UUID | None = None) -> bool:
    return access.RightsResolver().has_permission(actor, permission, resource_id=account_id)


def _require_manage(actor: Actor, account_id: UUID) -> None:
    if not _visible(actor, account_id):
        raise errors.not_found()
    if not access.RightsResolver().has_permission(actor, MANAGE, resource_id=account_id):
        raise errors.permission_denied(MANAGE)


@require_http_methods(["GET", "POST"])
def account_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        # The balance is shown to those who may view the account's money (§ 23.2.7).
        balances = records.balances(context.company.id)
        items = [
            _account_payload(a, balances.get(a.id), with_balance=_can(actor, VIEW, a.id))
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


def _optional_uuid(data: dict[str, Any], field: str) -> UUID | None:
    value = data.get(field)
    return None if value in (None, "") else _uuid(value, field)


def _currency(data: dict[str, Any]) -> Currency:
    code = get_str(data, "currency", max_length=3).upper()
    currency = Currency.objects.filter(code=code).first()
    if currency is None:
        raise errors.validation_error("currency", code="CURRENCY_INVALID")
    return currency


def _movement_payload(movement: MoneyMovement) -> dict[str, Any]:
    places = Currency.objects.get(code=movement.currency_id).minor_units
    return {
        "id": str(movement.id),
        "number": movement.number,
        "kind": movement.kind,
        "direction": movement.direction,
        "cash_account_id": str(movement.cash_account_id),
        "client_id": str(movement.client_id) if movement.client_id else None,
        "article_id": str(movement.article_id) if movement.article_id else None,
        "location_id": str(movement.location_id) if movement.location_id else None,
        "context_id": str(movement.context_id) if movement.context_id else None,
        "vehicle_id": str(movement.vehicle_id) if movement.vehicle_id else None,
        "note": movement.note,
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


# --- Money of an account (§ 17.2.1, § 22.1.7) -----------------------------------------------


@require_http_methods(["GET"])
def account_records(request: HttpRequest, cmp: str, account_id: UUID) -> JsonResponse:
    """Balance and posted records of one account."""
    context, actor = _actor(request, cmp)
    if not _visible(actor, account_id):
        raise errors.not_found()
    if not _can(actor, VIEW, account_id):
        raise errors.permission_denied(VIEW)
    account = services.get_account(context.company.id, account_id)
    balance = records.balances(context.company.id).get(account.id)
    movements = records.movements_of(context.company.id, account.id)[:200]
    return JsonResponse(
        {
            "cash_account": _account_payload(account, balance, with_balance=True),
            "records": [_movement_payload(m) for m in movements],
        }
    )


@require_POST
def account_opening(request: HttpRequest, cmp: str, account_id: UUID) -> JsonResponse:
    """Initial balance: part of setting up the account (§ 22.1.7), so it needs the right to
    manage accounts."""
    context, actor = _actor(request, cmp)
    _require_manage(actor, account_id)
    data = parse_json(request)
    currency = _currency(data)
    amount = _amount(data.get("amount"), "amount", currency.minor_units)

    def handler(ctx: CommandContext) -> CommandResult:
        movement = records.set_opening(ctx, context.company.id, account_id, amount, currency.code)
        return CommandResult(201, {"record": _movement_payload(movement)})

    payload = {"account_id": str(account_id), "amount": str(amount), "currency": currency.code}
    return run_and_respond(request, "cash_account.set_opening", actor, payload, handler)


@require_POST
def money_record(request: HttpRequest, cmp: str) -> JsonResponse:
    """A receipt (`in`, finance.income.record) or payout (`out`, finance.payout) entered by hand,
    in the scope of the chosen account (§ 17.2.7, § 23.7.1–23.7.2)."""
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    direction = get_str(data, "direction", max_length=3)
    if direction not in MoneyMovement.Direction.values:
        raise errors.validation_error("direction")
    account_id = _uuid(data.get("cash_account_id"), "cash_account_id")
    if not _visible(actor, account_id):
        raise errors.not_found()
    permission = INCOME if direction == MoneyMovement.Direction.IN else PAYOUT
    if not _can(actor, permission, account_id):
        raise errors.permission_denied(permission)
    currency = _currency(data)
    record = records.ManualRecord(
        cash_account_id=account_id,
        direction=direction,
        amount=_amount(data.get("amount"), "amount", currency.minor_units),
        currency=currency.code,
        article_id=_uuid(data.get("article_id"), "article_id"),
        client_id=_optional_uuid(data, "client_id"),
        location_id=_optional_uuid(data, "location_id"),
        context_id=_optional_uuid(data, "context_id"),
        vehicle_id=_optional_uuid(data, "vehicle_id"),
        note=get_str(data, "note", max_length=NOTE_MAX, required=False),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        movement = records.record_manual(ctx, context.company.id, record)
        return CommandResult(201, {"record": _movement_payload(movement)})

    def optional(value: UUID | None) -> str | None:
        return str(value) if value else None

    payload = {
        "cash_account_id": str(account_id),
        "direction": direction,
        "amount": str(record.amount),
        "currency": record.currency,
        "article_id": str(record.article_id),
        "client_id": optional(record.client_id),
        "location_id": optional(record.location_id),
        "context_id": optional(record.context_id),
        "vehicle_id": optional(record.vehicle_id),
        "note": record.note,
    }
    return run_and_respond(request, "money.record", actor, payload, handler)


# --- Income and expense articles (§ 17.2.8) -------------------------------------------------


def _article_payload(article: FinanceArticle, used: set[UUID]) -> dict[str, Any]:
    return {
        "id": str(article.id),
        "kind": article.kind,
        # The interface translates a starting article by its code while `name` is empty.
        "code": article.code or None,
        "name": article.name or None,
        "state": article.state,
        "used": article.id in used,
        "version": article.version,
    }


def _require_articles(actor: Actor) -> None:
    if not _can(actor, ARTICLES):
        raise errors.permission_denied(ARTICLES)


@require_http_methods(["GET", "POST"])
def article_list(request: HttpRequest, cmp: str) -> JsonResponse:
    """Articles are seen by those with any finance right; managed with finance.article.manage."""
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not any(_can(actor, p) for p in (*ACCOUNT_RIGHTS, ARTICLES)):
            raise errors.permission_denied(ARTICLES)
        used = articles.used_ids(context.company.id)
        items = [_article_payload(a, used) for a in articles.articles_of(context.company.id)]
        return JsonResponse({"articles": items})

    _require_articles(actor)
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=10)
    if kind not in FinanceArticle.Kind.values:
        raise errors.validation_error("kind")
    name = get_str(data, "name", max_length=NAME_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        article = articles.create_article(ctx, context.company.id, kind, name)
        return CommandResult(201, {"article": _article_payload(article, set())})

    return run_and_respond(
        request, "finance_article.create", actor, {"kind": kind, "name": name}, handler
    )


@require_http_methods(["PATCH", "DELETE"])
def article_detail(request: HttpRequest, cmp: str, article_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require_articles(actor)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    company_id = context.company.id

    if request.method == "DELETE":

        def delete(ctx: CommandContext) -> CommandResult:
            articles.delete_article(ctx, company_id, article_id, expected_version)
            return CommandResult(200, {"deleted": True})

        payload = {"article_id": str(article_id), "expected_version": expected_version}
        return run_and_respond(request, "finance_article.delete", actor, payload, delete)

    name = get_str(data, "name", max_length=NAME_MAX)

    def rename(ctx: CommandContext) -> CommandResult:
        article = articles.rename_article(ctx, company_id, article_id, name, expected_version)
        return CommandResult(200, {"article": _article_payload(article, set())})

    payload = {"article_id": str(article_id), "name": name, "expected_version": expected_version}
    return run_and_respond(request, "finance_article.rename", actor, payload, rename)


def _article_state(request: HttpRequest, cmp: str, article_id: UUID, state: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require_articles(actor)
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        article = articles.set_state(ctx, context.company.id, article_id, state, expected_version)
        used = articles.used_ids(context.company.id)
        return CommandResult(200, {"article": _article_payload(article, used)})

    payload = {"article_id": str(article_id), "state": state, "expected_version": expected_version}
    return run_and_respond(request, f"finance_article.set_{state}", actor, payload, handler)


@require_POST
def article_archive(request: HttpRequest, cmp: str, article_id: UUID) -> JsonResponse:
    return _article_state(request, cmp, article_id, FinanceArticle.State.ARCHIVED)


@require_POST
def article_restore(request: HttpRequest, cmp: str, article_id: UUID) -> JsonResponse:
    return _article_state(request, cmp, article_id, FinanceArticle.State.ACTIVE)
