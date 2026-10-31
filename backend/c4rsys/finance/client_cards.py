"""Settlements in the merge and the split of client cards (ТЗ § 6.5.2–6.5.3, § 6.7.2).

PROPOSAL (TODO(spec)):
* A duplicate is merged only with a zero settlement balance and no held deposit, so the
  settlements of the two cards are not mixed: each card keeps its own records, and nothing posted
  is changed.
* At the split an order assigned to the other card takes its charges by reversing entries (the
  posted entries stay); it is refused while payments, set-offs or deposits are tied to the order —
  they are cancelled or moved by the finance corrections first. A payment made after the merge
  and not tied to an order is assigned by itself, as the correction of a mistakenly named client
  of a payment (§ 17.2.12): the previous client stays in history.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Sum

from c4rsys.finance import charges, corrections, deposits, settlements
from c4rsys.finance.models import (
    ChargeEntry,
    DepositEntry,
    MoneyMovement,
    Offset,
    PaymentAllocation,
)
from c4rsys.parties import merges
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

SPLIT_REASON = "client_split"


def merge_check(company_id: UUID, duplicate_id: UUID) -> list[str]:
    found = []
    figures = settlements.client_figures(company_id, duplicate_id)
    if any(v != 0 for v in figures.balance.values()):
        found.append("duplicate_has_balance")
    if any(v != 0 for v in deposits.held_by_client(company_id, duplicate_id).values()):
        found.append("duplicate_holds_deposit")
    return found


def on_client_moved(
    ctx: CommandContext, company_id: UUID, order_ids: list[UUID], source: UUID, target: UUID
) -> None:
    tied = (
        PaymentAllocation.objects.filter(
            company_id=company_id, order_id__in=order_ids, state=PaymentAllocation.State.ACTIVE
        ).exists()
        or Offset.objects.filter(
            company_id=company_id, order_id__in=order_ids, state=Offset.State.ACTIVE
        ).exists()
        or DepositEntry.objects.filter(company_id=company_id, order_id__in=order_ids).exists()
    )
    if tied:
        raise errors.business_rule("CARD_SPLIT_ORDER_SETTLED")
    rows = (
        ChargeEntry.objects.filter(company_id=company_id, order_id__in=order_ids, client_id=source)
        .values("order_id", "source_type", "source_id", "currency_id")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        total: Decimal = row["total"]
        if total == 0:
            continue
        current = charges.current_charge(row["source_type"], row["source_id"])
        for client, delta in ((source, -total), (target, total)):
            charges._append(
                ctx,
                company_id,
                row["order_id"],
                client,
                row["source_type"],
                row["source_id"],
                delta,
                row["currency_id"],
                current,
                SPLIT_REASON,
                {"client_from": str(source), "client_to": str(target)},
            )


def _payments(company_id: UUID, client_id: UUID, moment: datetime) -> Any:
    return (
        settlements.movements_of_client(company_id, client_id)
        .filter(
            kind=MoneyMovement.Kind.PAYMENT,
            state=MoneyMovement.State.POSTED,
            created_at__gte=moment,
        )
        .exclude(allocations__state=PaymentAllocation.State.ACTIVE)
        .order_by("created_at")
    )


def since(company_id: UUID, client_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    return [
        {
            "kind": "payment",
            "id": str(m.id),
            "label": f"{m.number} · {m.amount} {m.currency_id}",
            "at": m.created_at.isoformat(),
        }
        for m in _payments(company_id, client_id, moment)
    ]


def reassign(
    ctx: CommandContext, company_id: UUID, ids: list[UUID], source: UUID, target: UUID
) -> None:
    for movement_id in ids:
        corrections.change_client(ctx, company_id, movement_id, target, SPLIT_REASON)


PROVIDER = merges.CardRecords("client", "payment", since, reassign)
