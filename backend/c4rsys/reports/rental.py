"""The starter dashboard «Rental and fleet» (ТЗ § 21.4.1–21.4.2, § 21.1.2–21.1.4; customer's
decisions О-3, О-6 of 29.10.2026).

* Rental time, occupancy and service count the part of each actual interval inside the period
  (§ 21.1.2). Utilisation is the share of rental time in the accounting time of the vehicle; the
  service time is not taken out of it; an overlap of rental and service is counted once in the
  total occupancy, so utilisation never exceeds 100% (§ 21.4.1). Idle time is the accounting time
  without rental and service. A planned booking is future occupancy, not utilisation.
* The accounting time of a vehicle runs from its card's creation (О-6) to the end of the period
  or now.
* The base cost of each rental is spread over the days by its actual time; before the return it
  is preliminary (§ 21.1.3). One-time charges are not spread.
* Payback (§ 21.4.2; decision О-3) only for own vehicles: invested — payouts of the company
  linked to the vehicle; received — the payments of orders shared by the vehicle's part of their
  charges. Without the cost of acquisition — «not enough data».
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Max, Min
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.platform.commands import Actor
from c4rsys.reports.overview import Block, _accounts
from c4rsys.reports.scope import Area, Filters, area

CENT = Decimal("0.01")
RENTAL_CHARGES = ("rental_base", "rental_mileage", "rental_manual", "rental_service")


def _hours(delta: timedelta) -> Decimal:
    return (Decimal(delta.total_seconds()) / Decimal(3600)).quantize(Decimal("0.1"))


@dataclass(frozen=True)
class Span:
    start: datetime
    end: datetime

    def clip(self, start: datetime, end: datetime) -> "Span | None":
        a, b = max(self.start, start), min(self.end, end)
        return Span(a, b) if a < b else None


def _union(spans: list[Span]) -> timedelta:
    total = timedelta(0)
    current: Span | None = None
    for s in sorted(spans, key=lambda x: x.start):
        if current is None or s.start > current.end:
            if current is not None:
                total += current.end - current.start
            current = s
        elif s.end > current.end:
            current = Span(current.start, s.end)
    if current is not None:
        total += current.end - current.start
    return total


def _money(rows: dict[str, Decimal]) -> list[dict[str, Any]]:
    return [{"currency": c, "amount": str(v.quantize(CENT))} for c, v in sorted(rows.items())]


def _rentals(company: Company, where: Area, filters: Filters, tariff_id: UUID | None) -> Any:
    from c4rsys.rentals.models import Rental

    found = Rental.objects.filter(company=company).select_related("vehicle_card")
    if not where.whole:
        found = found.filter(issue_location_id__in=where.location_ids)
    if filters.vehicle_id:
        found = found.filter(vehicle_card_id=filters.vehicle_id)
    if tariff_id:
        found = found.filter(tariff_id=tariff_id)
    return found


def _rental_blocks(
    company: Company, where: Area, filters: Filters, tariff_id: UUID | None, now: datetime
) -> list[Block]:
    from c4rsys.finance.models import ChargeEntry, PaymentAllocation
    from c4rsys.orders import services as orders
    from c4rsys.orders.models import Order
    from c4rsys.rentals.models import Rental

    rentals = _rentals(company, where, filters, tariff_id)
    start, end = filters.start_at, min(filters.end_at, now)
    created = rentals.filter(
        booking_number__isnull=False,
        created_at__gte=filters.start_at,
        created_at__lt=filters.end_at,
    )
    by_state: dict[str, int] = defaultdict(int)
    for state in created.values_list("state", flat=True):
        by_state[state] += 1
    started = rentals.filter(actual_start__gte=filters.start_at, actual_start__lt=filters.end_at)
    finished = rentals.filter(actual_end__gte=filters.start_at, actual_end__lt=filters.end_at)
    cancelled = rentals.filter(
        state=Rental.State.CANCELLED,
        cancelled_at__gte=filters.start_at,
        cancelled_at__lt=filters.end_at,
    )
    active = rentals.filter(state=Rental.State.ACTIVE)
    overdue = active.filter(planned_end__lt=now)
    time_in_rental = timedelta(0)
    for r in rentals.filter(actual_start__lt=end).exclude(actual_end__lte=start):
        span = Span(r.actual_start, r.actual_end or now).clip(start, end)
        if span is not None:
            time_in_rental += span.end - span.start
    ids = list(rentals.values_list("id", flat=True))
    charges: dict[str, Decimal] = defaultdict(Decimal)
    for c in ChargeEntry.objects.filter(
        company=company,
        source_type__in=("rental_base", "rental_mileage"),
        source_id__in=ids,
        created_at__gte=filters.start_at,
        created_at__lt=filters.end_at,
    ):
        charges[c.currency_id] += c.amount
    manual_ids = _manual_ids(ids)
    for c in ChargeEntry.objects.filter(
        company=company,
        source_type="rental_manual",
        source_id__in=manual_ids,
        created_at__gte=filters.start_at,
        created_at__lt=filters.end_at,
    ):
        charges[c.currency_id] += c.amount
    order_ids = set(rentals.values_list("order_id", flat=True))
    paid: dict[str, Decimal] = defaultdict(Decimal)
    for a in PaymentAllocation.objects.filter(
        company=company,
        order_id__in=order_ids,
        state=PaymentAllocation.State.ACTIVE,
        created_at__gte=filters.start_at,
        created_at__lt=filters.end_at,
    ):
        paid[a.currency_id] += a.amount
    debt: dict[str, Decimal] = defaultdict(Decimal)
    for order in Order.objects.filter(company=company, pk__in=order_ids):
        for currency, money in orders.amounts_of(order).items():
            if money.balance > 0:
                debt[currency] += money.balance
    blocks = [
        Block(
            "rental.bookings_by_state",
            "rental",
            "table",
            None,
            [{"state": s, "count": n} for s, n in sorted(by_state.items())],
            where,
            "/rentals",
        ),
        Block("rental.started", "rental", "count", started.count(), area=where, link="/rentals"),
        Block("rental.finished", "rental", "count", finished.count(), area=where, link="/rentals"),
        Block("rental.active", "rental", "count", active.count(), area=where, link="/rentals"),
        Block(
            "rental.cancelled", "rental", "count", cancelled.count(), area=where, link="/rentals"
        ),
        Block(
            "rental.overdue_returns",
            "rental",
            "count",
            overdue.count(),
            area=where,
            link="/rentals",
        ),
        Block("rental.hours", "rental", "hours", str(_hours(time_in_rental)), area=where),
        Block("rental.charges", "rental", "money", None, _money(charges), where, "/orders"),
        Block("rental.payments", "rental", "money", None, _money(paid), where, "/orders"),
        Block("rental.debt", "rental", "money", None, _money(debt), where, "/orders"),
        Block("rental.daily_cost", "rental", "table", None, _daily(rentals, filters, now), where),
    ]
    for b in blocks:
        if b.unit == "money" and not b.rows:
            b.value = 0
    return blocks


def _manual_ids(rental_ids: list[UUID]) -> list[UUID]:
    from c4rsys.rentals.models import RentalManualCharge

    return list(
        RentalManualCharge.objects.filter(rental_id__in=rental_ids).values_list("id", flat=True)
    )


def _daily(rentals: Any, filters: Filters, now: datetime) -> list[dict[str, Any]]:
    """The base cost spread over the days of actual use; preliminary until the return
    (§ 21.1.3). Corrections stay in their own period through the charges (§ 21.1.4)."""
    from c4rsys.finance.models import ChargeEntry

    start, end = filters.start_at, filters.end_at
    used = rentals.filter(actual_start__lt=end).exclude(actual_end__lte=start)
    base: dict[UUID, tuple[Decimal, str]] = {}
    for c in ChargeEntry.objects.filter(
        source_type="rental_base", source_id__in=list(used.values_list("id", flat=True))
    ):
        amount, _currency = base.get(c.source_id, (Decimal(0), c.currency_id))
        base[c.source_id] = (amount + c.amount, c.currency_id)
    days: dict[tuple[str, str, bool], Decimal] = defaultdict(Decimal)
    for r in used:
        if r.id not in base or r.actual_start is None:
            continue
        amount, currency = base[r.id]
        whole = Span(r.actual_start, r.actual_end or now)
        length = (whole.end - whole.start).total_seconds()
        if length <= 0:
            continue
        day = filters.start
        while day <= filters.end:
            bounds = filters.day_bounds(day)
            part = whole.clip(*bounds)
            if part is not None:
                share = Decimal((part.end - part.start).total_seconds()) / Decimal(length)
                days[(day.isoformat(), currency, r.actual_end is None)] += amount * share
            day += timedelta(days=1)
    return [
        {"day": d, "currency": c, "amount": str(v.quantize(CENT)), "preliminary": p}
        for (d, c, p), v in sorted(days.items())
    ]


# --- Fleet -------------------------------------------------------------------------------------


def _fleet_blocks(
    actor: Actor, company: Company, where: Area, filters: Filters, now: datetime
) -> list[Block]:
    from c4rsys.finance.models import MoneyMovement
    from c4rsys.fleet.models import OdometerReading, VehicleCard
    from c4rsys.rentals.models import Rental
    from c4rsys.service.models import ServiceOperation

    query = VehicleCard.objects.filter(company=company).exclude(relation="client")
    if not where.whole:
        query = query.filter(accounting_location_id__in=where.location_ids)
    if filters.vehicle_id:
        query = query.filter(pk=filters.vehicle_id)
    cards = list(query.order_by("name"))
    start, end = filters.start_at, min(filters.end_at, now)
    ids = [c.id for c in cards]
    rental_spans: dict[UUID, list[Span]] = defaultdict(list)
    rental_count: dict[UUID, int] = defaultdict(int)
    for r in Rental.objects.filter(vehicle_card_id__in=ids, actual_start__lt=end).exclude(
        actual_end__lte=start
    ):
        if r.actual_start is None:
            continue
        span = Span(r.actual_start, r.actual_end or now).clip(start, end)
        if span is not None:
            rental_spans[r.vehicle_card_id].append(span)
            rental_count[r.vehicle_card_id] += 1
    service_spans: dict[UUID, list[Span]] = defaultdict(list)
    for op in ServiceOperation.objects.filter(
        vehicle_card_id__in=ids, accepted_at__isnull=False, accepted_at__lt=end
    ).exclude(finished_at__lte=start):
        span = Span(op.accepted_at, op.finished_at or now).clip(start, end)  # type: ignore[arg-type]
        if span is not None:
            service_spans[op.vehicle_card_id].append(span)
    future = timedelta(0)
    for r in Rental.objects.filter(
        vehicle_card_id__in=ids, state=Rental.State.BOOKED, planned_start__lt=filters.end_at
    ).exclude(planned_end__lte=max(filters.start_at, now)):
        if r.planned_start is None or r.planned_end is None:
            continue
        span = Span(r.planned_start, r.planned_end).clip(max(filters.start_at, now), filters.end_at)
        if span is not None:
            future += span.end - span.start
    accounts = _accounts(actor, company)
    account_ids = [a.id for a in accounts] if accounts else []
    expenses: dict[UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for m in MoneyMovement.objects.filter(
        company=company,
        vehicle_id__in=ids,
        cash_account_id__in=account_ids,
        state=MoneyMovement.State.POSTED,
        direction="out",
        posted_at__gte=filters.start_at,
        posted_at__lt=filters.end_at,
    ).exclude(kind__in=("transfer", "reversal")):
        expenses[m.vehicle_id][m.currency_id] += m.amount  # type: ignore[index]
    mileage: dict[UUID, str | None] = {}
    for card_id, low, high in (
        OdometerReading.objects.filter(
            vehicle_card_id__in=ids,
            measured_at__gte=filters.start_at,
            measured_at__lt=filters.end_at,
        )
        .values("vehicle_card_id")
        .annotate(low=Min("value"), high=Max("value"))
        .values_list("vehicle_card_id", "low", "high")
    ):
        mileage[card_id] = str(high - low)
    total_accounting = timedelta(0)
    total_rental = timedelta(0)
    total_idle = timedelta(0)
    rows = []
    for card in cards:
        own_start = max(start, card.created_at)
        accounting = end - own_start if own_start < end else timedelta(0)
        rented = _union(rental_spans[card.id])
        busy = _union(rental_spans[card.id] + service_spans[card.id])
        idle = max(accounting - busy, timedelta(0))
        total_accounting += accounting
        total_rental += rented
        total_idle += idle
        share = (
            (Decimal(rented.total_seconds()) / Decimal(accounting.total_seconds()) * 100).quantize(
                Decimal("0.1")
            )
            if accounting.total_seconds() > 0
            else None
        )
        rows.append(
            {
                "vehicle": card.name,
                "vehicle_id": str(card.id),
                "rentals": rental_count[card.id],
                "rental_hours": str(_hours(rented)),
                "service_hours": str(_hours(_union(service_spans[card.id]))),
                "utilization": str(share) if share is not None else None,
                "expenses": ", ".join(
                    f"{v.quantize(CENT)} {c}" for c, v in sorted(expenses[card.id].items())
                )
                if accounts
                else None,
                "mileage": mileage.get(card.id),
            }
        )
    utilization = (
        str(
            (
                Decimal(total_rental.total_seconds())
                / Decimal(total_accounting.total_seconds())
                * 100
            ).quantize(Decimal("0.1"))
        )
        if total_accounting.total_seconds() > 0
        else None
    )
    return [
        Block("fleet.utilization", "fleet", "percent", utilization, area=where),
        Block("fleet.idle_hours", "fleet", "hours", str(_hours(total_idle)), area=where),
        Block("fleet.future_hours", "fleet", "hours", str(_hours(future)), area=where),
        Block("fleet.by_vehicle", "fleet", "table", None, rows, where, "/vehicles"),
    ]


# --- Payback (§ 21.4.2) ------------------------------------------------------------------------


def _vehicle_of_charges(company: Company) -> dict[tuple[str, UUID], UUID]:
    from c4rsys.delivery.models import Delivery
    from c4rsys.rentals.models import Rental, RentalManualCharge
    from c4rsys.service.models import ServiceJob, ServiceMaterial

    found: dict[tuple[str, UUID], UUID] = {}
    for rid, vid in Rental.objects.filter(company=company).values_list("id", "vehicle_card_id"):
        found[("rental_base", rid)] = vid
        found[("rental_mileage", rid)] = vid
    for mid, vid in RentalManualCharge.objects.filter(rental__company=company).values_list(
        "id", "rental__vehicle_card_id"
    ):
        found[("rental_manual", mid)] = vid
    for jid, vid in ServiceJob.objects.filter(operation__company=company).values_list(
        "id", "operation__vehicle_card_id"
    ):
        found[("service_job", jid)] = vid
    for mid, vid in ServiceMaterial.objects.filter(job__operation__company=company).values_list(
        "id", "job__operation__vehicle_card_id"
    ):
        found[("service_material", mid)] = vid
    for did, vid in Delivery.objects.filter(company=company).values_list("id", "vehicle_card_id"):
        if vid is not None:
            found[("delivery", did)] = vid
    return found


def _payback(actor: Actor, company: Company, where: Area, filters: Filters) -> list[Block]:
    from c4rsys.finance.models import ChargeEntry, MoneyMovement, PaymentAllocation
    from c4rsys.fleet.models import VehicleCard

    accounts = _accounts(actor, company)
    query = VehicleCard.objects.filter(company=company, relation="own")
    if not where.whole:
        query = query.filter(accounting_location_id__in=where.location_ids)
    if filters.vehicle_id:
        query = query.filter(pk=filters.vehicle_id)
    cards = list(query.order_by("name"))
    # Money of the company is needed in full: with partial access to accounts it is hidden.
    from c4rsys.reports.overview import _all_accounts

    if accounts is None or len(accounts) != _all_accounts(company).count() or not cards:
        return []
    ids = {c.id for c in cards}
    invested: dict[UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    # Payouts linked to the vehicle; a reversal of a payout takes it back (§ 21.1.4).
    for m in (
        MoneyMovement.objects.filter(
            company=company, vehicle_id__in=ids, state=MoneyMovement.State.POSTED
        )
        .exclude(kind__in=("transfer", "opening"))
        .select_related("reverses")
    ):
        if m.direction == "out" and m.kind != "reversal":
            invested[m.vehicle_id][m.currency_id] += m.amount  # type: ignore[index]
        elif m.kind == "reversal" and m.reverses is not None and m.reverses.direction == "out":
            invested[m.vehicle_id][m.currency_id] -= m.amount  # type: ignore[index]
    owner = _vehicle_of_charges(company)
    by_order: dict[UUID, dict[UUID, dict[str, Decimal]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(Decimal))
    )
    for c in ChargeEntry.objects.filter(company=company):
        vid = owner.get((c.source_type, c.source_id))
        by_order[c.order_id][vid][c.currency_id] += c.amount  # type: ignore[index]
    received: dict[UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for a in PaymentAllocation.objects.filter(
        company=company, state=PaymentAllocation.State.ACTIVE, order_id__in=list(by_order)
    ):
        parts = by_order[a.order_id]
        total = sum((v.get(a.currency_id, Decimal(0)) for v in parts.values()), Decimal(0))
        if total <= 0:
            continue
        for vid, money in parts.items():
            if vid in ids and money.get(a.currency_id):
                received[vid][a.currency_id] += a.amount * money[a.currency_id] / total
    rows = []
    for card in cards:
        currency = card.acquisition_currency_id
        if card.acquisition_cost is None or currency is None:
            rows.append(
                {"vehicle": card.name, "vehicle_id": str(card.id), "state": "not_enough_data"}
            )
            continue
        spent = invested[card.id].get(currency, Decimal(0))
        got = received[card.id].get(currency, Decimal(0))
        unrecovered = spent - got
        rows.append(
            {
                "vehicle": card.name,
                "vehicle_id": str(card.id),
                "currency": currency,
                "acquisition": str(card.acquisition_cost.quantize(CENT)),
                "invested": str(spent.quantize(CENT)),
                "received": str(got.quantize(CENT)),
                "unrecovered": str(unrecovered.quantize(CENT)),
                "state": "paid_back" if spent > 0 and unrecovered <= 0 else "not_paid_back",
            }
        )
    return [Block("fleet.payback", "fleet", "table", None, rows, where, "/vehicles")]


def build(actor: Actor, company: Company, filters: Filters) -> list[Block]:
    now = timezone.now()
    tariff_id = filters.tariff_id
    blocks: list[Block] = []
    rental = area(actor, "rental.view", company, filters)
    if rental is not None:
        blocks += _rental_blocks(company, rental, filters, tariff_id, now)
    fleet = area(actor, "vehicle.view", company, filters)
    if fleet is not None:
        blocks += _fleet_blocks(actor, company, fleet, filters, now)
        blocks += _payback(actor, company, fleet, filters)
    return blocks
