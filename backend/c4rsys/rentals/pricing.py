"""Base cost of a rental by a tariff snapshot. A pure function: no database, no clock.

Order of calculation (ТЗ § 13.3.7, § 13.2, docs/stage1/STAGE1_COMMANDS_API.md):

1. duration = end − actual_start;
2. the union of free intervals inside the period is subtracted from the end of the period
   (overlaps count once, § 13.3.2, § 13.3.6–13.3.7): the calculation end = start + billable;
3. billable units are determined from the start to the calculation end and rounded up:
   minutes and hours (§ 13.2.1); days «24 hours from issue» — whole 24-hour units, exactly
   24 hours is one day (§ 13.2.2); days with a fixed daily boundary — the settlement days
   touched by the interval, an end exactly on the boundary adds nothing (§ 13.2.2, § 13.3.7);
   calendar months from the local start, a shortened boundary does not move the next ones,
   a part of a month is a whole month (§ 13.2.3–13.2.4, § 13.2.7);
4. zero units give a zero base cost and no range is selected (§ 13.2.8, § 13.3.5);
5. the range is selected by the number of units, its rate applies to all units (§ 13.2.6, § 13.3.3);
6. the result is rounded to the minor units of the currency, half away from zero (§ 17.1.7).

Settlement days and months use the time zone of the issue location kept in the rental
(§ 13.2.7). Occupation of the vehicle is not changed by free time (§ 13.3.1); this module only
prices.
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from c4rsys.platform.money import round_money

DAY = timedelta(hours=24)
FIXED_UNITS = {"minute": timedelta(minutes=1), "hour": timedelta(hours=1)}


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
    day_boundary: time | None = None  # local time of the daily boundary (fixed_boundary)
    zone: str = "UTC"  # time zone of the issue location (§ 13.2.7)


def terms_from_snapshot(snapshot: dict[str, Any], zone: str = "UTC") -> TariffTerms:
    """Builds terms from a stored snapshot: tariff fields plus `currency_minor_units`."""
    boundary = snapshot.get("day_boundary")
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
        day_boundary=time.fromisoformat(boundary) if boundary else None,
        zone=zone or "UTC",
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


def clipped(intervals: Sequence[Interval], start: datetime, end: datetime) -> list[Interval]:
    """The parts of free intervals inside [start, end): for the current cost of an active rental,
    whose period has not ended yet (§ 13.6.7)."""
    start, end = _utc(start), _utc(end)
    result = []
    for item in intervals:
        lo, hi = max(_utc(item.start), start), min(_utc(item.end), end)
        if hi > lo:
            result.append(Interval(lo, hi))
    return result


def _add_months(local_start: datetime, months: int, zone: ZoneInfo) -> datetime:
    """The same local date and time `months` later; a missing day is the last day of that month.
    Always counted from the original start, so a shortened boundary does not move the next ones
    (§ 13.2.7)."""
    index = local_start.month - 1 + months
    year, month = local_start.year + index // 12, index % 12 + 1
    day = min(local_start.day, calendar.monthrange(year, month)[1])
    naive = local_start.replace(tzinfo=None, year=year, month=month, day=day)
    return naive.replace(tzinfo=zone).astimezone(UTC)


def _boundary_on(day: datetime, at: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(day.date(), at, tzinfo=zone).astimezone(UTC)


def _first_boundary(start: datetime, at: time, zone: ZoneInfo) -> datetime:
    """The last daily boundary at or before `start`: the beginning of its settlement day."""
    local = start.astimezone(zone)
    boundary = _boundary_on(local, at, zone)
    if boundary > start:
        boundary = _boundary_on(local - timedelta(days=1), at, zone)
    return boundary


def _next_boundary(boundary: datetime, at: time, zone: ZoneInfo) -> datetime:
    return _boundary_on(boundary.astimezone(zone) + timedelta(days=1), at, zone)


def _units(terms: TariffTerms, start: datetime, calc_end: datetime) -> int:
    billable = calc_end - start
    if billable <= timedelta(0):
        return 0
    if terms.unit in FIXED_UNITS:
        whole, rest = divmod(billable, FIXED_UNITS[terms.unit])
        return int(whole) + (1 if rest else 0)
    zone = ZoneInfo(terms.zone)
    if terms.unit == "day" and terms.day_mode == "from_issue":
        whole, rest = divmod(billable, DAY)
        return int(whole) + (1 if rest else 0)
    if terms.unit == "day" and terms.day_mode == "fixed_boundary" and terms.day_boundary:
        boundary, units = _first_boundary(start, terms.day_boundary, zone), 0
        while boundary < calc_end:
            boundary, units = _next_boundary(boundary, terms.day_boundary, zone), units + 1
        return units
    if terms.unit == "month":
        local_start, months = start.astimezone(zone), 1
        while _add_months(local_start, months, zone) < calc_end:
            months += 1
        return months
    raise PricingError("TARIFF_UNIT_NOT_SUPPORTED")


def advance(terms: TariffTerms, start: datetime, units: int) -> datetime:
    """The end of `units` tariff units from `start`, for a free interval given by its length
    (§ 13.3.2): minutes, hours and 24-hour days by duration; settlement days up to the `units`-th
    daily boundary after the start; calendar months by § 13.2.7."""
    if units < 1:
        raise PricingError("FREE_UNITS_INVALID")
    start = _utc(start)
    if terms.unit in FIXED_UNITS:
        return start + FIXED_UNITS[terms.unit] * units
    zone = ZoneInfo(terms.zone)
    if terms.unit == "day" and terms.day_mode == "from_issue":
        return start + DAY * units
    if terms.unit == "day" and terms.day_mode == "fixed_boundary" and terms.day_boundary:
        boundary = _first_boundary(start, terms.day_boundary, zone)
        for _ in range(units):
            boundary = _next_boundary(boundary, terms.day_boundary, zone)
        return boundary
    if terms.unit == "month":
        return _add_months(start.astimezone(zone), units, zone)
    raise PricingError("TARIFF_UNIT_NOT_SUPPORTED")


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
    units = _units(terms, actual_start, actual_start + billable)
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
