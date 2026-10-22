"""Orders: automatic creation, displayed state and amounts (ТЗ § 12.1.1, § 12.1.9–12.1.10,
§ 12.2.3–12.2.4).

The operations of an order and the money of an order live in other modules (rentals, finance).
They register providers here, so this module does not read their tables (A04, DEV_CONVENTIONS).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.orders.models import Order
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number

# --- Providers from other modules ------------------------------------------------------------


@dataclass(frozen=True)
class OperationSummary:
    """One operation of an order as the order needs to see it."""

    type: str  # "rental", later "service", "delivery", ...
    id: UUID
    state: str
    location_id: UUID | None
    finished: bool  # completed, cancelled or terminated with its formalities (§ 12.1.9)
    blockers: tuple[str, ...] = ()  # unmet closing conditions of this operation


@dataclass
class Money:
    """Amounts of one currency (§ 12.2.4)."""

    charged: Decimal = Decimal(0)
    paid: Decimal = Decimal(0)

    @property
    def balance(self) -> Decimal:
        """Positive: still to pay; negative: overpayment."""
        return self.charged - self.paid


OperationsProvider = Callable[[UUID, UUID], list[OperationSummary]]
# (company_id, order_id) -> {currency: amount}
AmountProvider = Callable[[UUID, UUID], dict[str, Decimal]]


@dataclass
class _Registry:
    operations: list[OperationsProvider] = field(default_factory=list)
    charged: list[AmountProvider] = field(default_factory=list)
    paid: list[AmountProvider] = field(default_factory=list)


registry = _Registry()


def register_operations(provider: OperationsProvider) -> None:
    registry.operations.append(provider)


def register_charged(provider: AmountProvider) -> None:
    registry.charged.append(provider)


def register_paid(provider: AmountProvider) -> None:
    registry.paid.append(provider)


def operations_of(order: Order) -> list[OperationSummary]:
    return [op for p in registry.operations for op in p(order.company_id, order.id)]


def amounts_of(order: Order) -> dict[str, Money]:
    result: dict[str, Money] = {}
    for provider in registry.charged:
        for currency, amount in provider(order.company_id, order.id).items():
            result.setdefault(currency, Money()).charged += amount
    for provider in registry.paid:
        for currency, amount in provider(order.company_id, order.id).items():
            result.setdefault(currency, Money()).paid += amount
    return result


# --- Creation (§ 12.1.1, § 12.1.10) ----------------------------------------------------------


def create_order(
    ctx: CommandContext,
    *,
    company_id: UUID,
    client_id: UUID,
    responsible_employment_id: UUID | None,
) -> Order:
    """A client order created by the operation that needs it (issue of a vehicle at stage 1).
    The number is the next one inside the company; the responsible employee is the creator by
    default. Creating the order does not start its operations (§ 12.2.3)."""
    order = Order.objects.create(
        company_id=company_id,
        number=next_number(company_id, "order"),
        client_id=client_id,
        responsible_employment_id=responsible_employment_id,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "order.created",
        object_type="order",
        object_id=order.id,
        after={
            "number": order.number,
            "client": str(client_id),
            "responsible_employment": str(responsible_employment_id)
            if responsible_employment_id
            else None,
        },
    )
    return order


def lock_open_order(company_id: UUID, order_id: UUID) -> Order:
    """For an operation added to an existing order: it must be open (§ 12.2.3)."""
    order = Order.objects.select_for_update().filter(company_id=company_id, pk=order_id).first()
    if order is None:
        raise errors.not_found()
    if order.state != Order.State.OPEN:
        raise errors.business_rule("ORDER_CLOSED", field="order_id")
    return order


# --- Reading -----------------------------------------------------------------------------------


def orders_of(company_id: UUID) -> QuerySet[Order]:
    return Order.objects.filter(company_id=company_id).order_by("-number")


def get_order(company_id: UUID, order_id: UUID) -> Order:
    order = orders_of(company_id).filter(pk=order_id).first()
    if order is None:
        raise errors.not_found()
    return order


def closing_conditions(operations: list[OperationSummary]) -> list[dict[str, Any]]:
    """Unmet conditions of § 12.1.9 that come from the operations."""
    unmet = []
    for op in operations:
        if not op.finished:
            unmet.append({"operation": op.type, "id": str(op.id), "condition": "not_finished"})
        unmet.extend({"operation": op.type, "id": str(op.id), "condition": b} for b in op.blockers)
    return unmet


def display_state(order: Order, operations: list[OperationSummary]) -> str:
    """«Open», «Ready to close» or the stored final state (§ 12.2.3). Debt does not make an
    order unready: closing with debt is a separate permission (§ 12.1.9)."""
    if order.state == Order.State.OPEN and not closing_conditions(operations):
        return "ready_to_close"
    return order.state
