"""HTTP API of the deposit of a rental (ТЗ § 14.3.3, § 14.7). Everything but the receipt needs
«Manage deposits» (customer's decision of 26.10.2026); a refund also needs the account in scope."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.companies.views import run_and_respond
from c4rsys.finance import deposits
from c4rsys.finance.views import (
    NOTE_MAX,
    RATE,
    _actor,
    _amount,
    _can,
    _rate,
    _uuid,
    _visible,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json

DEPOSIT = "finance.deposit.manage"


def _rental(company_id: UUID, rental_id: UUID) -> Any:
    from c4rsys.rentals import rentals

    return rentals.get_rental(company_id, rental_id)


def _require(actor: Actor, permission: str) -> None:
    if not _can(actor, permission):
        raise errors.permission_denied(permission)


@require_http_methods(["GET"])
def deposit_detail(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if not any(_can(actor, p) for p in ("rental.view", DEPOSIT)):
        raise errors.permission_denied("rental.view")
    return JsonResponse({"deposit": deposits.payload(_rental(context.company.id, rental_id))})


def _action(
    request: HttpRequest,
    cmp: str,
    rental_id: UUID,
    name: str,
    run: Callable[[CommandContext, UUID, dict[str, Any]], Any],
    parse: Callable[[dict[str, Any]], dict[str, Any]],
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, DEPOSIT)
    company_id = context.company.id
    _rental(company_id, rental_id)
    data = parse(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        run(ctx, company_id, data)
        return CommandResult(200, {"deposit": deposits.payload(_rental(company_id, rental_id))})

    payload = {"rental_id": str(rental_id), **{k: str(v) for k, v in data.items()}}
    return run_and_respond(request, f"deposit.{name}", actor, payload, handler)


def _amount_reason(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "amount": _amount(data.get("amount"), "amount", 4),
        "reason": get_str(data, "reason", max_length=NOTE_MAX),
    }


@require_POST
def deposit_to_balance(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    def parse(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "amount": _amount(data.get("amount"), "amount", 4),
            "reason": get_str(data, "reason", max_length=NOTE_MAX, required=False),
        }

    return _action(
        request,
        cmp,
        rental_id,
        "to_balance",
        lambda ctx, c, d: deposits.to_balance(ctx, c, rental_id, d["amount"], d["reason"]),
        parse,
    )


@require_POST
def deposit_hold(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    return _action(
        request,
        cmp,
        rental_id,
        "hold",
        lambda ctx, c, d: deposits.hold(ctx, c, rental_id, d["amount"], d["reason"]),
        _amount_reason,
    )


@require_POST
def deposit_release(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    return _action(
        request,
        cmp,
        rental_id,
        "release",
        lambda ctx, c, d: deposits.release(ctx, c, rental_id, d["amount"], d["reason"]),
        _amount_reason,
    )


@require_POST
def deposit_offset(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    def parse(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "order_id": _uuid(data.get("order_id"), "order_id"),
            "amount": _amount(data.get("amount"), "amount", 4),
        }

    return _action(
        request,
        cmp,
        rental_id,
        "offset",
        lambda ctx, c, d: deposits.offset(ctx, c, rental_id, d["order_id"], d["amount"]),
        parse,
    )


@require_POST
def deposit_waive(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    def parse(data: dict[str, Any]) -> dict[str, Any]:
        return {"reason": get_str(data, "reason", max_length=NOTE_MAX)}

    return _action(
        request,
        cmp,
        rental_id,
        "waive",
        lambda ctx, c, d: deposits.waive(ctx, c, rental_id, d["reason"]),
        parse,
    )


@require_POST
def deposit_refund(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    """Pays the held deposit back from an account in scope (§ 14.7.3)."""
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    account_id = _uuid(data.get("cash_account_id"), "cash_account_id")
    if not _visible(actor, account_id):
        raise errors.not_found()
    if not _can(actor, DEPOSIT, account_id):
        raise errors.permission_denied(DEPOSIT)
    company_id = context.company.id
    _rental(company_id, rental_id)
    refund = deposits.Refund(
        rental_id=rental_id,
        cash_account_id=account_id,
        amount=_amount(data.get("amount"), "amount", 4),
        rate=_rate(data.get("rate"), "rate"),
        vehicle_with_client=data.get("vehicle_with_client") is True,
        note=get_str(data, "note", max_length=NOTE_MAX, required=False),
    )
    may_override = _can(actor, RATE)

    def handler(ctx: CommandContext) -> CommandResult:
        deposits.refund(ctx, company_id, refund, may_override_rate=may_override)
        return CommandResult(200, {"deposit": deposits.payload(_rental(company_id, rental_id))})

    payload = {
        "rental_id": str(rental_id),
        "cash_account_id": str(account_id),
        "amount": str(refund.amount),
        "rate": str(refund.rate) if refund.rate is not None else None,
        "vehicle_with_client": refund.vehicle_with_client,
        "note": refund.note,
    }
    return run_and_respond(request, "deposit.refund", actor, payload, handler)
