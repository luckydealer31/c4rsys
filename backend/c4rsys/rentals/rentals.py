"""Reading rentals and their place in orders (ТЗ § 12.1.9, § 13.6.7)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Max, Q, QuerySet
from django.utils import timezone

from c4rsys.finance.charges import charge_ids_for_source
from c4rsys.inspections.models import Inspection
from c4rsys.orders.services import OperationSummary
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.money import round_money
from c4rsys.rentals import pricing
from c4rsys.rentals.models import Rental, RentalCalculation, RentalFreeInterval


def rentals_of(company_id: UUID) -> QuerySet[Rental]:
    return Rental.objects.filter(company_id=company_id).order_by("-actual_start")


def get_rental(company_id: UUID, rental_id: UUID) -> Rental:
    rental = rentals_of(company_id).filter(pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    return rental


@dataclass(frozen=True)
class DepositTerms:
    """The deposit of a rental as agreed (§ 13.1.6, § 14.7.1): from the rental's snapshot, so a
    later change of the tariff does not touch it. `hold_days` empty — no automatic transfer to
    the balance (customer's decision of 26.10.2026)."""

    required: bool
    amount: Decimal
    currency: str
    hold_days: int | None
    issued: bool  # the vehicle was handed over (the conflict of a missing deposit, § 14.3.3)
    returned_at: datetime | None  # the term counts from the actual return (§ 14.7.1)


def deposit_terms(rental: Rental) -> DepositTerms:
    """A deposit changed or cancelled before the issue (§ 14.7.3) replaces the tariff's; a
    booking without a tariff requires none yet."""
    snapshot = rental.tariff_snapshot or {}
    deposit = snapshot.get("vehicle_terms", {}).get("deposit") or {}
    if rental.deposit_override is not None:
        deposit = {**deposit, **rental.deposit_override}
    required = bool(deposit.get("required"))
    return DepositTerms(
        required=required,
        amount=Decimal(deposit["amount"]) if required and deposit.get("amount") else Decimal(0),
        # A deposit kept through a change of the tariff keeps its currency (decision П7).
        currency=deposit.get("currency") or snapshot.get("currency") or _company_currency(rental),
        hold_days=deposit.get("hold_days") if required else None,
        issued=rental.state in (Rental.State.ACTIVE, Rental.State.RETURNED),
        returned_at=rental.actual_end if rental.state == Rental.State.RETURNED else None,
    )


def _company_currency(rental: Rental) -> str:
    from c4rsys.companies.models import Company

    return Company.objects.get(pk=rental.company_id).main_currency_id or "USD"


def lock_rental(company_id: UUID, rental_id: UUID) -> Rental:
    """Serializes the deposit commands of one rental."""
    rental = Rental.objects.select_for_update().filter(company_id=company_id, pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    return rental


def returned_with_deposit_term(before: datetime) -> QuerySet[Rental]:
    """Returned rentals whose deposit has a hold term, returned before `before` (candidates of
    the automatic transfer, § 14.7.1)."""
    return (
        Rental.objects.filter(state=Rental.State.RETURNED, actual_end__lt=before)
        .filter(tariff_snapshot__vehicle_terms__deposit__required=True)
        .exclude(tariff_snapshot__vehicle_terms__deposit__hold_days=None)
    )


def terms_of(rental: Rental) -> pricing.TariffTerms:
    """The tariff snapshot with the time zone of the issue location kept in the rental, which
    settlement days and calendar months follow (§ 13.2.7)."""
    if rental.tariff_snapshot is None:
        raise RuntimeError("A rental without a tariff is not priced")
    return pricing.terms_from_snapshot(rental.tariff_snapshot, rental.calc_timezone)


def free_intervals_of(rental: Rental) -> list[pricing.Interval]:
    return [
        pricing.Interval(i.start, i.end)
        for i in RentalFreeInterval.objects.filter(rental_id=rental.id).order_by("start")
    ]


def calculate(rental: Rental, end: datetime) -> pricing.Calculation:
    """Base cost from the actual start to `end` with the free time of the rental (§ 13.3). Before
    the return the period is not over, so only the free time up to `end` counts."""
    if rental.actual_start is None:
        raise RuntimeError("Only an issued rental is priced by its actual start")
    end = max(end, rental.actual_start)
    intervals = free_intervals_of(rental)
    if rental.actual_end is None:
        intervals = pricing.clipped(intervals, rental.actual_start, end)
    return pricing.calculate(terms_of(rental), rental.actual_start, end, intervals)


def _calculation_payload(kind: str, end: datetime, result: pricing.Calculation) -> dict[str, Any]:
    return {
        "kind": kind,
        "end": end.astimezone(UTC).isoformat(),
        "unit": None,
        "billable_minutes": int(result.billable.total_seconds() // 60),
        "free_minutes": int(result.free.total_seconds() // 60),
        "units": result.units,
        "rate": str(result.rate) if result.rate is not None else None,
        "base_amount": str(result.base_amount),
        "currency": result.currency,
    }


def current_calculation(rental: Rental, now: datetime | None = None) -> dict[str, Any] | None:
    """While the rental is active the end is the current moment and the result is the current,
    not the actual, cost (§ 13.6.7)."""
    if rental.tariff_snapshot is None:
        return None
    unit = rental.tariff_snapshot.get("unit")
    if rental.actual_start is None:
        # A booking: the preliminary cost of the planned period (§ 13.6.7); not before the
        # tariff is chosen (§ 14.1.9).
        if rental.planned_start is None or rental.planned_end is None:
            return None
        result = pricing.calculate(terms_of(rental), rental.planned_start, rental.planned_end)
        return {**_calculation_payload("preliminary", rental.planned_end, result), "unit": unit}
    end = rental.actual_end or (now or timezone.now())
    result = calculate(rental, end)
    kind = "actual" if rental.actual_end else "current"
    return {**_calculation_payload(kind, end, result), "unit": unit}


def mileage(rental: Rental) -> dict[str, Any] | None:
    """Mileage of the whole rental against its included limit (§ 13.4.1–13.4.3): the limit is the
    included mileage per unit times the billable units after free time (§ 13.4.2); the overage is
    priced in the vehicle's odometer unit. None until both readings are known."""
    snapshot = rental.tariff_snapshot or {}
    terms = snapshot.get("vehicle_terms", {}).get("mileage") or {}
    if not terms or rental.actual_start is None:
        return None
    by_kind = {
        i.kind: i
        for i in Inspection.objects.filter(rental_id=rental.id).exclude(
            state=Inspection.State.INVALID
        )
    }
    issued, returned = by_kind.get("issue"), by_kind.get("return")
    if (
        issued is None
        or returned is None
        or issued.odometer_value is None
        or returned.odometer_value is None
        or rental.actual_end is None
    ):
        return {
            "mode": terms.get("mode"),
            "distance": None,
            "unit": snapshot["vehicle_terms"].get("mileage_unit"),
        }
    distance = returned.odometer_value - issued.odometer_value
    result: dict[str, Any] = {
        "mode": terms.get("mode"),
        "unit": snapshot["vehicle_terms"].get("mileage_unit"),
        "distance": distance,
        "included": None,
        "overage": Decimal(0),
        "amount": Decimal(0),
        "currency": snapshot.get("currency"),
    }
    if terms.get("mode") != "included":
        return result
    units = calculate(rental, rental.actual_end).units
    included = Decimal(terms["included_per_unit"]) * units
    overage = max(distance - included, Decimal(0))
    result.update(
        included=included,
        overage=overage,
        amount=round_money(
            overage * Decimal(terms["overage_price"]), int(snapshot["currency_minor_units"])
        ),
    )
    return result


def operations_for_order(company_id: UUID, order_id: UUID) -> list[OperationSummary]:
    """Rentals of an order as the order sees them. A rental is finished when it is returned and
    its formalization is completed, or when it is cancelled or annulled (§ 12.1.9, § 14.5.6)."""
    result = []
    for rental in Rental.objects.filter(company_id=company_id, order_id=order_id):
        finished = rental.state in (Rental.State.CANCELLED, Rental.State.ANNULLED) or (
            rental.state == Rental.State.RETURNED and rental.formalization_completed_at is not None
        )
        result.append(
            OperationSummary(
                type="rental",
                id=rental.id,
                state=rental.state,
                location_id=rental.issue_location_id,
                finished=finished,
            )
        )
    return result


def record_control_versions(
    ctx: CommandContext, company_id: UUID, order_id: UUID, reason: str
) -> None:
    """Writer for orders: one control version per rental of the order (§ 13.6.8)."""
    now = timezone.now()
    for rental in Rental.objects.select_for_update().filter(
        company_id=company_id, order_id=order_id
    ):
        if rental.state in (Rental.State.CANCELLED, Rental.State.ANNULLED, Rental.State.BOOKED):
            continue
        if rental.actual_start is None or rental.tariff_snapshot is None:
            continue
        end = rental.actual_end or max(now, rental.actual_start)
        result = calculate(rental, end)
        seq = (
            RentalCalculation.objects.filter(rental=rental).aggregate(m=Max("seq"))["m"] or 0
        ) + 1
        RentalCalculation.objects.create(
            company_id=company_id,
            rental=rental,
            seq=seq,
            reason=reason,
            kind="actual" if rental.actual_end else "current",
            inputs={
                "actual_start": rental.actual_start.isoformat(),
                "end": end.isoformat(),
                "tariff_id": rental.tariff_snapshot.get("tariff_id"),
                "tariff_version": rental.tariff_snapshot.get("tariff_version"),
                "free_intervals": [
                    {"start": i.start.isoformat(), "end": i.end.isoformat()}
                    for i in free_intervals_of(rental)
                ],
            },
            billable_minutes=int(result.billable.total_seconds() // 60),
            units=result.units,
            rate=result.rate,
            base_amount=result.base_amount,
            currency=result.currency,
            created_by_user_id=ctx.actor.user_id,
            created_at=now,
        )
        ctx.audit(
            "rental.control_version",
            object_type="rental",
            object_id=rental.id,
            after={"seq": seq, "reason": reason, "base_amount": result.base_amount},
        )


def history_objects(rental: Rental) -> list[tuple[str, str]]:
    """The rental, its inspections and its charges as journaled objects (§ 23.10)."""
    objects = [("rental", str(rental.id))]
    objects += [
        ("inspection", str(i))
        for i in Inspection.objects.filter(rental_id=rental.id).values_list("id", flat=True)
    ]
    objects += [("charge_entry", str(c)) for c in charge_ids_for_source("rental_base", rental.id)]
    return objects


def history_for_order(company_id: UUID, order_id: UUID) -> list[tuple[str, str]]:
    objects: list[tuple[str, str]] = []
    for rental in Rental.objects.filter(company_id=company_id, order_id=order_id):
        objects += history_objects(rental)
    return objects


def location_blockers(company_id: UUID, location_id: UUID, context_type: str | None) -> list[str]:
    """Rentals that block deactivating a location or switching off its rental context: booked or
    issued there or planned to return there, and returned there or from there without completed
    formalization (§ 4.4.1–4.4.2, decision 23.10.2026)."""
    if context_type not in (None, "rental"):
        return []
    unfinished = Rental.objects.filter(company_id=company_id).filter(
        Q(
            state__in=(Rental.State.BOOKED, Rental.State.ACTIVE),
            issue_location_id=location_id,
        )
        | Q(
            state__in=(Rental.State.BOOKED, Rental.State.ACTIVE),
            planned_return_location_id=location_id,
        )
        | Q(
            state=Rental.State.RETURNED,
            formalization_completed_at__isnull=True,
        )
        & (Q(issue_location_id=location_id) | Q(return_location_id=location_id))
    )
    return ["rentals_not_finished"] if unfinished.exists() else []


def vehicle_context_blockers(company_id: UUID, card_id: UUID, context_type: str) -> list[str]:
    """Rentals of the vehicle that keep its rental context on: booked, issued, or returned without
    completed formalization (decision of 24.10.2026)."""
    if context_type != "rental":
        return []
    unfinished = Rental.objects.filter(company_id=company_id, vehicle_card_id=card_id).filter(
        Q(state__in=(Rental.State.BOOKED, Rental.State.ACTIVE))
        | Q(state=Rental.State.RETURNED, formalization_completed_at__isnull=True)
    )
    return ["rentals_not_finished"] if unfinished.exists() else []


def started_after(company_id: UUID, card_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    """Rentals of the vehicle issued at or after the moment (decision Д-11)."""
    return [
        {"kind": "rental", "id": str(r.id), "state": r.state}
        for r in Rental.objects.filter(
            company_id=company_id,
            vehicle_card_id=card_id,
            state__in=(Rental.State.ACTIVE, Rental.State.RETURNED),
            actual_start__gte=moment,
        )
    ]


def vehicle_busy(company_id: UUID, card_id: UUID) -> list[str]:
    """A vehicle in an active rental is with the client and is not moved (§ 15.1.8)."""
    active = Rental.objects.filter(
        company_id=company_id, vehicle_card_id=card_id, state=Rental.State.ACTIVE
    ).exists()
    return ["in_rental"] if active else []
