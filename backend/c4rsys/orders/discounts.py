"""Discounts of an order (ТЗ § 12.5; decisions З11–З14, В-2).

* The company keeps a catalog of discounts: a percent or a fixed amount with its currency, a
  scope (the whole order, rental, additional services, service, goods), optionally only for some
  client groups and, for goods, some categories of the catalog of parts.
* A discount is applied to one position or to the order. Discounts never add up on a position:
  a position has either its own discount or the order's (§ 12.5.1).
* An order discount over positions that have their own: the employee chooses explicitly — keep
  them and apply the order's to the rest, or remove them and apply the order's to all, or not
  apply (§ 12.5.3–12.5.4).
* A fixed order discount is spread over its positions of its currency in proportion to their
  amounts; rounded to the minor units, the rest goes to the last position (§ 12.5.4; З13).
* A discount never exceeds 100 % of what it applies to (§ 12.5.5). An employee applies it with
  the right «Apply discounts» within the limit set for him; a fixed amount is compared as a
  percent of what it applies to (З12). The top-authority director has no limit (PROPOSAL).
* A discount is a charge of its own, «discount», per position: payments stay as they are; the
  balance of the order changes (§ 12.5.6). A recalculation of a position recalculates its
  discount: a percent from the new amount, a fixed amount capped by it (З14).
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from c4rsys.orders.models import AppliedDiscount, Discount, DiscountLimit, Order
from c4rsys.orders.services import ensure_open, registry
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.platform.models import Currency

SOURCE = "discount"
HUNDRED = Decimal(100)
# The scope a charge source belongs to; a delivery is covered only by a discount of the order.
SCOPE_OF: dict[str, str] = {
    "rental_base": Discount.Scope.RENTAL,
    "rental_mileage": Discount.Scope.RENTAL,
    "rental_manual": Discount.Scope.RENTAL,
    "rental_service": Discount.Scope.EXTRA_SERVICE,
    "service_job": Discount.Scope.SERVICE,
    "service_material": Discount.Scope.SERVICE,
    "sale_line": Discount.Scope.GOODS,
}

# sale line ids -> the code of the category of the catalog of parts (warehouse).
GoodsCategories = Callable[[list[UUID]], dict[UUID, str]]
_goods_categories: list[GoodsCategories] = []


def register_goods_categories(provider: GoodsCategories) -> None:
    _goods_categories.append(provider)


@dataclass(frozen=True)
class Position:
    type: str
    id: UUID
    currency: str
    base: Decimal
    category: str = ""


def positions_of(order: Order) -> list[Position]:
    """The charged positions of the order with their amounts before discounts."""
    rows = [
        line
        for p in registry.charge_lines
        for line in p(order.company_id, order.id)
        if line[0] != SOURCE
    ]
    goods = [source_id for source_type, source_id, _, _ in rows if source_type == "sale_line"]
    categories: dict[UUID, str] = {}
    for provider in _goods_categories:
        categories.update(provider(goods))
    return [
        Position(source_type, source_id, currency, amount, categories.get(source_id, ""))
        for source_type, source_id, currency, amount in rows
        if amount > 0
    ]


def _covers(scope: str, categories: list[str], position: Position) -> bool:
    if scope == Discount.Scope.ORDER:
        return True
    if SCOPE_OF.get(position.type) != scope:
        return False
    return not categories or (scope == Discount.Scope.GOODS and position.category in categories)


def _places(currency: str) -> int:
    found = Currency.objects.filter(code=currency).values_list("minor_units", flat=True).first()
    return found if found is not None else 2


def _round(value: Decimal, currency: str) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-_places(currency)), rounding=ROUND_HALF_UP)


def rounded(value: Decimal | int, currency: str) -> Decimal:
    return _round(Decimal(value), currency)


# --- Recalculation ---------------------------------------------------------------------------


def _active(order_id: UUID) -> list[AppliedDiscount]:
    return list(AppliedDiscount.objects.filter(order_id=order_id, removed_at__isnull=True))


def amounts(order: Order) -> dict[UUID, tuple[Decimal, AppliedDiscount]]:
    """The discount of each position and where it comes from."""
    applied = _active(order.id)
    own = {a.position_id: a for a in applied if a.target == AppliedDiscount.Target.POSITION}
    whole = next((a for a in applied if a.target == AppliedDiscount.Target.ORDER), None)
    result: dict[UUID, tuple[Decimal, AppliedDiscount]] = {}
    covered: list[Position] = []
    for position in positions_of(order):
        a = own.get(position.id)
        if a is not None:
            value = (
                _round(position.base * a.value / HUNDRED, position.currency)
                if a.kind == Discount.Kind.PERCENT
                else min(a.value, position.base)
            )
            result[position.id] = (value, a)
        elif whole is not None and _covers(whole.scope, whole.goods_categories, position):
            covered.append(position)
    if whole is None or not covered:
        return result
    if whole.kind == Discount.Kind.PERCENT:
        for position in covered:
            result[position.id] = (
                _round(position.base * whole.value / HUNDRED, position.currency),
                whole,
            )
        return result
    same = [p for p in covered if p.currency == whole.currency]
    total = sum((p.base for p in same), Decimal(0))
    if total <= 0:
        return result
    left = min(whole.value, total)
    for index, position in enumerate(same):
        if index == len(same) - 1:
            share = left
        else:
            share = min(_round(whole.value * position.base / total, position.currency), left)
        share = min(share, position.base)
        left -= share
        result[position.id] = (share, whole)
    return result


def recalculate(ctx: CommandContext, order_id: UUID) -> None:
    """Brings the discount charges of the order to the applied discounts (§ 12.5.6)."""
    from c4rsys.finance import charges

    order = Order.objects.filter(pk=order_id).first()
    if order is None or order.client_id is None:
        return
    wanted = amounts(order)
    known = {source_id for source_id, _ in charges.sources_of(order.id, SOURCE)}
    currencies = {p.id: p.currency for p in positions_of(order)}
    for position_id in set(wanted) | known:
        value, applied = wanted.get(position_id, (Decimal(0), None))
        currency = currencies.get(position_id) or charges.charged_currency(SOURCE, position_id)
        if currency is None:
            continue
        charges.set_charge(
            ctx,
            company_id=order.company_id,
            order_id=order.id,
            client_id=order.client_id,
            source_type=SOURCE,
            source_id=position_id,
            amount=-value,
            currency=currency,
            reason="discount",
            calculation={}
            if applied is None
            else {
                "discount": str(applied.discount_id),
                "name": applied.name,
                "kind": applied.kind,
                "value": str(applied.value),
                "target": applied.target,
            },
        )


def on_charge_changed(ctx: CommandContext, order_id: UUID, source_type: str) -> None:
    """A position was recalculated: its discount follows (З14)."""
    if (
        source_type != SOURCE
        and AppliedDiscount.objects.filter(order_id=order_id, removed_at__isnull=True).exists()
    ):
        recalculate(ctx, order_id)


# --- Applying ----------------------------------------------------------------------------------


def limit_of(actor: Actor, top_authority: bool) -> Decimal | None:
    if top_authority:
        return HUNDRED
    if actor.employment_id is None:
        return None
    found = DiscountLimit.objects.filter(employment_id=actor.employment_id).first()
    return found.percent if found else None


def offered(order: Order) -> list[Discount]:
    """Active discounts of the catalog for the groups of the order's client (§ 12.5.2)."""
    from c4rsys.parties import groups

    client_groups = (
        {str(g) for g in groups.ids_of_client(order.client_id)} if order.client_id else set()
    )
    return [
        d
        for d in Discount.objects.filter(
            company_id=order.company_id, state=Discount.State.ACTIVE
        ).order_by("name")
        if not d.client_group_ids or client_groups & set(d.client_group_ids)
    ]


def _locked_order(company_id: UUID, order_id: UUID) -> Order:
    order = Order.objects.select_for_update().filter(company_id=company_id, pk=order_id).first()
    if order is None:
        raise errors.not_found()
    if order.client_id is None:
        raise errors.business_rule("ORDER_WITHOUT_CLIENT")
    ensure_open(company_id, order_id)
    return order


def _check_limit(limit: Decimal | None, discount: Discount, base: Decimal) -> None:
    if limit is None:
        raise errors.business_rule("DISCOUNT_LIMIT_NOT_SET")
    percent = (
        discount.value
        if discount.kind == Discount.Kind.PERCENT
        else (discount.value * HUNDRED / base if base > 0 else HUNDRED)
    )
    if percent > limit:
        raise errors.business_rule("DISCOUNT_OVER_LIMIT", limit=str(limit))


def _record(
    ctx: CommandContext, order: Order, discount: Discount, target: str, position: Position | None
) -> AppliedDiscount:
    applied = AppliedDiscount.objects.create(
        company_id=order.company_id,
        order=order,
        discount=discount,
        target=target,
        position_type=position.type if position else "",
        position_id=position.id if position else None,
        name=discount.name,
        kind=discount.kind,
        value=discount.value,
        currency=discount.currency,
        scope=discount.scope,
        goods_categories=discount.goods_categories,
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "order.discount_applied",
        object_type="order",
        object_id=order.id,
        after=payload(applied),
    )
    return applied


def _remove(ctx: CommandContext, applied: AppliedDiscount, why: str) -> None:
    applied.removed_at = timezone.now()
    applied.removed_by_user_id = ctx.actor.user_id
    applied.save(update_fields=["removed_at", "removed_by_user_id"])
    ctx.audit(
        "order.discount_removed",
        object_type="order",
        object_id=applied.order_id,
        before=payload(applied),
        reason=why,
    )


def apply(
    ctx: CommandContext,
    company_id: UUID,
    order_id: UUID,
    discount_id: UUID,
    *,
    position_type: str = "",
    position_id: UUID | None = None,
    conflict: str = "",
    limit: Decimal | None,
) -> AppliedDiscount:
    order = _locked_order(company_id, order_id)
    discount = next((d for d in offered(order) if d.id == discount_id), None)
    if discount is None:
        raise errors.validation_error("discount_id", code="DISCOUNT_NOT_OFFERED")
    positions = positions_of(order)
    active = _active(order.id)
    if position_id is not None:
        position = next(
            (p for p in positions if p.id == position_id and p.type == position_type), None
        )
        if position is None:
            raise errors.validation_error("position_id", code="POSITION_UNKNOWN")
        if not _covers(discount.scope, discount.goods_categories, position):
            raise errors.business_rule("DISCOUNT_SCOPE_MISMATCH", field="discount_id")
        if discount.kind == Discount.Kind.AMOUNT and discount.currency != position.currency:
            raise errors.business_rule("DISCOUNT_CURRENCY_MISMATCH", field="discount_id")
        _check_limit(limit, discount, position.base)
        for a in active:
            if a.target == AppliedDiscount.Target.POSITION and a.position_id == position.id:
                _remove(ctx, a, "replaced")
        applied = _record(ctx, order, discount, AppliedDiscount.Target.POSITION, position)
    else:
        covered = [p for p in positions if _covers(discount.scope, discount.goods_categories, p)]
        if discount.kind == Discount.Kind.AMOUNT:
            covered = [p for p in covered if p.currency == discount.currency]
        if not covered:
            raise errors.business_rule("DISCOUNT_NOTHING_TO_APPLY")
        own = [
            a
            for a in active
            if a.target == AppliedDiscount.Target.POSITION
            and a.position_id in {p.id for p in covered}
        ]
        if own and conflict not in ("keep_positions", "remove_positions"):
            # § 12.5.3–12.5.4: never resolved automatically.
            raise errors.business_rule(
                "DISCOUNT_CONFLICT", positions=[str(a.position_id) for a in own]
            )
        kept = {a.position_id for a in own} if conflict == "keep_positions" else set()
        base = sum((p.base for p in covered if p.id not in kept), Decimal(0))
        _check_limit(limit, discount, base)
        if conflict == "remove_positions":
            for a in own:
                _remove(ctx, a, "order_discount_chosen")
        for a in active:
            if a.target == AppliedDiscount.Target.ORDER:
                _remove(ctx, a, "replaced")
        applied = _record(ctx, order, discount, AppliedDiscount.Target.ORDER, None)
    recalculate(ctx, order.id)
    return applied


def remove(ctx: CommandContext, company_id: UUID, order_id: UUID, applied_id: UUID) -> None:
    order = _locked_order(company_id, order_id)
    applied = AppliedDiscount.objects.filter(
        order=order, pk=applied_id, removed_at__isnull=True
    ).first()
    if applied is None:
        raise errors.not_found()
    _remove(ctx, applied, "removed")
    recalculate(ctx, order.id)


# --- Limits ------------------------------------------------------------------------------------


def set_limit(
    ctx: CommandContext, company_id: UUID, employment_id: UUID, percent: Decimal | None
) -> Decimal | None:
    found = DiscountLimit.objects.select_for_update().filter(employment_id=employment_id).first()
    before = str(found.percent) if found else None
    if percent is None:
        if found is not None:
            found.delete()
    else:
        if percent < 0 or percent > HUNDRED:
            raise errors.validation_error("percent")
        if found is None:
            found = DiscountLimit(company_id=company_id, employment_id=employment_id)
        found.percent = percent
        found.updated_by_user_id = ctx.actor.user_id
        found.updated_at = timezone.now()
        found.save()
    ctx.audit(
        "employee.discount_limit_changed",
        object_type="employment",
        object_id=employment_id,
        before={"percent": before},
        after={"percent": str(percent) if percent is not None else None},
    )
    return percent


def get_limit(employment_id: UUID) -> Decimal | None:
    found = DiscountLimit.objects.filter(employment_id=employment_id).first()
    return found.percent if found else None


# --- Payloads ----------------------------------------------------------------------------------


def payload(a: AppliedDiscount) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "discount_id": str(a.discount_id),
        "name": a.name,
        "kind": a.kind,
        "value": str(a.value),
        "currency": a.currency,
        "scope": a.scope,
        "target": a.target,
        "position_type": a.position_type,
        "position_id": str(a.position_id) if a.position_id else None,
        "created_at": a.created_at.isoformat(),
        "removed_at": a.removed_at.isoformat() if a.removed_at else None,
    }


def catalog_payload(d: Discount) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "version": d.version,
        "name": d.name,
        "kind": d.kind,
        "value": str(d.value),
        "currency": d.currency,
        "scope": d.scope,
        "client_group_ids": d.client_group_ids,
        "goods_categories": d.goods_categories,
        "state": d.state,
    }


def catalog_of(company_id: UUID) -> list[Discount]:
    return list(Discount.objects.filter(company_id=company_id).order_by("state", "name"))


def create_discount(ctx: CommandContext, company_id: UUID, data: dict[str, Any]) -> Discount:
    discount = Discount.objects.create(
        company_id=company_id, created_by_user_id=ctx.actor.user_id, **data
    )
    ctx.audit(
        "discount.created",
        object_type="discount",
        object_id=discount.id,
        after=catalog_payload(discount),
    )
    return discount


def set_archived(
    ctx: CommandContext, company_id: UUID, discount_id: UUID, archived: bool
) -> Discount:
    discount = (
        Discount.objects.select_for_update()
        .filter(Q(company_id=company_id), pk=discount_id)
        .first()
    )
    if discount is None:
        raise errors.not_found()
    discount.state = Discount.State.ARCHIVED if archived else Discount.State.ACTIVE
    discount.version += 1
    discount.save(update_fields=["state", "version"])
    ctx.audit(
        "discount.archived" if archived else "discount.restored",
        object_type="discount",
        object_id=discount.id,
    )
    return discount
