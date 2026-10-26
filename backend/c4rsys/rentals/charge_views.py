"""HTTP API of free time and manual charges of a rental (ТЗ § 13.3, § 13.5.1; customer's
decisions Т2–Т6 of 27.10.2026)."""

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.companies import access
from c4rsys.companies.views import run_and_respond
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.rentals import free_time, manual_charges, rentals
from c4rsys.rentals.views import _actor, _moment, _rental_payload


def _result(company_id: UUID, rental_id: UUID, status: int = 200) -> CommandResult:
    return CommandResult(
        status, {"rental": _rental_payload(rentals.get_rental(company_id, rental_id))}
    )


def _interval(data: dict[str, Any]) -> dict[str, Any]:
    start = _moment(data, "start", required=True)
    if start is None:
        raise errors.validation_error("start", code="FIELD_REQUIRED")
    units = data.get("units")
    if units in (None, ""):
        units = None
    elif isinstance(units, bool) or not isinstance(units, int) or units < 1:
        raise errors.validation_error("units", code="INTEGER_REQUIRED")
    return {
        "start": start,
        "end": _moment(data, "end", required=False),
        "units": units,
        "comment": get_str(data, "comment", max_length=500, required=False),
    }


def _encoded(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in values.items()}


@require_POST
def free_time_add(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    free_time.require_right(actor, rentals.get_rental(context.company.id, rental_id))
    data = parse_json(request)
    values = _interval(data)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        free_time.add(
            ctx, context.company.id, rental_id, expected_version=expected_version, **values
        )
        return _result(context.company.id, rental_id, 201)

    payload = {
        "rental_id": str(rental_id),
        **_encoded(values),
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.free_time_add", actor, payload, handler)


@require_http_methods(["PATCH"])
def free_time_change(
    request: HttpRequest, cmp: str, rental_id: UUID, interval_id: UUID
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    free_time.require_right(actor, rentals.get_rental(context.company.id, rental_id))
    data = parse_json(request)
    values = _interval(data)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        free_time.change(
            ctx,
            context.company.id,
            rental_id,
            interval_id,
            expected_version=expected_version,
            **values,
        )
        return _result(context.company.id, rental_id)

    payload = {
        "rental_id": str(rental_id),
        "interval_id": str(interval_id),
        **_encoded(values),
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.free_time_change", actor, payload, handler)


@require_POST
def free_time_remove(
    request: HttpRequest, cmp: str, rental_id: UUID, interval_id: UUID
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    free_time.require_right(actor, rentals.get_rental(context.company.id, rental_id))
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        free_time.remove(ctx, context.company.id, rental_id, interval_id, expected_version)
        return _result(context.company.id, rental_id)

    payload = {
        "rental_id": str(rental_id),
        "interval_id": str(interval_id),
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.free_time_remove", actor, payload, handler)


def _require_charge_right(actor: Any, company_id: UUID, rental_id: UUID) -> None:
    rental = rentals.get_rental(company_id, rental_id)
    if not access.RightsResolver().has_permission(
        actor, manual_charges.RIGHT, location_id=rental.issue_location_id
    ):
        raise errors.permission_denied(manual_charges.RIGHT)


@require_POST
def manual_charge_add(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require_charge_right(actor, context.company.id, rental_id)
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=10)
    try:
        amount = Decimal(str(data.get("amount", "")).strip())
    except InvalidOperation as exc:
        raise errors.validation_error("amount", code="AMOUNT_INVALID") from exc
    if not amount.is_finite():
        raise errors.validation_error("amount", code="AMOUNT_INVALID")
    reason = get_str(data, "reason", max_length=500)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        manual_charges.add(
            ctx,
            context.company.id,
            rental_id,
            kind=kind,
            amount=amount,
            reason=reason,
            expected_version=expected_version,
        )
        return _result(context.company.id, rental_id, 201)

    payload = {
        "rental_id": str(rental_id),
        "kind": kind,
        "amount": str(amount),
        "reason": reason,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.manual_charge_add", actor, payload, handler)


@require_POST
def manual_charge_cancel(
    request: HttpRequest, cmp: str, rental_id: UUID, charge_id: UUID
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require_charge_right(actor, context.company.id, rental_id)
    data = parse_json(request)
    reason = get_str(data, "reason", max_length=500)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        manual_charges.cancel(
            ctx, context.company.id, rental_id, charge_id, reason, expected_version
        )
        return _result(context.company.id, rental_id)

    payload = {
        "rental_id": str(rental_id),
        "charge_id": str(charge_id),
        "reason": reason,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.manual_charge_cancel", actor, payload, handler)
