"""Accruals (ТЗ § 12.2.4, § 13.6.3, § 17.2.6). Money movements are separate (T15)."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Sum
from django.utils import timezone

from c4rsys.finance.models import ChargeEntry
from c4rsys.platform.commands import CommandContext

# Called after the charge of a source changed: (ctx, order_id, source_type). Orders recalculate
# the discounts of the order (§ 12.5.6; decision З14).
AfterChange = Callable[[CommandContext, UUID, str], None]
_after_change: list[AfterChange] = []


def register_after_change(handler: AfterChange) -> None:
    _after_change.append(handler)


# Sources that go to another order together with a source (the discount of a position).
_companions: list[Callable[[str], list[str]]] = []


def register_companion(provider: Callable[[str], list[str]]) -> None:
    _companions.append(provider)


def discount_sources_of(order_id: UUID, prefix: str) -> list[tuple[str, UUID]]:
    """Sources of the order whose kind starts with the prefix: the discounts of positions."""
    return list(
        ChargeEntry.objects.filter(order_id=order_id, source_type__startswith=prefix)
        .values_list("source_type", "source_id")
        .distinct()
    )


def current_charge(source_type: str, source_id: UUID) -> Decimal:
    total = ChargeEntry.objects.filter(source_type=source_type, source_id=source_id).aggregate(
        total=Sum("amount")
    )["total"]
    return total if total is not None else Decimal(0)


def set_charge(
    ctx: CommandContext,
    *,
    company_id: UUID,
    order_id: UUID,
    client_id: UUID,
    source_type: str,
    source_id: UUID,
    amount: Decimal,
    currency: str,
    reason: str,
    calculation: dict[str, Any],
) -> ChargeEntry | None:
    """Brings the charge of a source to `amount` by appending the difference. Payments already
    made are not touched: the difference becomes debt or overpayment (§ 13.6.3). A charge left in
    another currency (another tariff of a booking) is brought to zero first."""
    totals = {
        r["currency_id"]: r["total"]
        for r in ChargeEntry.objects.filter(source_type=source_type, source_id=source_id)
        .values("currency_id")
        .annotate(total=Sum("amount"))
    }
    for other, total in totals.items():
        if other != currency and total != 0:
            _append(
                ctx,
                company_id,
                order_id,
                client_id,
                source_type,
                source_id,
                -total,
                other,
                Decimal(0),
                reason,
                calculation,
            )
    delta = amount - totals.get(currency, Decimal(0))
    if delta == 0:
        return None
    entry = _append(
        ctx,
        company_id,
        order_id,
        client_id,
        source_type,
        source_id,
        delta,
        currency,
        amount,
        reason,
        calculation,
    )
    for handler in _after_change:
        handler(ctx, order_id, source_type)
    return entry


def _append(
    ctx: CommandContext,
    company_id: UUID,
    order_id: UUID,
    client_id: UUID,
    source_type: str,
    source_id: UUID,
    delta: Decimal,
    currency: str,
    total: Decimal,
    reason: str,
    calculation: dict[str, Any],
) -> ChargeEntry:
    entry = ChargeEntry.objects.create(
        company_id=company_id,
        order_id=order_id,
        client_id=client_id,
        source_type=source_type,
        source_id=source_id,
        amount=delta,
        currency_id=currency,
        reason=reason,
        calculation=calculation,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "charge.recorded",
        object_type="charge_entry",
        object_id=entry.id,
        after={
            "order": str(order_id),
            "source_type": source_type,
            "source_id": str(source_id),
            "delta": delta,
            "total": total,
            "currency": currency,
            "reason": reason,
        },
    )
    return entry


def move_source(
    ctx: CommandContext, *, source_type: str, source_id: UUID, order_id: UUID, reason: str
) -> None:
    """The charges of a source go to another order with it (§ 12.3.7): a reversing entry in the
    order where they are and the same amount in the new one; nothing is changed or deleted."""
    kinds = [source_type] + [c for p in _companions for c in p(source_type)]
    touched: set[UUID] = set()
    for kind in kinds:
        touched |= _move(ctx, kind, source_id, order_id, reason)
    for touched_order in sorted(touched | {order_id}):
        for handler in _after_change:
            handler(ctx, touched_order, source_type)


def _move(
    ctx: CommandContext, source_type: str, source_id: UUID, order_id: UUID, reason: str
) -> set[UUID]:
    rows = (
        ChargeEntry.objects.filter(source_type=source_type, source_id=source_id)
        .exclude(order_id=order_id)
        .values("company_id", "order_id", "client_id", "currency_id")
        .annotate(total=Sum("amount"))
    )
    touched: set[UUID] = set()
    for row in rows:
        total = row["total"]
        if total == 0:
            continue
        touched.add(row["order_id"])
        calculation = {"moved_to": str(order_id), "from": str(row["order_id"])}
        for target, delta in ((row["order_id"], -total), (order_id, total)):
            _append(
                ctx,
                row["company_id"],
                target,
                row["client_id"],
                source_type,
                source_id,
                delta,
                row["currency_id"],
                current_charge(source_type, source_id),
                reason,
                calculation,
            )
    return touched


def charged_currency(source_type: str, source_id: UUID) -> str | None:
    """The currency of a source's current non-zero charge, to bring it to zero."""
    rows = (
        ChargeEntry.objects.filter(source_type=source_type, source_id=source_id)
        .values("currency_id")
        .annotate(total=Sum("amount"))
    )
    return next((r["currency_id"] for r in rows if r["total"] != 0), None)


def charged_for_order(company_id: UUID, order_id: UUID) -> dict[str, Decimal]:
    """Provider for orders: charged amounts per currency."""
    rows = (
        ChargeEntry.objects.filter(company_id=company_id, order_id=order_id)
        .values("currency_id")
        .annotate(total=Sum("amount"))
    )
    return {r["currency_id"]: r["total"] for r in rows}


def charge_ids_for_source(source_type: str, source_id: UUID) -> list[UUID]:
    return list(
        ChargeEntry.objects.filter(source_type=source_type, source_id=source_id).values_list(
            "id", flat=True
        )
    )


def lines_for_order(company_id: UUID, order_id: UUID) -> list[tuple[str, UUID, str, Decimal]]:
    """The current charge of each source of an order, for the client's history (decision Б-1).
    Sources brought to zero are left out."""
    rows = (
        ChargeEntry.objects.filter(company_id=company_id, order_id=order_id)
        .values("source_type", "source_id", "currency_id")
        .annotate(total=Sum("amount"))
        .order_by("source_type", "source_id")
    )
    return [
        (r["source_type"], r["source_id"], r["currency_id"], r["total"])
        for r in rows
        if r["total"] != 0
    ]


# Recalculations that change the agreed cost of a position; the client is told of them in the
# order (decision В-1). Charges at the start, at the return and by the terms are not changes.
CHANGE_REASONS = {
    "planned_end_changed",
    "tariff_changed",
    "booking_changed",
    "booking_vehicle_replaced",
    "corrected",
    "service_job_price_changed",
    "service_material_price_changed",
    "changed",
    "free_time",
}


def changes_for_order(
    company_id: UUID, order_id: UUID
) -> list[tuple[Any, str, UUID, str, Decimal, str]]:
    """Changes of the cost of an order for the client: recalculations of the agreed cost and the
    manual charges of a rental (decision В-1)."""
    return [
        (e.created_at, e.source_type, e.source_id, e.currency_id, e.amount, e.reason)
        for e in ChargeEntry.objects.filter(company_id=company_id, order_id=order_id).order_by(
            "created_at"
        )
        if e.amount != 0
        and (e.reason in CHANGE_REASONS or e.source_type == ChargeEntry.SourceType.RENTAL_MANUAL)
    ]
