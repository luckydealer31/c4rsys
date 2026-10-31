"""Payments and other money records in the single search and in the list of payments: by the
number or the id (ТЗ § 2.8.3). A record is found only on an account the employee may see."""

from uuid import UUID

from django.db.models import Q, QuerySet

from c4rsys.finance.models import MoneyMovement
from c4rsys.platform.commands import Actor
from c4rsys.platform.money import round_money
from c4rsys.platform.search import Hit, Query


def matching(found: QuerySet[MoneyMovement], query: Query) -> QuerySet[MoneyMovement]:
    condition = Q(pk__in=[])
    if query.number is not None:
        condition |= Q(number=query.number)
    if query.uuid is not None:
        condition |= Q(pk=query.uuid)
    return found.filter(condition)


def provider(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    from c4rsys.finance.views import _visible

    hits = []
    found = matching(MoneyMovement.objects.filter(company_id=company_id), query).select_related(
        "currency"
    )
    for m in found.order_by("-number")[: limit * 2]:
        if not _visible(actor, m.cash_account_id):
            continue
        amount = round_money(m.amount, m.currency.minor_units)
        hits.append(
            Hit(
                "payment",
                str(m.id),
                str(m.number),
                f"{amount} {m.currency_id}",
                exact=True,
                extra={"kind": m.kind, "direction": m.direction},
            )
        )
    return hits[:limit]
