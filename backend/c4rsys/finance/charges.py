"""Accruals (ТЗ § 12.2.4, § 13.6.3, § 17.2.6). Money movements are separate (T15)."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Sum
from django.utils import timezone

from c4rsys.finance.models import ChargeEntry
from c4rsys.platform.commands import CommandContext


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
    return _append(
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
    rows = (
        ChargeEntry.objects.filter(source_type=source_type, source_id=source_id)
        .exclude(order_id=order_id)
        .values("company_id", "order_id", "client_id", "currency_id")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        total = row["total"]
        if total == 0:
            continue
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
