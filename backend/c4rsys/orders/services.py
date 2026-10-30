"""Orders: automatic creation, displayed state and amounts (ТЗ § 12.1.1, § 12.1.9–12.1.10,
§ 12.2.3–12.2.4).

The operations of an order and the money of an order live in other modules (rentals, finance).
They register providers here, so this module does not read their tables (A04, DEV_CONVENTIONS).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.orders.models import Order
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext
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
# (company_id, order_id) -> what the client is shown of the operations (§ 7.1.1; decision Б-1)
ClientViewProvider = Callable[[UUID, UUID], list[dict[str, Any]]]
# (company_id, order_id) -> charges by source: (source type, source id, currency, amount)
ChargeLinesProvider = Callable[[UUID, UUID], list[tuple[str, UUID, str, Decimal]]]


@dataclass
class _Registry:
    operations: list[OperationsProvider] = field(default_factory=list)
    charged: list[AmountProvider] = field(default_factory=list)
    paid: list[AmountProvider] = field(default_factory=list)
    estimated: list[AmountProvider] = field(default_factory=list)
    client_view: list[ClientViewProvider] = field(default_factory=list)
    charge_lines: list[ChargeLinesProvider] = field(default_factory=list)


registry = _Registry()


def register_operations(provider: OperationsProvider) -> None:
    registry.operations.append(provider)


def register_charged(provider: AmountProvider) -> None:
    registry.charged.append(provider)


def register_paid(provider: AmountProvider) -> None:
    registry.paid.append(provider)


def register_estimated(provider: AmountProvider) -> None:
    """The cost of operations not charged because the order has no client (decision Р25)."""
    registry.estimated.append(provider)


def register_client_view(provider: ClientViewProvider) -> None:
    registry.client_view.append(provider)


def register_charge_lines(provider: ChargeLinesProvider) -> None:
    registry.charge_lines.append(provider)


def chargeable_client(order_id: UUID) -> UUID | None:
    """The client to charge in the order: none while the client is not specified (decision Р25)
    and while the order is preliminary — it is only discussed (§ 12.1.7; decision З2)."""
    row = Order.objects.filter(pk=order_id).values_list("client_id", "state").first()
    if row is None or row[1] == Order.State.PRELIMINARY:
        return None
    return row[0]


def estimated_of(order: Order) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    preliminary = order.state == Order.State.PRELIMINARY
    if order.is_internal or (order.client_id is not None and not preliminary):
        return result
    for provider in registry.estimated:
        for currency, amount in provider(order.company_id, order.id).items():
            result[currency] = result.get(currency, Decimal(0)) + amount
    return result


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
    client_id: UUID | None,
    responsible_employment_id: UUID | None,
    preliminary: bool = False,
) -> Order:
    """A client order created by the operation that needs it (an issue or a booking) or by hand
    (§ 12.1.7). A booking may create it before the client is known: «client not specified»
    (§ 12.1.8). The number is the next one inside the company; the responsible employee is the
    creator by default. Creating the order does not start its operations (§ 12.2.3)."""
    order = Order.objects.create(
        company_id=company_id,
        number=next_number(company_id, "order"),
        client_id=client_id,
        state=Order.State.PRELIMINARY if preliminary else Order.State.OPEN,
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
            "state": order.state,
            "client": str(client_id) if client_id else None,
            "responsible_employment": str(responsible_employment_id)
            if responsible_employment_id
            else None,
        },
    )
    return order


# (ctx, company_id, order_id) -> None: what an operation does when its order gets a client.
ClientAssignedHandler = Callable[[CommandContext, UUID, UUID], None]
_on_client_assigned: list[ClientAssignedHandler] = []


def register_on_client_assigned(handler: ClientAssignedHandler) -> None:
    _on_client_assigned.append(handler)


def assign_client(ctx: CommandContext, order: Order, client_id: UUID) -> None:
    """The client of an order created without one is set later; it is the same order, not a new
    one (§ 12.1.8). A client once set is not changed this way."""
    if order.is_internal:
        raise errors.business_rule("ORDER_INTERNAL", field="order_id")
    if order.client_id is not None:
        if order.client_id != client_id:
            raise errors.business_rule("ORDER_CLIENT_MISMATCH", field="client_id")
        return
    order.client_id = client_id
    order.version += 1
    order.save(update_fields=["client", "version", "updated_at"])
    ctx.audit(
        "order.client_assigned",
        object_type="order",
        object_id=order.id,
        before={"client": None},
        after={"client": str(client_id)},
    )
    # What was estimated without the client is charged to them now (decision Р25).
    for handler in _on_client_assigned:
        handler(ctx, order.company_id, order.id)


def open_order_of_client(
    company_id: UUID,
    order_id: UUID,
    client_id: UUID | None = None,
    *,
    preliminary_ok: bool = False,
) -> Order:
    """An open client order to add goods to (decision of 25.10.2026), locked. A preliminary one
    takes services and rental proposals (decision З2)."""
    order = lock_open_order(company_id, order_id, preliminary_ok=preliminary_ok)
    if order.is_internal or order.client_id is None:
        raise errors.business_rule("ORDER_WITHOUT_CLIENT", field="order_id")
    if client_id is not None and order.client_id != client_id:
        raise errors.business_rule("ORDER_CLIENT_MISMATCH", field="order_id")
    return order


def create_internal_order(
    ctx: CommandContext,
    *,
    company_id: UUID,
    responsible_employment_id: UUID | None,
    preliminary: bool = False,
) -> Order:
    """An internal order of the company itself (§ 12.1.1, § 12.1.3), for example for an internal
    move of a vehicle (§ 15.1.5). It has no client and no client charges."""
    order = Order.objects.create(
        company_id=company_id,
        number=next_number(company_id, "order"),
        is_internal=True,
        state=Order.State.PRELIMINARY if preliminary else Order.State.OPEN,
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
            "internal": True,
            "state": order.state,
            "responsible_employment": str(responsible_employment_id)
            if responsible_employment_id
            else None,
        },
    )
    return order


def lock_open_order(company_id: UUID, order_id: UUID, *, preliminary_ok: bool = False) -> Order:
    """For an operation added to an existing order: it must be open (§ 12.2.3)."""
    order = Order.objects.select_for_update().filter(company_id=company_id, pk=order_id).first()
    if order is None:
        raise errors.not_found()
    if order.state == Order.State.PRELIMINARY and not preliminary_ok:
        raise errors.business_rule("ORDER_PRELIMINARY", field="order_id")
    if order.state not in (Order.State.OPEN, Order.State.PRELIMINARY):
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


# --- Closing (§ 12.1.9, § 12.2.3, § 23.6.5; decision Р21 of 28.10.2026) ----------------------
#
# The order is closed by an employee with «Close orders» once every operation is finished in its
# own section by the rights of that section; closing needs no section rights of its own.

# (ctx, company_id, order_id) -> None: what an operation does when its order closes, in the
# closing transaction — for example the sale of reserved goods (§ 11.9.1).
CloseHandler = Callable[[CommandContext, UUID, UUID], None]
_on_close: list[CloseHandler] = []


def register_on_close(handler: CloseHandler) -> None:
    _on_close.append(handler)


def ensure_open(company_id: UUID, order_id: UUID) -> None:
    """While the order is closed its operations do not change (§ 12.2.3)."""
    state = (
        Order.objects.filter(company_id=company_id, pk=order_id)
        .values_list("state", flat=True)
        .first()
    )
    if state == Order.State.PRELIMINARY:
        raise errors.business_rule("ORDER_PRELIMINARY", order_id=str(order_id))
    if state != Order.State.OPEN:
        raise errors.business_rule("ORDER_CLOSED", order_id=str(order_id))


def ensure_editable(company_id: UUID, order_id: UUID) -> None:
    """A preliminary order is changed like an open one; nothing in it starts (§ 12.2.1)."""
    state = (
        Order.objects.filter(company_id=company_id, pk=order_id)
        .values_list("state", flat=True)
        .first()
    )
    if state not in (Order.State.OPEN, Order.State.PRELIMINARY):
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
    for handler in _on_close:
        handler(ctx, order.company_id, order.id)
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


def reopen_order(
    ctx: CommandContext, company_id: UUID, order_id: UUID, expected_version: int, reason: str
) -> Order:
    """The closing of an order is cancelled by an employee with the separate right, to correct it
    (§ 12.2.3). Nested operations do not reopen by themselves; corrections need their own rights.
    The reopening and the new closing are kept in the history."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    order = Order.objects.select_for_update().filter(company_id=company_id, pk=order_id).first()
    if order is None:
        raise errors.not_found()
    if order.version != expected_version:
        raise errors.version_conflict()
    if order.state != Order.State.CLOSED:
        raise errors.business_rule("ORDER_NOT_CLOSED")
    closed_at = order.closed_at
    order.state = Order.State.OPEN
    order.closed_at = None
    order.closed_by_user_id = None
    order.version += 1
    order.save(update_fields=["state", "closed_at", "closed_by_user_id", "version", "updated_at"])
    ctx.audit(
        "order.reopened",
        object_type="order",
        object_id=order.id,
        before={"state": Order.State.CLOSED, "closed_at": closed_at},
        after={"state": order.state},
        reason=reason,
    )
    return order


# --- Visibility (§ 12.4.2) ----------------------------------------------------------------------


def places_of(order: Order) -> set[UUID | None]:
    """Locations of the operations of the order; None for an order without located operations
    (its rights are checked anywhere in the company)."""
    located: set[UUID | None] = {
        op.location_id for op in operations_of(order) if op.location_id is not None
    }
    return located or {None}


def can_view(actor: Actor, order: Order) -> bool:
    """«View orders» in a location of one of its operations, or anywhere in the company for an
    order without located operations."""
    from c4rsys.companies import access  # companies use finance, finance uses orders

    resolver = access.RightsResolver()
    places = places_of(order)
    return any(resolver.has_permission(actor, "order.view", location_id=x) for x in places)


def has_right(actor: Actor, order: Order, permission: str) -> bool:
    """A right on the whole order: in every location of its operations."""
    from c4rsys.companies import access

    resolver = access.RightsResolver()
    return all(resolver.has_permission(actor, permission, location_id=x) for x in places_of(order))


# --- Manual creation, forming, responsible, cancellation (§ 12.1.7, § 12.1.10, § 12.2.1–12.2.2;
# decisions З2–З4, З7 of 28.10.2026) --------------------------------------------------------------


def create_manual(
    ctx: CommandContext,
    *,
    company_id: UUID,
    client_id: UUID | None,
    internal: bool,
    preliminary: bool,
) -> Order:
    """«Create the order» or «Save as preliminary»: for a client or the company itself
    (§ 12.1.3). Until the form is saved there is no order (§ 12.1.7)."""
    if internal == (client_id is not None):
        raise errors.validation_error("client_id", code="CLIENT_REQUIRED")
    if internal:
        return create_internal_order(
            ctx,
            company_id=company_id,
            responsible_employment_id=ctx.actor.employment_id,
            preliminary=preliminary,
        )
    return create_order(
        ctx,
        company_id=company_id,
        client_id=client_id,
        responsible_employment_id=ctx.actor.employment_id,
        preliminary=preliminary,
    )


def _locked(company_id: UUID, order_id: UUID, expected_version: int) -> Order:
    order = Order.objects.select_for_update().filter(company_id=company_id, pk=order_id).first()
    if order is None:
        raise errors.not_found()
    if order.version != expected_version:
        raise errors.version_conflict()
    return order


def _bump(order: Order, *fields: str) -> None:
    order.version += 1
    order.save(update_fields=[*fields, "version", "updated_at"])


# (ctx, company_id, order_id) -> None: what an operation does when its preliminary order is
# formed — a rental proposal becomes a booking, the cost is charged (§ 12.2.2).
FormHandler = Callable[[CommandContext, UUID, UUID], None]
_on_formed: list[FormHandler] = []


def register_on_formed(handler: FormHandler) -> None:
    _on_formed.append(handler)


def form_order(
    ctx: CommandContext, company_id: UUID, order_id: UUID, expected_version: int
) -> Order:
    """«Form the order»: the same row with its number, lines and history becomes an open order;
    nothing starts (§ 12.2.2). If a proposed vehicle is taken the order stays preliminary."""
    order = _locked(company_id, order_id, expected_version)
    if order.state != Order.State.PRELIMINARY:
        raise errors.business_rule("ORDER_NOT_PRELIMINARY")
    order.state = Order.State.OPEN
    _bump(order, "state")
    for handler in _on_formed:
        handler(ctx, company_id, order.id)
    ctx.audit(
        "order.formed",
        object_type="order",
        object_id=order.id,
        before={"state": Order.State.PRELIMINARY},
        after={"state": order.state},
    )
    return order


def set_responsible(
    ctx: CommandContext,
    company_id: UUID,
    order_id: UUID,
    employment_id: UUID,
    expected_version: int,
) -> Order:
    """The responsible employee coordinates the order; it gives no rights by itself (§ 12.1.10)."""
    from c4rsys.companies import staff
    from c4rsys.companies.models import Employment

    order = _locked(company_id, order_id, expected_version)
    if order.state not in (Order.State.OPEN, Order.State.PRELIMINARY):
        raise errors.business_rule("ORDER_CLOSED")
    employment = staff.get_employment(company_id, employment_id)
    if employment.state != Employment.State.ACTIVE:
        raise errors.business_rule("EMPLOYEE_NOT_ACTIVE", field="employment_id")
    if order.responsible_employment_id == employment.id:
        raise errors.business_rule("NOTHING_CHANGED")
    before = order.responsible_employment_id
    order.responsible_employment_id = employment.id
    _bump(order, "responsible_employment_id")
    ctx.audit(
        "order.responsible_changed",
        object_type="order",
        object_id=order.id,
        before={"responsible_employment": str(before) if before else None},
        after={"responsible_employment": str(employment.id)},
    )
    return order


# (ctx, company_id, order_id, reason) -> None: each module cancels its positions not started yet
# and refuses with a business rule when one has started (§ 12.2.1, § 12.3.7).
CancelHandler = Callable[[CommandContext, UUID, UUID, str], None]
_on_cancel: list[CancelHandler] = []


def register_on_cancel(handler: CancelHandler) -> None:
    _on_cancel.append(handler)


def cancel_order(
    ctx: CommandContext, company_id: UUID, order_id: UUID, expected_version: int, reason: str
) -> Order:
    """The whole order before its operations start: bookings and services not started are
    cancelled and their reserves freed (§ 12.2.1). An issued or returned rental is transferred
    or annulled first (§ 12.3.7). Payments stay: they become an overpayment (decision З4)."""
    reason = reason.strip()
    if not reason:
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    order = _locked(company_id, order_id, expected_version)
    if order.state not in (Order.State.OPEN, Order.State.PRELIMINARY):
        raise errors.business_rule("ORDER_CLOSED")
    before = order.state
    for handler in _on_cancel:
        handler(ctx, company_id, order.id, reason)
    order.state = Order.State.CANCELLED
    order.cancelled_at = timezone.now()
    order.cancel_reason = reason
    _bump(order, "state", "cancelled_at", "cancel_reason")
    ctx.audit(
        "order.cancelled",
        object_type="order",
        object_id=order.id,
        before={"state": before},
        after={"state": order.state},
        reason=reason,
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


# --- The client's history (§ 6.1.14, § 7.1.1; decision Б-1) -----------------------------------


def client_orders(
    company_id: UUID, client_ids: list[UUID], until: datetime | None = None
) -> QuerySet[Order]:
    """Orders of the client cards, newest first. A preliminary order is only discussed and is
    not history (PROPOSAL). After a broken link only the orders created before the break stay
    (§ 6.6.3)."""
    query = Order.objects.filter(company_id=company_id, client_id__in=client_ids).exclude(
        state=Order.State.PRELIMINARY
    )
    if until is not None:
        query = query.filter(created_at__lte=until)
    return query.order_by("-created_at")


def client_view(order: Order) -> dict[str, Any]:
    """What the client sees of an order: its operations as the modules describe them for the
    client, the charges by source and the amounts. No employees, internal comments, costs or
    expenses of the company (decision Б-1)."""
    operations = [item for p in registry.client_view for item in p(order.company_id, order.id)]
    names: dict[str, str] = {}
    for item in operations:
        names.update(item.pop("names", {}))
    lines = [
        {
            "source": source_type,
            "name": names.get(str(source_id), ""),
            "currency": currency,
            "amount": amount,
        }
        for p in registry.charge_lines
        for source_type, source_id, currency, amount in p(order.company_id, order.id)
    ]
    return {
        "id": str(order.id),
        "number": order.number,
        "state": display_state(order, operations_of(order)),
        "created_at": order.created_at.isoformat(),
        "closed_at": order.closed_at.isoformat() if order.closed_at else None,
        "operations": operations,
        "lines": lines,
        "amounts": amounts_of(order),
    }
