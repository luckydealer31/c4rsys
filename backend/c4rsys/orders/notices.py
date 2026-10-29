"""Debt of a closed order in the notification centre (ТЗ § 20.3.5, § 19.1.9; decisions З-7,
З-8, З-9): a warning when an order is closed with debt, without repetitions, until the debt is
paid; with the company's rule also an automatic task (off by default). The responsible of the
order receives it, else those who see the finances."""

from datetime import datetime
from decimal import Decimal

from django.db import transaction

from c4rsys.notifications import rules
from c4rsys.notifications import services as notifications
from c4rsys.notifications.models import Notification
from c4rsys.orders import services as orders
from c4rsys.orders.models import Order
from c4rsys.platform.models import AuditEvent

EVENT = "finance.debt"


def _debt(order: Order) -> dict[str, str]:
    return {
        c: str(m.balance) for c, m in orders.amounts_of(order).items() if m.balance > Decimal(0)
    }


def on_order_closed(entry: AuditEvent) -> int:
    from c4rsys.tasks import services as tasks

    order = Order.objects.filter(pk=entry.object_id).first()
    if order is None or order.client_id is None:
        return 0
    debt = _debt(order)
    if not debt:
        return 0
    condition = f"{EVENT}:{order.id}"
    params = {"number": order.number, "debt": debt}
    raised = rules.raise_event(
        order.company_id,
        EVENT,
        condition=condition,
        object_type="order",
        object_id=order.id,
        params=params,
        responsible=[order.responsible_employment_id],
    )
    if rules.rule(order.company_id, EVENT).auto_task:
        tasks.ensure_system(
            order.company_id,
            event=EVENT,
            condition=condition,
            location_id=None,
            object_type="order",
            object_id=order.id,
            params=params,
            priority="normal",
        )
    return int(raised)


def run(now: datetime) -> int:
    """The debt is paid: the warning and the task end."""
    from c4rsys.tasks import services as tasks

    changed = 0
    with transaction.atomic():
        for n in Notification.objects.filter(event=EVENT, state=Notification.State.ACTIVE):
            order = Order.objects.filter(pk=str(n.object_id)).first()
            if order is None or not _debt(order):
                notifications.resolve(n.company_id, n.condition, "debt_paid", now)
                tasks.close_system(n.company_id, n.condition, done=True, result="debt_paid")
                changed += 1
    return changed
