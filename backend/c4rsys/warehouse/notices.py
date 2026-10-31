"""Expiry of lots in the notification centre (ТЗ § 20.3.5; decision З-7: 7 days before by
default). A lot with stock warns from the company's moment before its expiry until it has no
stock left; the storekeepers of its location receive it (decision З-9)."""

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Sum

from c4rsys.notifications import rules
from c4rsys.notifications import services as notifications
from c4rsys.notifications.models import Notification
from c4rsys.warehouse.models import Lot, MinimumStock

EVENT = "warehouse.expiry"


def run(now: datetime) -> int:
    changed = 0
    warned: set[str] = set()
    with transaction.atomic():
        lots = (
            Lot.objects.select_related("item", "context", "context__location", "company")
            .filter(expiry_date__isnull=False, expiry_date__lte=(now + timedelta(days=90)).date())
            .annotate(left=Sum("stock__quantity"))
            .filter(left__gt=0)
        )
        for lot in lots:
            lead = rules.rule(lot.company_id, EVENT).lead_minutes
            today = now.astimezone(ZoneInfo(lot.company.timezone or "UTC")).date()
            if lot.expiry_date is None:
                continue
            if lot.expiry_date - timedelta(minutes=lead) > today:
                continue
            condition = f"{EVENT}:{lot.id}"
            warned.add(condition)
            state = "expired" if lot.expiry_date < today else "expiring"
            current = notifications.active(lot.company_id, condition)
            if current is not None and current.params.get("state") != state:
                notifications.resolve(lot.company_id, condition, "expiry_state_changed", now)
            changed += rules.raise_event(
                lot.company_id,
                EVENT,
                condition=condition,
                location_id=lot.context.location_id,
                params={
                    "item": lot.item.name,
                    "lot": lot.public_id,
                    "expiry_date": lot.expiry_date.isoformat(),
                    "state": state,
                },
                now=now,
            )
        for n in Notification.objects.filter(event=EVENT, state=Notification.State.ACTIVE):
            if n.condition not in warned:
                notifications.resolve(n.company_id, n.condition, "lot_without_stock", now)
                changed += 1
    return changed


LOW_STOCK = "warehouse.low_stock"


def low_stock(now: datetime) -> int:
    """§ 11.7.8: while the available quantity of an item in a warehouse (the stock less its
    reserves) is below the threshold set for that warehouse, the chosen employees are told; with
    no threshold nothing is raised. Nothing is purchased automatically. The notice ends when the
    quantity is back at the threshold or the threshold is removed."""
    from c4rsys.warehouse import prices

    changed = 0
    below: set[str] = set()
    with transaction.atomic():
        companies = MinimumStock.objects.values_list("company_id", flat=True).distinct()
        for company_id in set(companies):
            contexts = set(
                MinimumStock.objects.filter(company_id=company_id).values_list(
                    "context_id", flat=True
                )
            )
            for row in prices.low_stock(company_id, contexts):
                condition = f"{LOW_STOCK}:{row['item']['id']}:{row['context_id']}"
                below.add(condition)
                changed += rules.raise_event(
                    company_id,
                    LOW_STOCK,
                    condition=condition,
                    location_id=UUID(row["location_id"]),
                    object_type="warehouse_item",
                    object_id=UUID(row["item"]["id"]),
                    params={
                        "item": row["item"]["name"],
                        "location": row["location_name"],
                        "available": row["available"],
                        "minimum": row["minimum"],
                    },
                    now=now,
                )
        for n in Notification.objects.filter(event=LOW_STOCK, state=Notification.State.ACTIVE):
            if n.condition not in below:
                notifications.resolve(n.company_id, n.condition, "stock_restored", now)
                changed += 1
    return changed
