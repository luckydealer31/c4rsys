"""Additional services in a rental and in an order (ТЗ § 12.1.4, § 13.4.5–13.4.15; customer's
decisions Т8, У1–У12 of 27.10.2026). Equipment by instance comes later (decision Т1).

Each addition is its own line with a snapshot of the price (§ 13.4.8).

* A service linked to a rental has the rental's period (§ 13.4.9): the planned period of a
  booking, after the issue the actual one; it starts with the issue and ends with the return,
  without marks of its own (decisions У4–У5). It is charged into the order with the rental by the
  same period: the planned one while the end is planned, the actual one after the return; without
  a planned end it is priced up to now and charged at the return (decision Р16).
* A standalone service lives in the order with its own location (§ 13.4.14), its own period for a
  time service and its own actual start and completion (§ 13.4.11, decision У8); it is charged at
  once and recharged at every change.
* Unlinking makes a linked line standalone with the rental's current period; after the issue it
  is already started (decision У6). A cancelled booking leaves its services standalone in the
  same order (§ 13.4.15).
* A service is cancelled only before its start (§ 13.4.15). Quantity changes until the service is
  completed, and for a linked one until the formalization of the rental (decision У7).
* A time service has its own free time by the rules of § 13.3.2 (decision У9).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from c4rsys.finance import charges
from c4rsys.finance.charges import charge_ids_for_source
from c4rsys.finance.models import ChargeEntry
from c4rsys.orders import services as orders
from c4rsys.orders.services import OperationSummary
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.money import round_money
from c4rsys.rentals import extras, issue_policies, pricing
from c4rsys.rentals.models import (
    ExtraService,
    ExtraServiceFreeInterval,
    ExtraServiceLine,
    Rental,
)

State = ExtraServiceLine.State
SOURCE = ChargeEntry.SourceType.RENTAL_SERVICE
TIME = ExtraService.Pricing.TIME
QUANTITY = ExtraService.Pricing.QUANTITY


# --- Period and price ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime | None  # the shown end; None — a rental without a planned end
    priced_to: datetime  # the end of the calculation: now for an issued rental not returned yet
    kind: str  # preliminary (booking), planned, current (no planned end yet) or actual


def _rental(line: ExtraServiceLine) -> Rental | None:
    if line.rental_id is None:
        return None
    return Rental.objects.get(pk=line.rental_id)


def rental_period(rental: Rental, now: datetime) -> Period | None:
    """The period of a rental for its linked services (decisions У4–У5)."""
    if rental.actual_start is not None:
        if rental.actual_end is not None:
            return Period(rental.actual_start, rental.actual_end, rental.actual_end, "actual")
        if rental.planned_end is not None:
            return Period(rental.actual_start, rental.planned_end, rental.planned_end, "planned")
        return Period(rental.actual_start, None, max(now, rental.actual_start), "current")
    if rental.planned_start is not None and rental.planned_end is not None:
        return Period(rental.planned_start, rental.planned_end, rental.planned_end, "preliminary")
    return None


def period_of(line: ExtraServiceLine, now: datetime) -> Period | None:
    rental = _rental(line)
    if rental is not None:
        return rental_period(rental, now)
    if line.period_start is not None and line.period_end is not None:
        return Period(line.period_start, line.period_end, line.period_end, "planned")
    return None


def location_of(line: ExtraServiceLine) -> UUID | None:
    rental = _rental(line)
    return extras.rental_location(rental) if rental is not None else line.location_id


def terms_of(line: ExtraServiceLine) -> pricing.TariffTerms:
    """A time service priced as a tariff of one range: its rate applies to all units; a day is
    24 hours from the start of the period (decisions Т7, У12)."""
    snap = line.snapshot
    return pricing.TariffTerms(
        unit=snap["unit"],
        day_mode="from_issue" if snap["unit"] == "day" else None,
        currency=snap["currency"],
        minor_units=int(snap["currency_minor_units"]),
        ranges=(pricing.PriceRange(1, None, Decimal(snap["price"])),),
        zone=issue_policies.zone_of(line.company_id, location_of(line)),
    )


def free_of(line: ExtraServiceLine) -> list[pricing.Interval]:
    return [
        pricing.Interval(i.start, i.end)
        for i in ExtraServiceFreeInterval.objects.filter(line_id=line.id).order_by("start")
    ]


@dataclass(frozen=True)
class Price:
    amount: Decimal
    currency: str
    kind: str
    units: int | None = None
    billable_minutes: int | None = None
    free_minutes: int | None = None
    rate: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "amount": str(self.amount),
            "currency": self.currency,
            "units": self.units,
            "billable_minutes": self.billable_minutes,
            "free_minutes": self.free_minutes,
            "rate": str(self.rate) if self.rate is not None else None,
        }


def price_of(line: ExtraServiceLine, now: datetime | None = None) -> Price | None:
    """The cost of the line by its snapshot: once — the price; by quantity — price × quantity
    (free time does not reduce it, § 13.4.6); by duration — the units of the period after its free
    time, a part of a unit is a whole unit (§ 13.4.10). A cancelled line costs nothing."""
    snap = line.snapshot
    minor = int(snap["currency_minor_units"])
    unit_price = Decimal(snap["price"])
    if line.state == State.CANCELLED:
        return Price(round_money(Decimal(0), minor), snap["currency"], "cancelled")
    if line.pricing != TIME:
        quantity = line.quantity if line.pricing == QUANTITY else 1
        return Price(round_money(unit_price * (quantity or 0), minor), snap["currency"], "fixed")
    period = period_of(line, now or timezone.now())
    if period is None:
        return None
    intervals = free_of(line)
    if period.kind in ("current", "planned"):
        intervals = pricing.clipped(intervals, period.start, period.priced_to)
    result = pricing.calculate(terms_of(line), period.start, period.priced_to, intervals)
    return Price(
        result.base_amount,
        result.currency,
        period.kind,
        units=result.units,
        billable_minutes=int(result.billable.total_seconds() // 60),
        free_minutes=int(result.free.total_seconds() // 60),
        rate=result.rate,
    )


# --- Charges ------------------------------------------------------------------------------------


def charge_line(ctx: CommandContext, line: ExtraServiceLine, reason: str) -> None:
    """A line is charged as soon as its cost is known: a time service linked to a rental without
    a planned end — at the return (decision Р16). An order without a client is charged when it
    gets one; until then the amount is estimated (decision Р25)."""
    client_id = orders.chargeable_client(line.order_id)
    if client_id is None:
        return
    price = price_of(line)
    if price is None or price.kind == "current":
        return
    charges.set_charge(
        ctx,
        company_id=line.company_id,
        order_id=line.order_id,
        client_id=client_id,
        source_type=SOURCE,
        source_id=line.id,
        amount=price.amount,
        currency=price.currency,
        reason=reason,
        calculation={
            **price.as_dict(),
            "service_id": line.snapshot.get("service_id"),
            "service_version": line.snapshot.get("service_version"),
            "quantity": line.quantity,
        },
    )


def charge_linked(ctx: CommandContext, rental: Rental, reason: str) -> None:
    """Called with every recalculation of the rental (§ 14.5.3, decision Р16)."""
    for line in ExtraServiceLine.objects.select_for_update().filter(rental_id=rental.id):
        charge_line(ctx, line, reason)


def estimated_for_order(company_id: UUID, order_id: UUID) -> dict[str, Decimal]:
    """The cost of the lines of an order without a client (decision Р25)."""
    result: dict[str, Decimal] = {}
    for line in ExtraServiceLine.objects.filter(company_id=company_id, order_id=order_id):
        price = price_of(line)
        if price is not None and price.kind != "current":
            result[price.currency] = result.get(price.currency, Decimal(0)) + price.amount
    return result


# --- Reading ------------------------------------------------------------------------------------


def display_state(line: ExtraServiceLine) -> str:
    """A linked service follows the rental: planned while booked, started after the issue,
    completed with the return (decision У5)."""
    rental = _rental(line)
    if rental is None:
        return line.state
    if rental.state == Rental.State.ACTIVE:
        return State.STARTED
    if rental.state == Rental.State.RETURNED:
        return State.COMPLETED
    return State.PLANNED


def _finished(line: ExtraServiceLine) -> bool:
    rental = _rental(line)
    if rental is None:
        return line.state in (State.COMPLETED, State.CANCELLED)
    return rental.state in (Rental.State.CANCELLED, Rental.State.ANNULLED) or (
        rental.state == Rental.State.RETURNED and rental.formalization_completed_at is not None
    )


def _utc(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat() if moment else None


def _free_payload(line: ExtraServiceLine) -> list[dict[str, Any]]:
    return [
        {
            "id": str(i.id),
            "start": _utc(i.start),
            "end": _utc(i.end),
            "units": i.units,
            "comment": i.comment,
        }
        for i in ExtraServiceFreeInterval.objects.filter(line_id=line.id).order_by("start")
    ]


def payload(line: ExtraServiceLine) -> dict[str, Any]:
    period = period_of(line, timezone.now())
    price = price_of(line)
    snap = line.snapshot
    return {
        "id": str(line.id),
        "order_id": str(line.order_id),
        "service_id": str(line.service_id),
        "name": snap["name"],
        "pricing": line.pricing,
        "unit": snap.get("unit"),
        "price": snap["price"],
        "currency": snap["currency"],
        "quantity": line.quantity,
        "rental_id": str(line.rental_id) if line.rental_id else None,
        "origin_rental_id": str(line.origin_rental_id) if line.origin_rental_id else None,
        "location_id": str(location_of(line) or "") or None,
        "time_zone": issue_policies.zone_of(line.company_id, location_of(line)),
        "period": {"start": _utc(period.start), "end": _utc(period.end), "kind": period.kind}
        if period and line.pricing == TIME
        else None,
        "state": display_state(line),
        "actual_start": _utc(line.actual_start),
        "actual_end": _utc(line.actual_end),
        "unlinked_at": _utc(line.unlinked_at),
        "cancelled_at": _utc(line.cancelled_at),
        "cancel_reason": line.cancel_reason,
        "calculation": price.as_dict() if price else None,
        "free_intervals": _free_payload(line) if line.pricing == TIME else [],
        "finished": _finished(line),
        "version": line.version,
    }


def lines_of_order(company_id: UUID, order_id: UUID) -> list[ExtraServiceLine]:
    return list(
        ExtraServiceLine.objects.filter(company_id=company_id, order_id=order_id).order_by(
            "created_at"
        )
    )


def lines_of_rental(rental: Rental) -> list[ExtraServiceLine]:
    """Lines linked to the rental now and lines unlinked from it."""
    return list(
        ExtraServiceLine.objects.filter(
            Q(rental_id=rental.id) | Q(origin_rental_id=rental.id)
        ).order_by("created_at")
    )


def get_line(company_id: UUID, line_id: UUID) -> ExtraServiceLine:
    line = ExtraServiceLine.objects.filter(company_id=company_id, pk=line_id).first()
    if line is None:
        raise errors.not_found()
    return line


def operations_for_order(company_id: UUID, order_id: UUID) -> list[OperationSummary]:
    """Lines of the order as the order sees them; the order closes after a standalone service is
    completed or cancelled (§ 13.4.11), a linked one finishes with its rental."""
    return [
        OperationSummary(
            type="service",
            id=line.id,
            state=display_state(line),
            location_id=location_of(line),
            finished=_finished(line),
        )
        for line in lines_of_order(company_id, order_id)
    ]


def history_objects(line: ExtraServiceLine) -> list[tuple[str, str]]:
    return [("service_line", str(line.id))] + [
        ("charge_entry", str(c)) for c in charge_ids_for_source(SOURCE, line.id)
    ]


def history_for_order(company_id: UUID, order_id: UUID) -> list[tuple[str, str]]:
    objects: list[tuple[str, str]] = []
    for line in lines_of_order(company_id, order_id):
        objects += history_objects(line)
    return objects


# --- Commands -----------------------------------------------------------------------------------


def _snapshot(line: ExtraServiceLine) -> dict[str, Any]:
    return {
        "service": line.snapshot.get("service_id"),
        "name": line.snapshot.get("name"),
        "order": str(line.order_id),
        "rental": str(line.rental_id) if line.rental_id else None,
        "location": str(line.location_id) if line.location_id else None,
        "quantity": line.quantity,
        "period_start": _utc(line.period_start),
        "period_end": _utc(line.period_end),
        "state": line.state,
        "actual_start": _utc(line.actual_start),
        "actual_end": _utc(line.actual_end),
    }


def _audit(
    ctx: CommandContext, line: ExtraServiceLine, action: str, before: dict[str, Any] | None
) -> None:
    after = _snapshot(line)
    if before is not None:
        ctx.audit(
            action,
            object_type="service_line",
            object_id=line.id,
            before={k: v for k, v in before.items() if v != after[k]},
            after={k: v for k, v in after.items() if v != before[k]},
        )
    else:
        ctx.audit(action, object_type="service_line", object_id=line.id, after=after)


def _quantity(line_pricing: str, quantity: int | None) -> int | None:
    if line_pricing != QUANTITY:
        if quantity is not None:
            raise errors.validation_error("quantity", code="QUANTITY_NOT_APPLICABLE")
        return None
    if quantity is None or quantity < 1:
        raise errors.validation_error("quantity", code="QUANTITY_REQUIRED")
    return quantity


def _period(
    line_pricing: str, start: datetime | None, end: datetime | None
) -> tuple[datetime | None, datetime | None]:
    if line_pricing != TIME:
        if start is not None or end is not None:
            raise errors.validation_error("period_start", code="PERIOD_NOT_APPLICABLE")
        return None, None
    if start is None or end is None:
        raise errors.validation_error(
            "period_end" if start else "period_start", code="FIELD_REQUIRED"
        )
    if end <= start:
        raise errors.validation_error("period_end", code="PERIOD_INVALID")
    return start, end


def _locked_rental(company_id: UUID, rental_id: UUID) -> Rental:
    rental = Rental.objects.select_for_update().filter(company_id=company_id, pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    return rental


def _rental_open(rental: Rental) -> None:
    """A linked service changes while the rental may: booked, issued, returned until the
    formalization is completed (decision У7)."""
    if rental.state not in (Rental.State.BOOKED, Rental.State.ACTIVE, Rental.State.RETURNED):
        raise errors.business_rule("RENTAL_SERVICES_CLOSED", state=rental.state)
    if rental.formalization_completed_at is not None:
        raise errors.business_rule("RENTAL_FORMALIZATION_COMPLETED")
    orders.ensure_open(rental.company_id, rental.order_id)


def _bump(rental: Rental) -> None:
    rental.version += 1
    rental.save(update_fields=["version", "updated_at"])


def _active_service(company_id: UUID, service_id: UUID) -> ExtraService:
    service = (
        ExtraService.objects.select_related("currency")
        .filter(company_id=company_id, pk=service_id)
        .first()
    )
    if service is None:
        raise errors.validation_error("service_id", code="SERVICE_UNKNOWN")
    if service.state != ExtraService.State.ACTIVE:
        raise errors.business_rule("SERVICE_ARCHIVED", field="service_id")
    return service


def add_linked(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    *,
    service_id: UUID,
    quantity: int | None,
    expected_version: int,
) -> ExtraServiceLine:
    """A service linked to the rental, from the card of the rental (§ 13.4.5)."""
    rental = _locked_rental(company_id, rental_id)
    if rental.version != expected_version:
        raise errors.version_conflict()
    _rental_open(rental)
    service = _active_service(company_id, service_id)
    if not extras.for_rental(rental).filter(pk=service.id).exists():
        raise errors.business_rule("SERVICE_NOT_APPLICABLE", field="service_id")
    line = ExtraServiceLine.objects.create(
        company_id=company_id,
        order_id=rental.order_id,
        service=service,
        pricing=service.pricing,
        snapshot=extras.line_snapshot(service),
        currency_id=service.currency_id,
        rental=rental,
        quantity=_quantity(service.pricing, quantity),
        created_by_user_id=ctx.actor.user_id,
    )
    _bump(rental)
    _audit(ctx, line, "service_line.added", None)
    charge_line(ctx, line, "added")
    return line


@dataclass(frozen=True)
class Standalone:
    service_id: UUID
    location_id: UUID
    quantity: int | None
    period_start: datetime | None
    period_end: datetime | None
    order_id: UUID | None  # None: a new order of `client_id` with only this service
    client_id: UUID | None
    responsible_employment_id: UUID | None


def add_standalone(ctx: CommandContext, company_id: UUID, data: Standalone) -> ExtraServiceLine:
    """A standalone service in an open order of a client or in a new order with only this
    service (§ 12.1.4; decision У1). The location of the service is chosen; its revenue belongs
    there (§ 13.4.14)."""
    from c4rsys.companies import locations
    from c4rsys.parties import services as clients

    locations.require_usable(company_id, data.location_id, "location_id")
    service = _active_service(company_id, data.service_id)
    if not extras.at_location(company_id, data.location_id).filter(pk=service.id).exists():
        raise errors.business_rule("SERVICE_NOT_APPLICABLE", field="service_id")
    quantity = _quantity(service.pricing, data.quantity)
    start, end = _period(service.pricing, data.period_start, data.period_end)
    if data.order_id is not None:
        order = orders.open_order_of_client(company_id, data.order_id, preliminary_ok=True)
    else:
        if data.client_id is None:
            raise errors.validation_error("client_id", code="CLIENT_REQUIRED")
        client = clients.get_client(company_id, data.client_id)
        order = orders.create_order(
            ctx,
            company_id=company_id,
            client_id=client.id,
            responsible_employment_id=data.responsible_employment_id,
        )
    line = ExtraServiceLine.objects.create(
        company_id=company_id,
        order=order,
        service=service,
        pricing=service.pricing,
        snapshot=extras.line_snapshot(service),
        currency_id=service.currency_id,
        location_id=data.location_id,
        quantity=quantity,
        period_start=start,
        period_end=end,
        created_by_user_id=ctx.actor.user_id,
    )
    _audit(ctx, line, "service_line.added", None)
    charge_line(ctx, line, "added")
    return line


def _locked_line(company_id: UUID, line_id: UUID, expected_version: int | None) -> ExtraServiceLine:
    line = (
        ExtraServiceLine.objects.select_for_update()
        .filter(company_id=company_id, pk=line_id)
        .first()
    )
    if line is None:
        raise errors.not_found()
    if expected_version is not None and line.version != expected_version:
        raise errors.version_conflict()
    # A service of a preliminary order changes and is cancelled, it does not start (§ 12.2.1).
    orders.ensure_editable(company_id, line.order_id)
    return line


def _changeable(line: ExtraServiceLine) -> Rental | None:
    """The rental of a linked line, checked open; a standalone line until its completion."""
    rental = None
    if line.rental_id is not None:
        rental = _locked_rental(line.company_id, line.rental_id)
        _rental_open(rental)
    elif line.state in (State.COMPLETED, State.CANCELLED):
        raise errors.business_rule("SERVICE_LINE_CLOSED", state=line.state)
    return rental


def _save(line: ExtraServiceLine, rental: Rental | None) -> None:
    line.version += 1
    line.save()
    if rental is not None:
        _bump(rental)


def change(
    ctx: CommandContext,
    company_id: UUID,
    line_id: UUID,
    *,
    quantity: int | None,
    period_start: datetime | None,
    period_end: datetime | None,
    expected_version: int,
) -> ExtraServiceLine:
    """Quantity and, for a standalone time service, its period (decision У7). The calculated
    number of time units is never entered by hand (§ 13.4.9)."""
    line = _locked_line(company_id, line_id, expected_version)
    rental = _changeable(line)
    before = _snapshot(line)
    if quantity is not None:
        line.quantity = _quantity(line.pricing, quantity)
    if period_start is not None or period_end is not None:
        if rental is not None:
            # § 13.4.9: a linked service has the rental's period; unlink it for another one.
            raise errors.business_rule("SERVICE_PERIOD_OF_RENTAL", field="period_start")
        start, end = _period(
            line.pricing, period_start or line.period_start, period_end or line.period_end
        )
        if start is None or end is None:
            raise RuntimeError("A time service has its period")
        outside = ExtraServiceFreeInterval.objects.filter(line_id=line.id).exclude(
            start__gte=start, end__lte=end
        )
        if outside.exists():
            raise errors.business_rule(
                "FREE_INTERVAL_OUTSIDE_SERVICE",
                field="period_end",
                intervals=[str(i.id) for i in outside],
            )
        line.period_start, line.period_end = start, end
    _save(line, rental)
    _audit(ctx, line, "service_line.changed", before)
    charge_line(ctx, line, "changed")
    return line


def _moment(at: datetime | None, field: str) -> datetime:
    moment = at or timezone.now()
    if moment > timezone.now():
        raise errors.business_rule("FUTURE_TIME_NOT_ALLOWED", field=field)
    return moment


def start(
    ctx: CommandContext, company_id: UUID, line_id: UUID, at: datetime | None, expected_version: int
) -> ExtraServiceLine:
    """The actual start of a standalone service (§ 13.4.11). The start of its period does not
    start it by itself."""
    line = _locked_line(company_id, line_id, expected_version)
    orders.ensure_open(company_id, line.order_id)
    if line.rental_id is not None:
        raise errors.business_rule("SERVICE_FOLLOWS_RENTAL")
    if line.state != State.PLANNED:
        raise errors.business_rule("SERVICE_NOT_PLANNED", state=line.state)
    before = _snapshot(line)
    line.state = State.STARTED
    line.actual_start = _moment(at, "actual_start")
    _save(line, None)
    _audit(ctx, line, "service_line.started", before)
    return line


def complete(
    ctx: CommandContext, company_id: UUID, line_id: UUID, at: datetime | None, expected_version: int
) -> ExtraServiceLine:
    """The completion of the service actually rendered (§ 13.4.11)."""
    line = _locked_line(company_id, line_id, expected_version)
    if line.rental_id is not None:
        raise errors.business_rule("SERVICE_FOLLOWS_RENTAL")
    if line.state != State.STARTED or line.actual_start is None:
        raise errors.business_rule("SERVICE_NOT_STARTED", state=line.state)
    moment = _moment(at, "actual_end")
    if moment < line.actual_start:
        raise errors.validation_error("actual_end", code="PERIOD_INVALID")
    before = _snapshot(line)
    line.state = State.COMPLETED
    line.actual_end = moment
    _save(line, None)
    _audit(ctx, line, "service_line.completed", before)
    return line


def cancel(
    ctx: CommandContext, company_id: UUID, line_id: UUID, reason: str, expected_version: int
) -> ExtraServiceLine:
    """Only before the actual start (§ 13.4.15): a standalone planned service, a linked one while
    the rental is a booking. The charge is brought to zero; the line stays."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    line = _locked_line(company_id, line_id, expected_version)
    rental = None
    if line.rental_id is not None:
        rental = _locked_rental(company_id, line.rental_id)
        if rental.state != Rental.State.BOOKED:
            raise errors.business_rule("SERVICE_ALREADY_STARTED", state=display_state(line))
    elif line.state != State.PLANNED:
        raise errors.business_rule("SERVICE_ALREADY_STARTED", state=line.state)
    before = _snapshot(line)
    if line.rental_id is not None:
        # The link is kept in the history; a cancelled line is standalone with its last place.
        line.origin_rental_id, line.rental_id = line.rental_id, None
        line.location_id = extras.rental_location(rental) if rental else None
        if line.pricing == TIME and rental is not None:
            line.period_start, line.period_end = rental.planned_start, rental.planned_end
    line.state = State.CANCELLED
    line.cancelled_at = timezone.now()
    line.cancel_reason = reason.strip()
    _save(line, rental)
    _audit(ctx, line, "service_line.cancelled", before)
    charge_line(ctx, line, "cancelled")
    return line


def _detach(line: ExtraServiceLine, rental: Rental, now: datetime) -> None:
    """The linked line becomes standalone in the same order with the rental's current period
    (decision У6): after the issue it is already started, after the return completed."""
    period = rental_period(rental, now)
    if period is None:
        raise RuntimeError("A rental with services has its period")
    line.origin_rental_id, line.rental_id = rental.id, None
    line.location_id = extras.rental_location(rental)
    line.unlinked_at = now
    if line.pricing == TIME:
        end = period.end or period.priced_to
        if end <= period.start:
            raise errors.business_rule("SERVICE_PERIOD_EMPTY", field="period_end")
        line.period_start, line.period_end = period.start, end
    if rental.actual_start is not None:
        line.state = State.STARTED
        line.actual_start = rental.actual_start
        if rental.actual_end is not None:
            line.state = State.COMPLETED
            line.actual_end = rental.actual_end


def unlink(
    ctx: CommandContext, company_id: UUID, line_id: UUID, expected_version: int
) -> ExtraServiceLine:
    """Unlinks the service from the rental by a separate action: it is the same line, not a
    cancellation or a new addition; its period may then be changed separately (§ 13.4.9)."""
    line = _locked_line(company_id, line_id, expected_version)
    if line.rental_id is None:
        raise errors.business_rule("SERVICE_NOT_LINKED")
    rental = _locked_rental(company_id, line.rental_id)
    _rental_open(rental)
    before = _snapshot(line)
    _detach(line, rental, timezone.now())
    _save(line, rental)
    _audit(ctx, line, "service_line.unlinked", before)
    charge_line(ctx, line, "unlinked")
    return line


def detach_on_cancel(ctx: CommandContext | None, rental: Rental) -> None:
    """A cancelled booking leaves its services standalone in the same order with their terms and
    periods (§ 13.4.15), also at the automatic cancellation."""
    now = timezone.now()
    for line in ExtraServiceLine.objects.select_for_update().filter(rental_id=rental.id):
        before = _snapshot(line)
        _detach(line, rental, now)
        line.version += 1
        line.save()
        if ctx is not None:
            _audit(ctx, line, "service_line.unlinked", before)
            charge_line(ctx, line, "booking_cancelled")


# --- The order cancelled, the rental annulled or transferred (§ 12.2.1, § 12.3.7; decisions
# З4–З6) ------------------------------------------------------------------------------------------


def _cancelled(
    ctx: CommandContext, line: ExtraServiceLine, reason: str, rental: Rental | None
) -> None:
    before = _snapshot(line)
    if rental is not None:
        line.origin_rental_id, line.rental_id = rental.id, None
        line.location_id = extras.rental_location(rental)
    line.state = State.CANCELLED
    line.cancelled_at = timezone.now()
    line.cancel_reason = reason
    line.version += 1
    line.save()
    _audit(ctx, line, "service_line.cancelled", before)
    charge_line(ctx, line, "cancelled")


def cancel_for_order(ctx: CommandContext, company_id: UUID, order_id: UUID, reason: str) -> None:
    """With the whole order the services not started are cancelled; a started or completed one
    keeps the order from cancelling (§ 12.2.1). Linked services of cancelled bookings are already
    standalone here."""
    for line in ExtraServiceLine.objects.select_for_update().filter(
        company_id=company_id, order_id=order_id
    ):
        if line.rental_id is not None:
            rental = _locked_rental(company_id, line.rental_id)
            if rental.state not in (Rental.State.CANCELLED, Rental.State.ANNULLED):
                raise errors.business_rule("ORDER_HAS_ACTIVE_RENTAL", rental_id=str(rental.id))
            continue
        if line.state == State.CANCELLED:
            continue
        if line.state != State.PLANNED:
            raise errors.business_rule("ORDER_HAS_STARTED_OPERATIONS", line_id=str(line.id))
        _cancelled(ctx, line, reason, None)


def annul_linked(ctx: CommandContext, rental: Rental, reason: str) -> None:
    """A wrong rental is annulled with its linked services: their charges go to zero, the lines
    and their history stay (decision З5)."""
    for line in ExtraServiceLine.objects.select_for_update().filter(rental_id=rental.id):
        _cancelled(ctx, line, reason, rental)


def move_linked(ctx: CommandContext, rental: Rental, order_id: UUID) -> None:
    """Linked services go with the rental to its new order with their charges (decision З6)."""
    for line in ExtraServiceLine.objects.select_for_update().filter(rental_id=rental.id):
        before = _snapshot(line)
        line.order_id = order_id
        line.version += 1
        line.save()
        _audit(ctx, line, "service_line.moved", before)
        charges.move_source(
            ctx, source_type=SOURCE, source_id=line.id, order_id=order_id, reason="transferred"
        )


# --- Free time of a time service (§ 13.4.6; decision У9) -----------------------------------------


def _free_bounds(line: ExtraServiceLine, rental: Rental | None) -> tuple[datetime, datetime]:
    """Inside the period of the service: a linked one — inside the issued rental by the rules of
    the rental's free time (decision Т4)."""
    if rental is not None:
        if rental.actual_start is None:
            raise errors.business_rule("FREE_TIME_NOT_ALLOWED", state=rental.state)
        return rental.actual_start, rental.actual_end or rental.planned_end or timezone.now()
    if line.period_start is None or line.period_end is None:
        raise RuntimeError("A standalone time service has its period")
    return line.period_start, line.period_end


def add_free(
    ctx: CommandContext,
    company_id: UUID,
    line_id: UUID,
    *,
    start: datetime,
    end: datetime | None,
    units: int | None,
    comment: str,
    expected_version: int | None,
) -> ExtraServiceFreeInterval:
    line = _locked_line(company_id, line_id, expected_version)
    if line.pricing != TIME:
        # § 13.4.6: the quantity of a piece service is not reduced by free time.
        raise errors.business_rule("FREE_TIME_NOT_FOR_SERVICE", field="line_id")
    rental = _changeable(line)
    if (end is None) == (units is None):
        raise errors.validation_error("end", code="FREE_END_OR_UNITS")
    if units is not None:
        try:
            end = pricing.advance(terms_of(line), start, units)
        except pricing.PricingError as exc:
            raise errors.validation_error("units", code=exc.code) from exc
    if end is None:
        raise RuntimeError("Checked above: the end or the units are given")
    if end <= start:
        raise errors.validation_error("end", code="PERIOD_INVALID")
    low, high = _free_bounds(line, rental)
    if start < low or end > high:
        raise errors.business_rule(
            "FREE_INTERVAL_OUTSIDE_SERVICE",
            field="end" if end > high else "start",
            start=low.isoformat(),
            end=high.isoformat(),
        )
    item = ExtraServiceFreeInterval.objects.create(
        company_id=company_id,
        line=line,
        start=start,
        end=end,
        units=units,
        comment=comment,
        created_by_user_id=ctx.actor.user_id,
    )
    _save(line, rental)
    ctx.audit(
        "service_line.free_time_added",
        object_type="service_line",
        object_id=line.id,
        after={"id": str(item.id), "start": _utc(start), "end": _utc(end), "units": units},
    )
    charge_line(ctx, line, "free_time")
    return item


def remove_free(
    ctx: CommandContext, company_id: UUID, line_id: UUID, interval_id: UUID, expected_version: int
) -> None:
    line = _locked_line(company_id, line_id, expected_version)
    rental = _changeable(line)
    item = (
        ExtraServiceFreeInterval.objects.select_for_update()
        .filter(line_id=line.id, pk=interval_id)
        .first()
    )
    if item is None:
        raise errors.not_found()
    before = {"id": str(item.id), "start": _utc(item.start), "end": _utc(item.end)}
    item.delete()
    _save(line, rental)
    ctx.audit(
        "service_line.free_time_removed",
        object_type="service_line",
        object_id=line.id,
        before=before,
    )
    charge_line(ctx, line, "free_time")


def free_outside(rental: Rental, end: datetime) -> list[str]:
    """Free intervals of linked services reaching past a new end of the rental: like the rental's
    own, a conflict to settle by hand (§ 13.3.2)."""
    return [
        str(i)
        for i in ExtraServiceFreeInterval.objects.filter(
            line__rental_id=rental.id, end__gt=end
        ).values_list("id", flat=True)
    ]


def time_lines(rental: Rental) -> list[ExtraServiceLine]:
    return list(
        ExtraServiceLine.objects.filter(rental_id=rental.id, pricing=TIME).order_by("created_at")
    )
