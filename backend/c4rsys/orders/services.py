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


# --- Control versions of the calculation (§ 13.6.8) ------------------------------------------

ControlVersionWriter = Callable[[CommandContext, UUID, UUID, str], None]
_control_writers: list[ControlVersionWriter] = []


def register_control_version(writer: ControlVersionWriter) -> None:
    _control_writers.append(writer)


def record_control_versions(ctx: CommandContext, order: Order, reason: str) -> None:
    """Immutable versions of the current calculation of every operation of the order, kept at
    payment and at closing (§ 13.6.8). Each module writes its own."""
    for writer in _control_writers:
        writer(ctx, order.company_id, order.id, reason)


# --- Closing (§ 12.1.9, § 12.2.3, § 23.6.5) ---------------------------------------------------

# Operation type -> permission of its section needed to close an order containing it.
_section_permissions: dict[str, str] = {}


def register_section_permission(operation_type: str, permission: str) -> None:
    _section_permissions[operation_type] = permission


def section_permission(operation_type: str) -> str | None:
    return _section_permissions.get(operation_type)


def ensure_open(company_id: UUID, order_id: UUID) -> None:
    """While the order is closed its operations do not change (§ 12.2.3)."""
    state = (
        Order.objects.filter(company_id=company_id, pk=order_id)
        .values_list("state", flat=True)
        .first()
    )
    if state != Order.State.OPEN:
        raise errors.business_rule("ORDER_CLOSED", order_id=str(order_id))


def close_order(
    ctx: CommandContext,
    company_id: UUID,
    order_id: UUID,
    expected_version: int,
    *,
    may_close_with_debt: bool,
) -> Order:
    """All operations finished (§ 12.1.9); debt needs a separate permission (§ 12.1.9, § 23.6.5);
    the current calculation is kept as a control version (§ 13.6.8). Debt and overpayment stay
    with the client after closing (§ 6.7.2)."""
    order = Order.objects.select_for_update().filter(company_id=company_id, pk=order_id).first()
    if order is None:
        raise errors.not_found()
    if order.version != expected_version:
        raise errors.version_conflict()
    if order.state != Order.State.OPEN:
        raise errors.business_rule("ORDER_CLOSED")
    unmet = closing_conditions(operations_of(order))
    if unmet:
        raise errors.business_rule("ORDER_NOT_READY_TO_CLOSE", conditions=unmet)
    amounts = amounts_of(order)
    debt = {c: m.balance for c, m in amounts.items() if m.balance > 0}
    if debt and not may_close_with_debt:
        raise errors.business_rule("ORDER_HAS_DEBT", debt={c: str(v) for c, v in debt.items()})

    record_control_versions(ctx, order, "closing")
    now = timezone.now()
    order.state = Order.State.CLOSED
    order.closed_at = now
    order.closed_by_user_id = ctx.actor.user_id
    order.version += 1
    order.save(update_fields=["state", "closed_at", "closed_by_user_id", "version", "updated_at"])
    ctx.audit(
        "order.closed",
        object_type="order",
        object_id=order.id,
        before={"state": Order.State.OPEN},
        after={
            "state": order.state,
            "closed_at": now,
            "amounts": {
                c: {"charged": m.charged, "paid": m.paid, "balance": m.balance}
                for c, m in amounts.items()
            },
            "with_debt": bool(debt),
        },
    )
    return order


# --- History (§ 23.10, screens «История») ----------------------------------------------------

HistoryProvider = Callable[[UUID, UUID], list[tuple[str, str]]]
_history_providers: list[HistoryProvider] = []


def register_history(provider: HistoryProvider) -> None:
    _history_providers.append(provider)


def history_objects(order: Order) -> list[tuple[str, str]]:
    """The order and the objects of its operations and money, as journaled objects."""
    objects = [("order", str(order.id))]
    for provider in _history_providers:
        objects.extend(provider(order.company_id, order.id))
    return objects
