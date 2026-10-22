"""Calculation of the base rental cost (ТЗ § 13.2.2, § 13.2.6, § 13.2.8, § 13.3, § 17.1.7).

Fixture of the examples: Asia/Bangkok, November 2026 (CONFIRMED 22.10.2026); a day is 24 hours
from issue; 1 000 THB for any positive number of days unless stated otherwise
(docs/DOMAIN_PRICING.md, E01–E06; base example of TECHNICAL_ARCHITECTURE.md).
"""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from c4rsys.platform.money import round_money
from c4rsys.rentals.pricing import (
    Interval,
    PriceRange,
    PricingError,
    TariffTerms,
    calculate,
    terms_from_snapshot,
)

BKK = ZoneInfo("Asia/Bangkok")
FLAT = TariffTerms("day", "from_issue", "THB", 2, (PriceRange(1, None, Decimal("1000")),))
STEPPED = TariffTerms(
    "day",
    "from_issue",
    "THB",
    2,
    (PriceRange(1, 2, Decimal("1000")), PriceRange(3, None, Decimal("600"))),
)


def nov(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=BKK)


def test_base_example_two_days_2000() -> None:
    result = calculate(FLAT, nov(1, 10), nov(3, 10))

    assert (result.units, result.base_amount) == (2, Decimal("2000.00"))


def test_e01_early_return_prices_actual_24_hours() -> None:
    # § 14.5.3, § 13.2.2: the planned end 3 Nov is irrelevant; the actual period is priced.
    result = calculate(FLAT, nov(1, 10), nov(2, 10))

    assert (result.units, result.base_amount) == (1, Decimal("1000.00"))


def test_e02_one_minute_over_24_hours_is_two_days() -> None:
    result = calculate(FLAT, nov(1, 10), nov(2, 10, 1))

    assert (result.units, result.base_amount) == (2, Decimal("2000.00"))


def test_e03_free_minute_is_subtracted_before_rounding() -> None:
    # § 13.3.2, § 13.3.7: occupation stays 24 h 1 min.
    result = calculate(FLAT, nov(1, 10), nov(2, 10, 1), [Interval(nov(2, 10), nov(2, 10, 1))])

    assert result.duration == timedelta(hours=24, minutes=1)
    assert result.billable == timedelta(hours=24)
    assert (result.units, result.base_amount) == (1, Decimal("1000.00"))


def test_e04_overlapping_free_intervals_count_once() -> None:
    result = calculate(
        FLAT,
        nov(1, 10),
        nov(2, 15),
        [Interval(nov(1, 12), nov(1, 15)), Interval(nov(1, 14), nov(1, 17))],
    )

    assert result.free == timedelta(hours=5)
    assert result.billable == timedelta(hours=24)
    assert result.base_amount == Decimal("1000.00")


def test_e05_all_free_gives_zero_and_no_range() -> None:
    # § 13.2.8, § 13.3.5.
    result = calculate(FLAT, nov(1, 10), nov(3, 10), [Interval(nov(1, 10), nov(3, 10))])

    assert result.duration == timedelta(hours=48)
    assert (result.units, result.range, result.base_amount) == (0, None, Decimal("0.00"))


def test_e06_free_day_moves_to_another_range_and_may_raise_price() -> None:
    # § 13.2.6, § 13.3.3–13.3.4: the selected range's rate applies to all units.
    before = calculate(STEPPED, nov(1, 10), nov(4, 10))
    after = calculate(STEPPED, nov(1, 10), nov(4, 10), [Interval(nov(3, 10), nov(4, 10))])

    assert (before.units, before.rate, before.base_amount) == (
        3,
        Decimal("600"),
        Decimal("1800.00"),
    )
    assert (after.units, after.rate, after.base_amount) == (2, Decimal("1000"), Decimal("2000.00"))


@pytest.mark.parametrize(("days", "rate"), [(1, "1000"), (2, "1000"), (3, "600"), (30, "600")])
def test_range_bounds_are_inclusive(days: int, rate: str) -> None:
    result = calculate(STEPPED, nov(1, 10), nov(1, 10) + timedelta(days=days))

    assert result.rate == Decimal(rate)


def test_zero_period_costs_nothing() -> None:
    result = calculate(FLAT, nov(1, 10), nov(1, 10))

    assert (result.units, result.base_amount) == (0, Decimal("0.00"))


def test_daylight_saving_does_not_change_24_hour_units() -> None:
    # Moments are compared in UTC (§ 4.2.2): a DST night still needs 24 real hours.
    berlin = ZoneInfo("Europe/Berlin")
    start = datetime(2026, 10, 24, 12, 0, tzinfo=berlin)
    same_wall_time_next_day = datetime(2026, 10, 25, 12, 0, tzinfo=berlin)  # 25 real hours

    assert calculate(FLAT, start, same_wall_time_next_day).units == 2


@pytest.mark.parametrize(
    ("amount", "places", "expected"),
    [("10.005", 2, "10.01"), ("10.004", 2, "10.00"), ("-10.005", 2, "-10.01"), ("2.5", 0, "3")],
)
def test_rounding_half_away_from_zero(amount: str, places: int, expected: str) -> None:
    # § 17.1.7.
    assert round_money(Decimal(amount), places) == Decimal(expected)


def test_total_is_rounded_to_currency_minor_units() -> None:
    terms = TariffTerms("day", "from_issue", "JPY", 0, (PriceRange(1, None, Decimal("333.3333")),))

    result = calculate(terms, nov(1, 10), nov(4, 10))

    assert result.base_amount == Decimal("1000")


@pytest.mark.parametrize(
    ("intervals", "code"),
    [
        ([Interval(nov(1, 9), nov(1, 11))], "FREE_INTERVAL_OUTSIDE_RENTAL"),
        ([Interval(nov(2, 9), nov(2, 11))], "FREE_INTERVAL_OUTSIDE_RENTAL"),
        ([Interval(nov(1, 12), nov(1, 12))], "FREE_INTERVAL_INVALID"),
    ],
)
def test_free_interval_must_lie_inside_rental(intervals: list[Interval], code: str) -> None:
    # § 13.3.2.
    with pytest.raises(PricingError) as error:
        calculate(FLAT, nov(1, 10), nov(2, 10), intervals)

    assert error.value.code == code


def test_invalid_inputs() -> None:
    with pytest.raises(PricingError, match="PERIOD_INVALID"):
        calculate(FLAT, nov(2, 10), nov(1, 10))
    with pytest.raises(PricingError, match="TIME_WITHOUT_ZONE"):
        calculate(FLAT, datetime(2026, 11, 1, 10), nov(2, 10))  # noqa: DTZ001
    hourly = TariffTerms("hour", None, "THB", 2, FLAT.ranges)
    with pytest.raises(PricingError, match="TARIFF_UNIT_NOT_SUPPORTED"):
        calculate(hourly, nov(1, 10), nov(2, 10))


def test_terms_from_snapshot() -> None:
    snapshot = {
        "unit": "day",
        "day_mode": "from_issue",
        "currency": "THB",
        "currency_minor_units": 2,
        "ranges": [
            {"from": 1, "to": 2, "rate": "1000.0000"},
            {"from": 3, "to": None, "rate": "600"},
        ],
    }

    assert terms_from_snapshot(snapshot) == STEPPED
