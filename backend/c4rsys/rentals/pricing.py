"""Base cost of a rental by a tariff snapshot. A pure function: no database, no clock.

Order of calculation (ТЗ § 13.3.7, § 13.2.2, docs/stage1/STAGE1_COMMANDS_API.md):

1. duration = end − actual_start;
2. the union of free intervals inside the period is subtracted (overlaps count once, § 13.3.2);
3. billable units are determined and rounded up (a day «24 hours from issue»: whole 24-hour
   units, exactly 24 hours is one day, § 13.2.2);
4. zero units give a zero base cost and no range is selected (§ 13.2.8, § 13.3.5);
5. the range is selected by the number of units, its rate applies to all units (§ 13.2.6, § 13.3.3);
6. the result is rounded to the minor units of the currency, half away from zero (§ 17.1.7).

Occupation of the vehicle is not changed by free time (§ 13.3.1); this module only prices.
Minute, hour and month units and the fixed day boundary come at stage 2.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

DAY = timedelta(hours=24)


class PricingError(ValueError):
    """Inputs that cannot be priced; `code` is the API error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PriceRange:
    from_units: int
    to_units: int | None  # None: no upper bound
    rate: Decimal

    def contains(self, units: int) -> bool:
        return units >= self.from_units and (self.to_units is None or units <= self.to_units)

    def as_dict(self) -> dict[str, Any]:
        """Snapshot form, read back by terms_from_snapshot."""
        return {"from": self.from_units, "to": self.to_units, "rate": str(self.rate)}


@dataclass(frozen=True)
class TariffTerms:
    """The part of a tariff snapshot the calculation needs."""

    unit: str
    day_mode: str | None
    currency: str
    minor_units: int
    ranges: tuple[PriceRange, ...]


def terms_from_snapshot(snapshot: dict[str, Any]) -> TariffTerms:
    """Builds terms from a stored snapshot: tariff fields plus `currency_minor_units`."""
    return TariffTerms(
        unit=snapshot["unit"],
        day_mode=snapshot.get("day_mode"),
        currency=snapshot["currency"],
        minor_units=int(snapshot["currency_minor_units"]),
        ranges=tuple(
            PriceRange(
                from_units=int(r["from"]),
                to_units=None if r["to"] is None else int(r["to"]),
                rate=Decimal(str(r["rate"])),
            )
            for r in snapshot["ranges"]
        ),
    )


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Calculation:
    duration: timedelta  # whole rental period; occupation is not reduced by free time
    free: timedelta  # union of free intervals
    billable: timedelta
    units: int
    range: PriceRange | None
    rate: Decimal | None
    base_amount: Decimal
    currency: str


def _utc(value: datetime) -> datetime:
    """Moments are compared in UTC (§ 4.2.2). Python subtracts two datetimes of the same zone by
    wall clock, which would lose or add an hour across a daylight saving change."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise PricingError("TIME_WITHOUT_ZONE")
    return value.astimezone(UTC)


def free_union(intervals: Sequence[Interval], start: datetime, end: datetime) -> timedelta:
    """Total length of the union of free intervals. Each must lie fully inside the rental period
    (§ 13.3.2) and be non-empty."""
    start, end = _utc(start), _utc(end)
    intervals = [Interval(_utc(x.start), _utc(x.end)) for x in intervals]
    for item in intervals:
        if item.end <= item.start:
            raise PricingError("FREE_INTERVAL_INVALID")
        if item.start < start or item.end > end:
            raise PricingError("FREE_INTERVAL_OUTSIDE_RENTAL")
    total = timedelta(0)
    current: Interval | None = None
    for item in sorted(intervals, key=lambda x: x.start):
        if current is None or item.start > current.end:
            if current is not None:
                total += current.end - current.start
            current = item
        elif item.end > current.end:
            current = Interval(current.start, item.end)
    if current is not None:
        total += current.end - current.start
    return total


def _units(terms: TariffTerms, billable: timedelta) -> int:
    if terms.unit == "day" and terms.day_mode == "from_issue":
        whole, rest = divmod(billable, DAY)
        return int(whole) + (1 if rest else 0)
    raise PricingError("TARIFF_UNIT_NOT_SUPPORTED")


def round_money(amount: Decimal, minor_units: int) -> Decimal:
    """Nearest value; a half goes away from zero (§ 17.1.7)."""
    return amount.quantize(Decimal(1).scaleb(-minor_units), rounding=ROUND_HALF_UP)


def calculate(
    terms: TariffTerms,
    actual_start: datetime,
    end: datetime,
    free_intervals: Sequence[Interval] = (),
) -> Calculation:
    """Base cost for the period from `actual_start` to `end`. While the rental is active, `end` is
    the current moment and the result is the current, not the actual, cost (§ 13.6.7)."""
    actual_start, end = _utc(actual_start), _utc(end)
    if end < actual_start:
        raise PricingError("PERIOD_INVALID")
    duration = end - actual_start
    free = free_union(free_intervals, actual_start, end)
    billable = duration - free
    units = _units(terms, billable)
    if units == 0:
        return Calculation(
            duration,
            free,
            billable,
            0,
            None,
            None,
            round_money(Decimal(0), terms.minor_units),
            terms.currency,
        )
    selected = next((r for r in terms.ranges if r.contains(units)), None)
    if selected is None:
        raise PricingError("TARIFF_RANGES_INVALID")
    amount = round_money(selected.rate * units, terms.minor_units)
    return Calculation(
        duration, free, billable, units, selected, selected.rate, amount, terms.currency
    )
