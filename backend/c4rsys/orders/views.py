"""HTTP API of orders: reading, manual creation, forming, the responsible employee, cancellation,
closing and reopening (ТЗ § 12.1.7, § 12.1.9–12.1.10, § 12.2, § 23.6)."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, history
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.orders import services
from c4rsys.orders.models import Order
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money

LIST_LIMIT = 100


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _visible(actor: Actor, operations: list[services.OperationSummary]) -> bool:
    """An order is visible with order.view in a location of one of its operations; an order
    without located operations needs order.view anywhere in the company."""
    resolver = access.RightsResolver()
    located = {op.location_id for op in operations if op.location_id is not None}
    if not located:
        return resolver.has_permission(actor, "order.view")
    return any(resolver.has_permission(actor, "order.view", location_id=x) for x in located)


def _uuid(data: dict[str, Any], key: str, *, required: bool = True) -> UUID | None:
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise errors.validation_error(key, code="FIELD_REQUIRED")
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _flag(data: dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise errors.validation_error(key)
    return value


def _order_for(actor: Actor, company_id: UUID, order_id: UUID, permission: str) -> Order:
    """A visible order and a right on the whole of it."""
    order = services.get_order(company_id, order_id)
    if not services.can_view(actor, order):
        raise errors.not_found()
    if not services.has_right(actor, order, permission):
        raise errors.permission_denied(permission)
    return order


def _result(order: Order, status: int = 200) -> CommandResult:
    return CommandResult(status, {"order": _payload(order, services.operations_of(order))})


def _money(currency: str, money: services.Money) -> dict[str, str]:
    """Amounts shown with the minor units of the currency (§ 17.1.7)."""
    places = Currency.objects.get(code=currency).minor_units

    def text(value: Decimal) -> str:
        return str(round_money(value, places))

    return {
        "charged": text(money.charged),
        "paid": text(money.paid),
        "to_pay": text(max(money.balance, Decimal(0))),
        "overpaid": text(max(-money.balance, Decimal(0))),
    }


def _amount(currency: str, value: Decimal) -> str:
    return str(round_money(value, Currency.objects.get(code=currency).minor_units))


def _payload(order: Order, operations: list[services.OperationSummary]) -> dict[str, Any]:
    return {
        "id": str(order.id),
        "number": order.number,
        "client_id": str(order.client_id) if order.client_id else None,
        "is_internal": order.is_internal,
        "state": services.display_state(order, operations),
        "responsible_employment_id": str(order.responsible_employment_id)
        if order.responsible_employment_id
        else None,
        "created_at": order.created_at.isoformat(),
        "closed_at": order.closed_at.isoformat() if order.closed_at else None,
        "cancelled_at": order.cancelled_at.isoformat() if order.cancelled_at else None,
        "cancel_reason": order.cancel_reason,
        "version": order.version,
        "operations": [
            {"type": op.type, "id": str(op.id), "state": op.state, "finished": op.finished}
            for op in operations
        ],
        "closing_conditions": services.closing_conditions(operations),
        "amounts": {
            currency: _money(currency, m)
            for currency, m in sorted(services.amounts_of(order).items())
        },
        # Without a client nothing is charged; the cost of the operations is estimated (Р25).
        "estimated": {
            currency: _amount(currency, value)
            for currency, value in sorted(services.estimated_of(order).items())
        },
    }


@require_http_methods(["GET", "POST"])
def order_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "POST":
        return _create(request, context, actor)
    items = []
    found = services.orders_of(context.company.id)
    client = request.GET.get("client_id")
    if client:
        # § 6.1.14: the orders of a client card and of the cards merged into it.
        from c4rsys.parties import services as clients

        try:
            client_id = UUID(client)
        except ValueError as exc:
            raise errors.validation_error("client_id") from exc
        found = found.filter(client_id__in=clients.family_ids(context.company.id, client_id))
    for order in found[:LIST_LIMIT]:
        operations = services.operations_of(order)
        if _visible(actor, operations):
            items.append(_payload(order, operations))
    return JsonResponse({"orders": items})


@require_GET
def order_detail(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    order = services.get_order(context.company.id, order_id)
    operations = services.operations_of(order)
    if not _visible(actor, operations):
        raise errors.not_found()
    return JsonResponse({"order": _payload(order, operations)})


@require_POST
def order_close(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    """§ 12.1.9, § 23.6.5: order.close in the locations of its operations, with debt also
    order.close_with_debt. The operations are finished in their sections by the rights of those
    sections, so closing checks no section rights (decision Р21 of 28.10.2026)."""
    context, actor = _actor(request, cmp)
    order = services.get_order(context.company.id, order_id)
    operations = services.operations_of(order)
    if not _visible(actor, operations):
        raise errors.not_found()
    resolver = access.RightsResolver()
    places = {op.location_id for op in operations}
    if not all(resolver.has_permission(actor, "order.close", location_id=x) for x in places):
        raise errors.permission_denied("order.close")
    may_close_with_debt = all(
        resolver.has_permission(actor, "order.close_with_debt", location_id=x) for x in places
    )
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        closed = services.close_order(
            ctx,
            context.company.id,
            order_id,
            expected_version,
            may_close_with_debt=may_close_with_debt,
        )
        return CommandResult(200, {"order": _payload(closed, services.operations_of(closed))})

    payload = {"order_id": str(order_id), "expected_version": expected_version}
    return run_and_respond(request, "order.close", actor, payload, handler)


@require_POST
def order_reopen(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    """§ 12.2.3: the closing is cancelled with the separate right «Reopen orders»."""
    context, actor = _actor(request, cmp)
    order = services.get_order(context.company.id, order_id)
    operations = services.operations_of(order)
    if not _visible(actor, operations):
        raise errors.not_found()
    resolver = access.RightsResolver()
    places = {op.location_id for op in operations}
    if not all(resolver.has_permission(actor, "order.reopen", location_id=x) for x in places):
        raise errors.permission_denied("order.reopen")
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    reason = get_str(data, "reason", max_length=2000, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        reopened = services.reopen_order(
            ctx, context.company.id, order_id, expected_version, reason
        )
        return CommandResult(200, {"order": _payload(reopened, services.operations_of(reopened))})

    payload = {"order_id": str(order_id), "expected_version": expected_version, "reason": reason}
    return run_and_respond(request, "order.reopen", actor, payload, handler)


@require_GET
def order_history(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    """History tab of the order: the order, its rentals, inspections, charges and payments."""
    context, actor = _actor(request, cmp)
    order = services.get_order(context.company.id, order_id)
    if not _visible(actor, services.operations_of(order)):
        raise errors.not_found()
    events = history.object_history(context.company.id, services.history_objects(order))
    return JsonResponse({"history": history.payload(events)})


def _create(request: HttpRequest, context: companies.CompanyContext, actor: Actor) -> JsonResponse:
    """§ 12.1.7: «Create the order», or «Save as preliminary» (decision З3)."""
    if not access.RightsResolver().has_permission(actor, "order.create"):
        raise errors.permission_denied("order.create")
    data = parse_json(request)
    client_id = _uuid(data, "client_id", required=False)
    internal = _flag(data, "internal")
    preliminary = _flag(data, "preliminary")
    if client_id is not None:
        from c4rsys.parties import services as clients

        clients.get_client(context.company.id, client_id)

    def handler(ctx: CommandContext) -> CommandResult:
        order = services.create_manual(
            ctx,
            company_id=context.company.id,
            client_id=client_id,
            internal=internal,
            preliminary=preliminary,
        )
        return _result(order, 201)

    payload = {
        "client_id": str(client_id) if client_id else None,
        "internal": internal,
        "preliminary": preliminary,
    }
    return run_and_respond(request, "order.create", actor, payload, handler)


@require_POST
def order_form(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    """§ 12.2.2: «Form the order» with «Change orders»; its rental proposals become bookings and
    need «Book» there."""
    context, actor = _actor(request, cmp)
    _order_for(actor, context.company.id, order_id, "order.edit")
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        return _result(services.form_order(ctx, context.company.id, order_id, expected_version))

    payload = {"order_id": str(order_id), "expected_version": expected_version}
    return run_and_respond(request, "order.form", actor, payload, handler)


@require_POST
def order_responsible(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    """§ 12.1.10: the responsible employee is changed with «Change orders» (decision З7)."""
    context, actor = _actor(request, cmp)
    _order_for(actor, context.company.id, order_id, "order.edit")
    data = parse_json(request)
    employment_id = _uuid(data, "employment_id")
    if employment_id is None:
        raise RuntimeError("A required id is given")
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        order = services.set_responsible(
            ctx, context.company.id, order_id, employment_id, expected_version
        )
        return _result(order)

    payload = {
        "order_id": str(order_id),
        "employment_id": str(employment_id),
        "expected_version": expected_version,
    }
    return run_and_respond(request, "order.set_responsible", actor, payload, handler)


@require_POST
def order_cancel(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    """§ 12.2.1: the whole order with the separate right «Cancel orders» (decision З4)."""
    context, actor = _actor(request, cmp)
    _order_for(actor, context.company.id, order_id, "order.cancel")
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    reason = get_str(data, "reason", max_length=2000, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        order = services.cancel_order(ctx, context.company.id, order_id, expected_version, reason)
        return _result(order)

    payload = {"order_id": str(order_id), "expected_version": expected_version, "reason": reason}
    return run_and_respond(request, "order.cancel", actor, payload, handler)
