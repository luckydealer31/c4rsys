"""HTTP API of settlements with parties, set-offs, refunds and corrections (ТЗ § 11.2.4,
§ 11.4.2, § 17.2.3, § 17.2.10–17.2.17, § 17.3.3, § 23.2.7, § 23.7.1)."""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.companies.views import run_and_respond
from c4rsys.finance import corrections, offsets, settlements
from c4rsys.finance.models import (
    MoneyMovement,
    Offset,
    PaymentAllocation,
    SettlementEntry,
)
from c4rsys.finance.views import (
    ACCOUNT_RIGHTS,
    INCOME,
    NOTE_MAX,
    PAYOUT,
    RATE,
    VIEW,
    _actor,
    _amount,
    _can,
    _currency,
    _movement_payload,
    _optional_uuid,
    _rate,
    _rounded,
    _uuid,
    _visible,
)
from c4rsys.parties import services as parties
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money
from c4rsys.warehouse import settlements as warehouse

SETTLE = "finance.settlement.manage"
OFFSET = "finance.offset"
CORRECT = "finance.correct"
READ_RIGHTS = (*ACCOUNT_RIGHTS, SETTLE, OFFSET, CORRECT)


def _money(amounts: dict[str, Decimal]) -> dict[str, str]:
    places = {c.code: c.minor_units for c in Currency.objects.filter(code__in=amounts)}
    return {c: str(round_money(v, places.get(c, 2))) for c, v in sorted(amounts.items())}


def _require_any(actor: Actor, rights: tuple[str, ...]) -> None:
    if not any(_can(actor, p) for p in rights):
        raise errors.permission_denied(rights[0])


def _require(actor: Actor, permission: str, account_id: UUID | None = None) -> None:
    if account_id is not None and not _visible(actor, account_id):
        raise errors.not_found()
    if not _can(actor, permission, account_id):
        raise errors.permission_denied(permission)


def _figures(figures: settlements.ClientFigures) -> dict[str, Any]:
    return {
        "balance": _money(figures.balance),
        "available": _money(figures.available),
        "unpaid": _money(figures.unpaid),
    }


def _entry_payload(entry: SettlementEntry) -> dict[str, Any]:
    places = Currency.objects.get(code=entry.currency_id).minor_units
    return {
        "id": str(entry.id),
        "kind": entry.kind,
        "client_id": str(entry.client_id) if entry.client_id else None,
        "counterparty_id": str(entry.counterparty_id) if entry.counterparty_id else None,
        "debtor": entry.debtor,
        "amount": str(round_money(entry.amount, places)),
        "currency": entry.currency_id,
        "as_of": entry.as_of.isoformat(),
        "return_result": entry.return_result or None,
        "cancelled": SettlementEntry.objects.filter(cancels=entry).exists(),
        "left": str(round_money(settlements.opening_left(entry), places))
        if entry.client_id and entry.kind == SettlementEntry.Kind.OPENING
        else None,
        "note": entry.note,
    }


def _offset_payload(offset: Offset) -> dict[str, Any]:
    return {
        "id": str(offset.id),
        "order_id": str(offset.order_id) if offset.order_id else None,
        "opening_id": str(offset.opening_id) if offset.opening_id else None,
        "amount": _rounded(offset.amount, offset.currency_id),
        "currency": offset.currency_id,
        "settled_amount": _rounded(offset.settled_amount, offset.settled_currency_id),
        "settled_currency": offset.settled_currency_id,
        "applied_rate": str(offset.applied_rate) if offset.applied_rate is not None else None,
        "state": offset.state,
        "replaces_id": str(offset.replaces_id) if offset.replaces_id else None,
        "comment": offset.comment,
        "created_at": offset.created_at.isoformat(),
    }


# --- Lists and cards ------------------------------------------------------------------------


@require_http_methods(["GET"])
def settlement_lists(request: HttpRequest, cmp: str) -> JsonResponse:
    """Who owes the company and whom the company owes (§ 17.2.3)."""
    context, actor = _actor(request, cmp)
    _require_any(actor, (VIEW, SETTLE, OFFSET))
    grouped = settlements.lists(context.company.id)
    return JsonResponse(
        {
            "clients": [
                {"id": str(row["id"]), "name": row["name"], **_figures(row["figures"])}
                for row in grouped.get("clients", [])
            ],
            "counterparties": [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "payable": _money(row["payable"].amounts),
                    "incomplete": row["payable"].incomplete,
                }
                for row in grouped.get("counterparties", [])
            ],
        }
    )


@require_http_methods(["GET"])
def client_settlement(request: HttpRequest, cmp: str, client_id: UUID) -> JsonResponse:
    """Available funds, unpaid charges and the balance of the client (§ 17.2.13), with initial
    obligations and set-offs."""
    context, actor = _actor(request, cmp)
    _require_any(actor, (*READ_RIGHTS, "client.find"))
    company_id = context.company.id
    parties.get_client(company_id, client_id)
    entries = SettlementEntry.objects.filter(
        company_id=company_id, client_id=client_id, kind=SettlementEntry.Kind.OPENING
    ).order_by("created_at")
    offset_rows = Offset.objects.filter(company_id=company_id, client_id=client_id).order_by(
        "-created_at"
    )
    return JsonResponse(
        {
            **_figures(settlements.client_figures(company_id, client_id)),
            "openings": [_entry_payload(e) for e in entries],
            "offsets": [_offset_payload(o) for o in offset_rows[:100]],
        }
    )


@require_http_methods(["GET"])
def counterparty_settlement(request: HttpRequest, cmp: str, counterparty_id: UUID) -> JsonResponse:
    """What the company owes the counterparty, its entries and payments (§ 17.2.3)."""
    context, actor = _actor(request, cmp)
    _require_any(actor, (VIEW, SETTLE, PAYOUT))
    company_id = context.company.id
    owed = settlements.payable(company_id, counterparty_id)
    entries = SettlementEntry.objects.filter(
        company_id=company_id, counterparty_id=counterparty_id
    ).exclude(kind=SettlementEntry.Kind.CANCELLATION)
    payments = settlements._supplier_money(company_id, counterparty_id).order_by("-number")[:100]
    settled_returns = set(
        SettlementEntry.objects.filter(
            company_id=company_id, counterparty_id=counterparty_id, stock_operation__isnull=False
        ).values_list("stock_operation_id", flat=True)
    )
    return JsonResponse(
        {
            "payable": _money(owed.amounts),
            "incomplete": owed.incomplete,
            "entries": [_entry_payload(e) for e in entries.order_by("created_at")],
            "payments": [
                _movement_payload(m) for m in payments if _visible(actor, m.cash_account_id)
            ],
            # Purchase receipts and returns of this supplier with the state of settlements.
            "receipts": [
                {
                    "id": str(v.id),
                    "number": v.number,
                    "purchase_id": str(v.purchase_id) if v.purchase_id else None,
                    "currency": v.currency,
                    "amount": _rounded(v.amount, v.currency),
                    "incomplete": v.incomplete,
                    "to_pay": _rounded(
                        settlements.statement(company_id, receipt_id=v.id)["to_pay"], v.currency
                    ),
                }
                for v in warehouse.receipts(company_id, supplier_id=counterparty_id)
            ],
            "returns": [
                {
                    "id": str(v.id),
                    "number": v.number,
                    "receipt_id": str(v.receipt_id),
                    "currency": v.currency,
                    "amount": _rounded(v.amount, v.currency),
                    "incomplete": v.incomplete,
                    "settled": v.id in settled_returns,
                }
                for v in warehouse.returns(company_id, supplier_id=counterparty_id)
            ],
        }
    )


@require_http_methods(["GET"])
def supplier_statement(request: HttpRequest, cmp: str) -> JsonResponse:
    """Settlements of a purchase or a receipt (§ 11.2.4): received, returned, paid, to pay."""
    context, actor = _actor(request, cmp)
    _require_any(actor, (VIEW, SETTLE, PAYOUT, "warehouse.view"))
    purchase_id = _optional_uuid(dict(request.GET.items()), "purchase_id")
    receipt_id = _optional_uuid(dict(request.GET.items()), "receipt_id")
    if (purchase_id is None) == (receipt_id is None):
        raise errors.validation_error("purchase_id", code="TARGET_REQUIRED")
    data = settlements.statement(context.company.id, purchase_id=purchase_id, receipt_id=receipt_id)
    places = Currency.objects.get(code=data["currency"]).minor_units
    return JsonResponse(
        {
            "currency": data["currency"],
            "incomplete": data["incomplete"],
            **{
                key: str(round_money(data[key], places))
                for key in ("received", "returned", "paid", "to_pay")
            },
        }
    )


# --- Initial obligations and returns --------------------------------------------------------


@require_POST
def opening_create(request: HttpRequest, cmp: str) -> JsonResponse:
    """An initial obligation at the start of accounting (§ 17.2.14): a client's debt or positive
    balance, a debt between the company and a counterparty. No money moves."""
    context, actor = _actor(request, cmp)
    _require(actor, SETTLE)
    data = parse_json(request)
    debtor = get_str(data, "debtor", max_length=10)
    if debtor not in SettlementEntry.Debtor.values:
        raise errors.validation_error("debtor")
    client_id = _optional_uuid(data, "client_id")
    counterparty_id = _optional_uuid(data, "counterparty_id")
    if (client_id is None) == (counterparty_id is None):
        raise errors.validation_error("client_id", code="PARTY_REQUIRED")
    currency = _currency(data)
    try:
        as_of = date.fromisoformat(get_str(data, "as_of", max_length=10))
    except ValueError as exc:
        raise errors.validation_error("as_of", code="DATE_INVALID") from exc
    new = settlements.NewOpening(
        debtor=debtor,
        amount=_amount(data.get("amount"), "amount", currency.minor_units),
        currency=currency.code,
        as_of=as_of,
        client_id=client_id,
        counterparty_id=counterparty_id,
        note=get_str(data, "note", max_length=NOTE_MAX, required=False),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        entry = settlements.create_opening(ctx, context.company.id, new)
        return CommandResult(201, {"entry": _entry_payload(entry)})

    payload = {**{k: str(v) for k, v in data.items() if k != "note"}, "note": new.note}
    return run_and_respond(request, "settlement.opening", actor, payload, handler)


@require_POST
def entry_cancel(request: HttpRequest, cmp: str, entry_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, SETTLE)
    note = get_str(parse_json(request), "note", max_length=NOTE_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        entry = settlements.cancel_entry(ctx, context.company.id, entry_id, note)
        return CommandResult(200, {"entry": _entry_payload(entry)})

    payload = {"entry_id": str(entry_id), "note": note}
    return run_and_respond(request, "settlement.cancel", actor, payload, handler)


@require_POST
def return_settle(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    """The financial result of a return to a supplier (§ 11.4.2)."""
    context, actor = _actor(request, cmp)
    _require(actor, SETTLE)
    data = parse_json(request)
    result = get_str(data, "result", max_length=20)
    if result not in SettlementEntry.ReturnResult.values:
        raise errors.validation_error("result")
    raw = data.get("amount")
    amount = None if raw in (None, "") else _amount(raw, "amount", 4)
    note = get_str(data, "note", max_length=NOTE_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        entry = settlements.settle_return(
            ctx, context.company.id, operation_id, result, amount, note
        )
        return CommandResult(201, {"entry": _entry_payload(entry)})

    payload = {
        "operation_id": str(operation_id),
        "result": result,
        "amount": str(amount) if amount is not None else None,
        "note": note,
    }
    return run_and_respond(request, "settlement.return", actor, payload, handler)


# --- Money with suppliers and clients -------------------------------------------------------


@require_POST
def supplier_payment(request: HttpRequest, cmp: str) -> JsonResponse:
    """A payment to a supplier (out, finance.payout) or money back from one (in,
    finance.income.record), linked to purchases and receipts (§ 11.2.4)."""
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    direction = get_str(data, "direction", max_length=3)
    if direction not in MoneyMovement.Direction.values:
        raise errors.validation_error("direction")
    account_id = _uuid(data.get("cash_account_id"), "cash_account_id")
    _require(actor, PAYOUT if direction == MoneyMovement.Direction.OUT else INCOME, account_id)
    currency = _currency(data)
    raw_links = data.get("links", [])
    if not isinstance(raw_links, list):
        raise errors.validation_error("links")
    links = []
    for index, item in enumerate(raw_links):
        where = f"links[{index}]"
        if not isinstance(item, dict):
            raise errors.validation_error(where)
        links.append(
            settlements.SupplierLink(
                amount=_amount(item.get("amount"), f"{where}.amount", currency.minor_units),
                purchase_id=_optional_uuid(item, "purchase_id"),
                receipt_id=_optional_uuid(item, "receipt_id"),
                rate=_rate(item.get("rate"), f"{where}.rate"),
            )
        )
    payment = settlements.SupplierPayment(
        cash_account_id=account_id,
        counterparty_id=_uuid(data.get("counterparty_id"), "counterparty_id"),
        direction=direction,
        amount=_amount(data.get("amount"), "amount", currency.minor_units),
        currency=currency.code,
        article_id=_uuid(data.get("article_id"), "article_id"),
        links=tuple(links),
        note=get_str(data, "note", max_length=NOTE_MAX, required=False),
    )

    may_override = _can(actor, RATE)

    def handler(ctx: CommandContext) -> CommandResult:
        movement = settlements.pay_supplier(
            ctx, context.company.id, payment, may_override_rate=may_override
        )
        return CommandResult(201, {"record": _movement_payload(movement)})

    payload = {
        "cash_account_id": str(account_id),
        "counterparty_id": str(payment.counterparty_id),
        "direction": direction,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "article_id": str(payment.article_id),
        "links": [
            {
                "amount": str(link.amount),
                "purchase_id": str(link.purchase_id) if link.purchase_id else None,
                "receipt_id": str(link.receipt_id) if link.receipt_id else None,
                "rate": str(link.rate) if link.rate is not None else None,
            }
            for link in links
        ],
        "note": payment.note,
    }
    return run_and_respond(request, "supplier.payment", actor, payload, handler)


@require_POST
def client_refund(request: HttpRequest, cmp: str) -> JsonResponse:
    """Available funds paid back to the client, possibly in another currency (§ 17.2.17)."""
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    account_id = _uuid(data.get("cash_account_id"), "cash_account_id")
    _require(actor, PAYOUT, account_id)
    funds_currency = _currency({"currency": data.get("funds_currency")})
    new = settlements.Refund(
        cash_account_id=account_id,
        client_id=_uuid(data.get("client_id"), "client_id"),
        funds_amount=_amount(data.get("funds_amount"), "funds_amount", funds_currency.minor_units),
        funds_currency=funds_currency.code,
        rate=_rate(data.get("rate"), "rate"),
        note=get_str(data, "note", max_length=NOTE_MAX, required=False),
    )
    may_override = _can(actor, RATE)

    def handler(ctx: CommandContext) -> CommandResult:
        movement = settlements.refund(ctx, context.company.id, new, may_override_rate=may_override)
        return CommandResult(201, {"record": _movement_payload(movement)})

    payload = {
        "cash_account_id": str(account_id),
        "client_id": str(new.client_id),
        "funds_amount": str(new.funds_amount),
        "funds_currency": new.funds_currency,
        "rate": str(new.rate) if new.rate is not None else None,
        "note": new.note,
    }
    return run_and_respond(request, "client.refund", actor, payload, handler)


# --- Set-offs (§ 17.2.10, § 17.2.15–17.2.16) ------------------------------------------------


def _confirmed(data: dict[str, Any]) -> Decimal | None:
    raw = data.get("confirmed_debt")
    return None if raw in (None, "") else _amount(raw, "confirmed_debt", 4)


@require_POST
def offset_create(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, OFFSET)
    data = parse_json(request)
    currency = _currency(data)
    new = offsets.NewOffset(
        client_id=_uuid(data.get("client_id"), "client_id"),
        amount=_amount(data.get("amount"), "amount", currency.minor_units),
        currency=currency.code,
        order_id=_optional_uuid(data, "order_id"),
        opening_id=_optional_uuid(data, "opening_id"),
        rate=_rate(data.get("rate"), "rate"),
        comment=get_str(data, "comment", max_length=NOTE_MAX, required=False),
    )
    may_override = _can(actor, RATE)

    def handler(ctx: CommandContext) -> CommandResult:
        offset = offsets.create(ctx, context.company.id, new, may_override_rate=may_override)
        return CommandResult(201, {"offset": _offset_payload(offset)})

    payload = {
        "client_id": str(new.client_id),
        "amount": str(new.amount),
        "currency": new.currency,
        "order_id": str(new.order_id) if new.order_id else None,
        "opening_id": str(new.opening_id) if new.opening_id else None,
        "rate": str(new.rate) if new.rate is not None else None,
        "comment": new.comment,
    }
    return run_and_respond(request, "offset.create", actor, payload, handler)


@require_POST
def offset_cancel(request: HttpRequest, cmp: str, offset_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, OFFSET)
    data = parse_json(request)
    confirmed = _confirmed(data)
    comment = get_str(data, "comment", max_length=NOTE_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        offset = offsets.cancel(
            ctx, context.company.id, offset_id, confirmed_debt=confirmed, comment=comment
        )
        return CommandResult(200, {"offset": _offset_payload(offset)})

    payload = {
        "offset_id": str(offset_id),
        "confirmed_debt": str(confirmed) if confirmed is not None else None,
        "comment": comment,
    }
    return run_and_respond(request, "offset.cancel", actor, payload, handler)


@require_POST
def offset_correct(request: HttpRequest, cmp: str, offset_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, OFFSET)
    data = parse_json(request)
    amount = _amount(data.get("amount"), "amount", 4)
    rate = _rate(data.get("rate"), "rate")
    confirmed = _confirmed(data)
    comment = get_str(data, "comment", max_length=NOTE_MAX, required=False)
    may_override = _can(actor, RATE)

    def handler(ctx: CommandContext) -> CommandResult:
        offset = offsets.correct(
            ctx,
            context.company.id,
            offset_id,
            amount,
            rate,
            confirmed_debt=confirmed,
            may_override_rate=may_override,
            comment=comment,
        )
        return CommandResult(201, {"offset": _offset_payload(offset)})

    payload = {
        "offset_id": str(offset_id),
        "amount": str(amount),
        "rate": str(rate) if rate is not None else None,
        "confirmed_debt": str(confirmed) if confirmed is not None else None,
        "comment": comment,
    }
    return run_and_respond(request, "offset.correct", actor, payload, handler)


# --- Corrections (§ 17.2.11, § 23.2.7) and split of an expense (§ 17.3.3) -------------------


@require_POST
def record_reverse(request: HttpRequest, cmp: str, movement_id: UUID) -> JsonResponse:
    """Cancels a posted record by the opposite one with a reason; the right covers the accounts
    of the record (both of a transfer)."""
    context, actor = _actor(request, cmp)
    movement = MoneyMovement.objects.filter(company_id=context.company.id, pk=movement_id).first()
    if movement is None:
        raise errors.not_found()
    for account_id in corrections.accounts_of(movement):
        _require(actor, CORRECT, account_id)
    reason = get_str(parse_json(request), "reason", max_length=NOTE_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        created = corrections.reverse(ctx, context.company.id, movement_id, reason)
        return CommandResult(201, {"records": [_movement_payload(m) for m in created]})

    payload = {"movement_id": str(movement_id), "reason": reason}
    return run_and_respond(request, "money.reverse", actor, payload, handler)


@require_POST
def allocation_relink(request: HttpRequest, cmp: str, allocation_id: UUID) -> JsonResponse:
    """Removes the link of a payment to an order or moves it to another order (§ 17.2.11)."""
    context, actor = _actor(request, cmp)
    allocation = (
        PaymentAllocation.objects.filter(company_id=context.company.id, pk=allocation_id)
        .select_related("money_movement")
        .first()
    )
    if allocation is None:
        raise errors.not_found()
    _require(actor, CORRECT, allocation.money_movement.cash_account_id)
    data = parse_json(request)
    order_id = _optional_uuid(data, "order_id")
    reason = get_str(data, "reason", max_length=NOTE_MAX)
    rate = _rate(data.get("rate"), "rate")
    may_override = _can(actor, RATE)

    def handler(ctx: CommandContext) -> CommandResult:
        corrections.relink_allocation(
            ctx,
            context.company.id,
            allocation_id,
            order_id,
            reason,
            rate=rate,
            may_override_rate=may_override,
        )
        movement = MoneyMovement.objects.get(pk=allocation.money_movement_id)
        return CommandResult(200, {"payment": _movement_payload(movement)})

    payload = {
        "allocation_id": str(allocation_id),
        "order_id": str(order_id) if order_id else None,
        "reason": reason,
        "rate": str(rate) if rate is not None else None,
    }
    return run_and_respond(request, "payment.relink", actor, payload, handler)


@require_POST
def record_shares(request: HttpRequest, cmp: str, movement_id: UUID) -> JsonResponse:
    """Splits one expense between vehicles and orders (§ 17.3.3)."""
    context, actor = _actor(request, cmp)
    movement = MoneyMovement.objects.filter(company_id=context.company.id, pk=movement_id).first()
    if movement is None:
        raise errors.not_found()
    _require(actor, PAYOUT, movement.cash_account_id)
    data = parse_json(request)
    raw = data.get("shares", [])
    if not isinstance(raw, list):
        raise errors.validation_error("shares")
    places = Currency.objects.get(code=movement.currency_id).minor_units
    shares = []
    for index, item in enumerate(raw):
        where = f"shares[{index}]"
        if not isinstance(item, dict):
            raise errors.validation_error(where)
        shares.append(
            corrections.Share(
                amount=_amount(item.get("amount"), f"{where}.amount", places),
                vehicle_id=_optional_uuid(item, "vehicle_id"),
                order_id=_optional_uuid(item, "order_id"),
            )
        )

    def handler(ctx: CommandContext) -> CommandResult:
        created = corrections.set_shares(ctx, context.company.id, movement_id, shares)
        return CommandResult(
            200,
            {
                "shares": [
                    {
                        "id": str(s.id),
                        "vehicle_id": str(s.vehicle_id) if s.vehicle_id else None,
                        "order_id": str(s.order_id) if s.order_id else None,
                        "amount": str(round_money(s.amount, places)),
                    }
                    for s in created
                ]
            },
        )

    payload = {
        "movement_id": str(movement_id),
        "shares": [
            {
                "amount": str(s.amount),
                "vehicle_id": str(s.vehicle_id) if s.vehicle_id else None,
                "order_id": str(s.order_id) if s.order_id else None,
            }
            for s in shares
        ],
    }
    return run_and_respond(request, "expense.split", actor, payload, handler)


@require_POST
def record_client(request: HttpRequest, cmp: str, movement_id: UUID) -> JsonResponse:
    """Corrects the mistakenly named client of a payment with a reason (§ 17.2.12)."""
    context, actor = _actor(request, cmp)
    movement = MoneyMovement.objects.filter(company_id=context.company.id, pk=movement_id).first()
    if movement is None:
        raise errors.not_found()
    _require(actor, CORRECT, movement.cash_account_id)
    data = parse_json(request)
    client_id = _uuid(data.get("client_id"), "client_id")
    reason = get_str(data, "reason", max_length=NOTE_MAX)
    parties.get_client(context.company.id, client_id)

    def handler(ctx: CommandContext) -> CommandResult:
        corrections.change_client(ctx, context.company.id, movement_id, client_id, reason)
        fresh = MoneyMovement.objects.get(pk=movement_id)
        return CommandResult(200, {"record": _movement_payload(fresh)})

    payload = {"movement_id": str(movement_id), "client_id": str(client_id), "reason": reason}
    return run_and_respond(request, "payment.change_client", actor, payload, handler)
