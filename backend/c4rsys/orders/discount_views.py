"""HTTP API of discounts (ТЗ § 12.5; decisions З11–З14, В-2)."""

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.orders import discounts, services
from c4rsys.orders.models import Discount
from c4rsys.orders.views import _order_for
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _require(actor: Actor, permission: str) -> None:
    if not access.RightsResolver().has_permission(actor, permission, whole_company=True):
        raise errors.permission_denied(permission)


def _decimal(data: dict[str, Any], key: str) -> Decimal:
    try:
        value = Decimal(str(data.get(key, "")).replace(",", "."))
    except InvalidOperation as exc:
        raise errors.validation_error(key) from exc
    if not value.is_finite() or value <= 0:
        raise errors.validation_error(key)
    return value


@require_http_methods(["GET", "POST"])
def catalog(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        items = [discounts.catalog_payload(d) for d in discounts.catalog_of(context.company.id)]
        return JsonResponse({"discounts": items})
    _require(actor, "company.settings")
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=10)
    scope = get_str(data, "scope", max_length=20)
    if kind not in Discount.Kind.values:
        raise errors.validation_error("kind")
    if scope not in Discount.Scope.values:
        raise errors.validation_error("scope")
    value = _decimal(data, "value")
    if kind == Discount.Kind.PERCENT and value > 100:
        raise errors.validation_error("value", code="DISCOUNT_OVER_100")
    currency = get_str(data, "currency", max_length=3, required=False).upper()
    if kind == Discount.Kind.AMOUNT and not currency:
        raise errors.validation_error("currency", code="FIELD_REQUIRED")
    groups = data.get("client_group_ids") or []
    categories = data.get("goods_categories") or []
    if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        raise errors.validation_error("client_group_ids")
    if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
        raise errors.validation_error("goods_categories")
    if categories and scope != Discount.Scope.GOODS:
        raise errors.validation_error("goods_categories", code="FIELD_NOT_APPLICABLE")
    fields = {
        "name": get_str(data, "name", max_length=150),
        "kind": kind,
        "value": value,
        "currency": currency if kind == Discount.Kind.AMOUNT else "",
        "scope": scope,
        "client_group_ids": groups,
        "goods_categories": categories,
    }

    def handler(ctx: CommandContext) -> CommandResult:
        created = discounts.create_discount(ctx, context.company.id, fields)
        return CommandResult(201, {"discount": discounts.catalog_payload(created)})

    payload = {**fields, "value": str(value)}
    return run_and_respond(request, "discount.create", actor, payload, handler)


@require_POST
def catalog_archive(request: HttpRequest, cmp: str, discount_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "company.settings")
    archived = parse_json(request).get("archived")
    if not isinstance(archived, bool):
        raise errors.validation_error("archived")

    def handler(ctx: CommandContext) -> CommandResult:
        found = discounts.set_archived(ctx, context.company.id, discount_id, archived)
        return CommandResult(200, {"discount": discounts.catalog_payload(found)})

    payload = {"discount_id": str(discount_id), "archived": archived}
    return run_and_respond(request, "discount.archive", actor, payload, handler)


def _order_payload(order: Any, actor: Actor, top: bool) -> dict[str, Any]:
    from c4rsys.orders.services import registry

    # The names of the positions as the modules give them for the order.
    names: dict[str, str] = {}
    for provider in registry.client_view:
        for item in provider(order.company_id, order.id):
            names.update(item.get("names", {}))
    applied = discounts.amounts(order)
    positions = [
        {
            "type": p.type,
            "id": str(p.id),
            "name": names.get(str(p.id), ""),
            "currency": p.currency,
            "base": str(discounts.rounded(p.base, p.currency)),
            "discount": str(
                discounts.rounded(
                    applied[(p.type, p.id)][0] if (p.type, p.id) in applied else 0, p.currency
                )
            ),
            "applied_id": str(applied[(p.type, p.id)][1].id) if (p.type, p.id) in applied else None,
            "applied_target": applied[(p.type, p.id)][1].target
            if (p.type, p.id) in applied
            else None,
        }
        for p in discounts.positions_of(order)
    ]
    limit = discounts.limit_of(actor, top)
    return {
        "positions": positions,
        "applied": [discounts.payload(a) for a in order.discounts.filter(removed_at__isnull=True)],
        "offered": [discounts.catalog_payload(d) for d in discounts.offered(order)],
        "limit": str(limit) if limit is not None else None,
    }


@require_http_methods(["GET", "POST"])
def order_discounts(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        order = services.get_order(context.company.id, order_id)
        if not services.can_view(actor, order):
            raise errors.not_found()
        return JsonResponse(_order_payload(order, actor, context.is_top_authority))
    order = _order_for(actor, context.company.id, order_id, "order.discount")
    data = parse_json(request)
    discount_id = get_str(data, "discount_id", max_length=40)
    position_type = get_str(data, "position_type", max_length=30, required=False)
    raw_position = get_str(data, "position_id", max_length=40, required=False)
    conflict = get_str(data, "conflict", max_length=20, required=False)
    if conflict not in ("", "keep_positions", "remove_positions"):
        raise errors.validation_error("conflict")
    try:
        discount_uuid = UUID(discount_id)
        position_uuid = UUID(raw_position) if raw_position else None
    except ValueError as exc:
        raise errors.validation_error("discount_id") from exc
    limit = discounts.limit_of(actor, context.is_top_authority)

    def handler(ctx: CommandContext) -> CommandResult:
        discounts.apply(
            ctx,
            context.company.id,
            order.id,
            discount_uuid,
            position_type=position_type,
            position_id=position_uuid,
            conflict=conflict,
            limit=limit,
        )
        fresh = services.get_order(context.company.id, order.id)
        return CommandResult(200, _order_payload(fresh, actor, context.is_top_authority))

    payload = {
        "order_id": str(order_id),
        "discount_id": discount_id,
        "position_type": position_type,
        "position_id": raw_position,
        "conflict": conflict,
    }
    return run_and_respond(request, "order.discount_apply", actor, payload, handler)


@require_POST
def order_discount_remove(
    request: HttpRequest, cmp: str, order_id: UUID, applied_id: UUID
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    order = _order_for(actor, context.company.id, order_id, "order.discount")

    def handler(ctx: CommandContext) -> CommandResult:
        discounts.remove(ctx, context.company.id, order.id, applied_id)
        fresh = services.get_order(context.company.id, order.id)
        return CommandResult(200, _order_payload(fresh, actor, context.is_top_authority))

    payload = {"order_id": str(order_id), "applied_id": str(applied_id)}
    return run_and_respond(request, "order.discount_remove", actor, payload, handler)


@require_http_methods(["GET", "PUT"])
def discount_limit(request: HttpRequest, cmp: str, employment_id: UUID) -> JsonResponse:
    """The limit is set together with the right by who manages employees' rights (§ 12.5.5)."""
    context, actor = _actor(request, cmp)
    _require(actor, "employee.rights.manage")
    from c4rsys.companies import staff

    staff.get_employment(context.company.id, employment_id)
    if request.method == "GET":
        found = discounts.get_limit(employment_id)
        return JsonResponse({"percent": str(found) if found is not None else None})
    raw = parse_json(request).get("percent")
    percent = None if raw in (None, "") else _decimal({"percent": raw}, "percent")

    def handler(ctx: CommandContext) -> CommandResult:
        value = discounts.set_limit(ctx, context.company.id, employment_id, percent)
        return CommandResult(200, {"percent": str(value) if value is not None else None})

    payload = {"employment_id": str(employment_id), "percent": str(raw) if raw else None}
    return run_and_respond(request, "employee.discount_limit", actor, payload, handler)
