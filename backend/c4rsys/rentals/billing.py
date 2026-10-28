"""Charges of a rental and of its linked services in the order (ТЗ § 13.6.3, § 13.6.7; customer's
decisions Р16, Р25 of 28.10.2026).

* A rental with a known start and end is charged into the order at once: a booking by its
  planned period, an issued rental from the actual start to the planned end, a returned one by
  the actual period. Every change of the period, the tariff or the free time brings the charge to
  the new amount by an appended difference. Without a planned end an issued rental is not charged
  until the return: its cost is the current one, up to now (§ 13.6.7).
* An order without a client has no charges (a charge belongs to a client): its amounts are shown
  as estimated, and the charges are created when the order gets its client.
* A cancelled booking costs nothing: its charge is brought to zero; payments stay and are paid
  back separately (§ 14.1.5). TODO(spec): a fee for a cancellation or a no-show, and the charge of
  a booking cancelled automatically (no employee to record it under).
"""

from datetime import UTC
from decimal import Decimal
from typing import Any
from uuid import UUID

from c4rsys.finance import charges
from c4rsys.finance.models import ChargeEntry
from c4rsys.orders import services as orders
from c4rsys.platform.commands import CommandContext
from c4rsys.rentals import extra_lines, pricing, rentals
from c4rsys.rentals.models import ExtraServiceLine, Rental

SOURCE = ChargeEntry.SourceType.RENTAL_BASE


def base_calculation(rental: Rental) -> tuple[str, pricing.Calculation] | None:
    """The base cost charged into the order and its kind, or None while it is not known."""
    if rental.tariff_snapshot is None or rental.state in (
        Rental.State.CANCELLED,
        Rental.State.ANNULLED,
    ):
        return None
    if rental.actual_start is not None:
        if rental.actual_end is not None:
            return "actual", rentals.calculate(rental, rental.actual_end)
        if rental.planned_end is None:
            return None
        return "planned", rentals.calculate(rental, rental.planned_end)
    if rental.planned_start is None or rental.planned_end is None:
        return None
    terms = rentals.terms_of(rental)
    return "preliminary", pricing.calculate(terms, rental.planned_start, rental.planned_end)


def _client_of(order_id: UUID) -> UUID | None:
    return orders.chargeable_client(order_id)


def charge_base(ctx: CommandContext, rental: Rental, reason: str) -> ChargeEntry | None:
    client_id = _client_of(rental.order_id)
    if client_id is None:
        return None
    found = base_calculation(rental)
    calculation: dict[str, Any]
    if found is None:
        currency = charges.charged_currency(SOURCE, rental.id)
        if currency is None:
            return None
        amount, calculation = Decimal(0), {"kind": "none"}
    else:
        kind, result = found
        start, end = (
            (rental.actual_start, rental.actual_end or rental.planned_end)
            if rental.actual_start is not None
            else (rental.planned_start, rental.planned_end)
        )
        snapshot = rental.tariff_snapshot or {}
        amount, currency = result.base_amount, result.currency
        calculation = {
            "kind": kind,
            "start": start.astimezone(UTC).isoformat() if start else None,
            "end": end.astimezone(UTC).isoformat() if end else None,
            "billable_minutes": int(result.billable.total_seconds() // 60),
            "free_minutes": int(result.free.total_seconds() // 60),
            "units": result.units,
            "rate": str(result.rate) if result.rate is not None else None,
            "base_amount": str(result.base_amount),
            "tariff_id": snapshot.get("tariff_id"),
            "tariff_version": snapshot.get("tariff_version"),
        }
    return charges.set_charge(
        ctx,
        company_id=rental.company_id,
        order_id=rental.order_id,
        client_id=client_id,
        source_type=SOURCE,
        source_id=rental.id,
        amount=amount,
        currency=currency,
        reason=reason,
        calculation=calculation,
    )


def sync(ctx: CommandContext, rental: Rental, reason: str) -> ChargeEntry | None:
    """The base cost and the linked services of the rental brought to their current amounts."""
    entry = charge_base(ctx, rental, reason)
    extra_lines.charge_linked(ctx, rental, reason)
    return entry


def on_client_assigned(ctx: CommandContext, company_id: UUID, order_id: UUID) -> None:
    """Handler for orders: the order got its client — what was estimated is charged (Р25)."""
    for rental in Rental.objects.select_for_update().filter(
        company_id=company_id, order_id=order_id
    ):
        charge_base(ctx, rental, "client_assigned")
    for line in ExtraServiceLine.objects.select_for_update().filter(
        company_id=company_id, order_id=order_id
    ):
        extra_lines.charge_line(ctx, line, "client_assigned")


def estimated_for_order(company_id: UUID, order_id: UUID) -> dict[str, Decimal]:
    """Provider for orders: the amounts of an order without a client or of a preliminary one,
    not charged (Р25, З2)."""
    if orders.chargeable_client(order_id) is not None:
        return {}
    result: dict[str, Decimal] = {}
    for rental in Rental.objects.filter(company_id=company_id, order_id=order_id):
        found = base_calculation(rental)
        if found is not None:
            calc = found[1]
            result[calc.currency] = result.get(calc.currency, Decimal(0)) + calc.base_amount
    for currency, amount in extra_lines.estimated_for_order(company_id, order_id).items():
        result[currency] = result.get(currency, Decimal(0)) + amount
    return result
