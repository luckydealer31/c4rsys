"""Tariff catalog: creation, change, archiving and availability (ТЗ § 13.1–13.2, § 23.4.4)."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies import access
from c4rsys.companies.models import Company, Location
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.platform.models import Currency
from c4rsys.rentals.models import Tariff, TariffLocation, TariffRange

RATE_PLACES = 4


@dataclass(frozen=True)
class PriceRange:
    from_units: int
    to_units: int | None
    rate: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {"from": self.from_units, "to": self.to_units, "rate": str(self.rate)}


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise errors.validation_error(field, code="RANGE_BOUND_INVALID")
    return value


def _rate(value: Any, field: str) -> Decimal:
    """A decimal string or an integer; JSON floats are refused to keep exact money (A05)."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise errors.validation_error(field, code="RATE_INVALID")
    try:
        rate = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise errors.validation_error(field, code="RATE_INVALID") from exc
    if not rate.is_finite() or rate <= 0:
        # Free time is given separately (§ 13.3), not by a zero rate (decision 22.10.2026).
        raise errors.validation_error(field, code="RATE_INVALID")
    exponent = rate.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > RATE_PLACES:
        raise errors.validation_error(field, code="RATE_TOO_PRECISE", max_places=RATE_PLACES)
    return rate


def parse_ranges(value: Any) -> list[PriceRange]:
    """Structure of § 13.2.8: whole positive units, inclusive bounds, the first range starts at 1,
    each next one right after the previous upper bound, no gaps or overlaps, the last one open."""
    if not isinstance(value, list) or not value:
        raise errors.validation_error("ranges", code="RANGES_REQUIRED")
    ranges: list[PriceRange] = []
    for index, item in enumerate(value):
        where = f"ranges[{index}]"
        if not isinstance(item, dict):
            raise errors.validation_error(where)
        upper = item.get("to")
        ranges.append(
            PriceRange(
                from_units=_positive_int(item.get("from"), f"{where}.from"),
                to_units=None if upper is None else _positive_int(upper, f"{where}.to"),
                rate=_rate(item.get("rate"), f"{where}.rate"),
            )
        )
    expected_from = 1
    for index, item in enumerate(ranges):
        last = index == len(ranges) - 1
        if item.from_units != expected_from:
            raise errors.validation_error("ranges", code="RANGES_NOT_CONTIGUOUS", index=index)
        if last and item.to_units is not None:
            raise errors.validation_error("ranges", code="RANGES_LAST_NOT_OPEN")
        if not last:
            if item.to_units is None:
                raise errors.validation_error("ranges", code="RANGES_ONLY_LAST_OPEN", index=index)
            if item.to_units < item.from_units:
                raise errors.validation_error("ranges", code="RANGE_BOUND_INVALID", index=index)
            expected_from = item.to_units + 1
    return ranges


# --- Availability and readiness (§ 4.3.1, § 13.1.3) ------------------------------------------


def available_at(company_id: UUID, location_id: UUID) -> QuerySet[Tariff]:
    """Active tariffs available in the location: for the whole company or listing it."""
    return Tariff.objects.filter(
        Q(all_locations=True) | Q(locations=location_id),
        company_id=company_id,
        state=Tariff.State.ACTIVE,
    ).distinct()


def rental_conditions_met(company_id: UUID, location_id: UUID) -> bool:
    """Module conditions of a rental context: a current vehicle rental tariff is available in the
    location (§ 4.3.1). Standalone additional services come later."""
    return available_at(company_id, location_id).exists()


# --- Permissions within the tariff's availability (§ 23.1.1, § 23.4.4) ----------------------


@dataclass(frozen=True)
class Availability:
    all_locations: bool
    location_ids: frozenset[UUID]


def availability_of(tariff: Tariff) -> Availability:
    return Availability(
        all_locations=tariff.all_locations,
        location_ids=frozenset(
            TariffLocation.objects.filter(tariff=tariff).values_list("location_id", flat=True)
        ),
    )


def covers(actor: Actor, permission: str, availability: Availability) -> bool:
    """The permission covers every location where the tariff is available."""
    resolver = access.RightsResolver()
    if availability.all_locations:
        return resolver.has_permission(actor, permission, whole_company=True)
    return all(
        resolver.has_permission(actor, permission, location_id=x) for x in availability.location_ids
    )


def visible(actor: Actor, availability: Availability) -> bool:
    """A tariff is visible where the employee may view tariffs: a company-wide tariff applies in
    every location, so any tariff.view opens it; a limited one needs one of its locations."""
    resolver = access.RightsResolver()
    if availability.all_locations:
        return resolver.has_permission(actor, "tariff.view")
    return any(
        resolver.has_permission(actor, "tariff.view", location_id=x)
        for x in availability.location_ids
    )


# --- Commands ----------------------------------------------------------------------------------


def snapshot(tariff: Tariff) -> dict[str, Any]:
    return {
        "name": tariff.name,
        "currency": tariff.currency_id,
        "unit": tariff.unit,
        "day_mode": tariff.day_mode or None,
        "all_locations": tariff.all_locations,
        "locations": sorted(
            str(x)
            for x in TariffLocation.objects.filter(tariff=tariff).values_list(
                "location_id", flat=True
            )
        ),
        "ranges": [
            PriceRange(r.from_units, r.to_units, r.rate).as_dict()
            for r in tariff.ranges.order_by("from_units")
        ],
        "state": tariff.state,
    }


def validate_locations(company_id: UUID, location_ids: frozenset[UUID]) -> None:
    found = Location.objects.filter(company_id=company_id, pk__in=location_ids).count()
    if found != len(location_ids):
        raise errors.validation_error("locations", code="LOCATION_UNKNOWN")


def _write_scope_and_ranges(
    tariff: Tariff, location_ids: frozenset[UUID] | None, ranges: list[PriceRange] | None
) -> None:
    if location_ids is not None:
        TariffLocation.objects.filter(tariff=tariff).delete()
        TariffLocation.objects.bulk_create(
            TariffLocation(company_id=tariff.company_id, tariff=tariff, location_id=x)
            for x in sorted(location_ids)
        )
    if ranges is not None:
        TariffRange.objects.filter(tariff=tariff).delete()
        TariffRange.objects.bulk_create(
            TariffRange(
                company_id=tariff.company_id,
                tariff=tariff,
                from_units=r.from_units,
                to_units=r.to_units,
                rate=r.rate,
            )
            for r in ranges
        )


@dataclass(frozen=True)
class NewTariff:
    name: str
    currency: str  # empty: the company's main currency is proposed (§ 22.1.7)
    ranges: list[PriceRange]
    location_ids: frozenset[UUID] | None  # None: the whole company (§ 13.1.3)


def create_tariff(ctx: CommandContext, company: Company, data: NewTariff) -> Tariff:
    """A daily tariff «24 hours from issue» (§ 13.2.2); other units and the fixed day boundary come
    at stage 2."""
    code = data.currency or (company.main_currency_id or "")
    if not code:
        raise errors.validation_error("currency", code="FIELD_REQUIRED")
    currency = Currency.objects.filter(code=code.upper()).first()
    if currency is None:
        raise errors.validation_error("currency", code="CURRENCY_INVALID")
    tariff = Tariff.objects.create(
        company=company,
        name=data.name,
        currency=currency,
        unit=Tariff.Unit.DAY,
        day_mode=Tariff.DayMode.FROM_ISSUE,
        all_locations=data.location_ids is None,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    _write_scope_and_ranges(tariff, data.location_ids or frozenset(), data.ranges)
    ctx.audit("tariff.created", object_type="tariff", object_id=tariff.id, after=snapshot(tariff))
    return tariff


def locked_tariff(company_id: UUID, tariff_id: UUID, expected_version: int) -> Tariff:
    tariff = Tariff.objects.select_for_update().filter(company_id=company_id, pk=tariff_id).first()
    if tariff is None:
        raise errors.not_found()
    if tariff.version != expected_version:
        raise errors.version_conflict()
    return tariff


@dataclass(frozen=True)
class TariffChanges:
    """Name, ranges and availability may change; currency, unit and day mode may not
    (decision 22.10.2026). Existing rentals keep their snapshot (§ 13.6.1)."""

    name: str | None = None
    ranges: list[PriceRange] | None = None
    location_ids: frozenset[UUID] | None = None
    all_locations: bool | None = None


def change_tariff(ctx: CommandContext, tariff: Tariff, changes: TariffChanges) -> Tariff:
    before = snapshot(tariff)
    if changes.name is not None:
        tariff.name = changes.name
    location_ids = None
    if changes.all_locations is not None:
        tariff.all_locations = changes.all_locations
        location_ids = changes.location_ids or frozenset()
    tariff.version += 1
    tariff.save(update_fields=["name", "all_locations", "version", "updated_at"])
    _write_scope_and_ranges(tariff, location_ids, changes.ranges)
    after = snapshot(tariff)
    ctx.audit(
        "tariff.changed",
        object_type="tariff",
        object_id=tariff.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return tariff


def set_archived(ctx: CommandContext, tariff: Tariff, *, archived: bool) -> Tariff:
    """An archived tariff is not offered for new rentals; it can be restored (decision
    22.10.2026)."""
    target = Tariff.State.ARCHIVED if archived else Tariff.State.ACTIVE
    if tariff.state == target:
        raise errors.business_rule("TARIFF_STATE_UNCHANGED", state=tariff.state)
    before = tariff.state
    tariff.state = target
    tariff.version += 1
    tariff.save(update_fields=["state", "version", "updated_at"])
    ctx.audit(
        "tariff.archived" if archived else "tariff.restored",
        object_type="tariff",
        object_id=tariff.id,
        before={"state": before},
        after={"state": target},
    )
    return tariff
