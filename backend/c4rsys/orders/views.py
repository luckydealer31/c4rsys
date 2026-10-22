"""HTTP API of orders: reading (ТЗ § 12.2.3–12.2.4, § 23.6). Closing comes with T16."""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.orders import services
from c4rsys.orders.models import Order
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor

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
        "version": order.version,
        "operations": [
            {"type": op.type, "id": str(op.id), "state": op.state, "finished": op.finished}
            for op in operations
        ],
        "closing_conditions": services.closing_conditions(operations),
        "amounts": {
            currency: {
                "charged": str(m.charged),
                "paid": str(m.paid),
                "to_pay": str(max(m.balance, 0)),
                "overpaid": str(max(-m.balance, 0)),
            }
            for currency, m in sorted(services.amounts_of(order).items())
        },
    }


@require_GET
def order_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    items = []
    for order in services.orders_of(context.company.id)[:LIST_LIMIT]:
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
