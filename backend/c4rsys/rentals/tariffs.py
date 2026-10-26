"""Tariff catalog: creation, change, archiving and availability (ТЗ § 13.1–13.2, § 23.4.4)."""

from dataclasses import dataclass
from datetime import time
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies import access
from c4rsys.companies.models import Company, Location
from c4rsys.fleet.models import VehicleCard
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.platform.models import Currency
from c4rsys.rentals.models import Tariff, TariffLocation, TariffRange, TariffVehicleTerms
from c4rsys.rentals.pricing import PriceRange

RATE_PLACES = 4


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
        "day_boundary": tariff.day_boundary_time.isoformat(timespec="minutes")
        if tariff.day_boundary_time
        else None,
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
    # § 13.1.4: minute, hour, day or month; a day is «24 hours from issue» or settlement days
    # with a fixed daily boundary (§ 13.2.2).
    unit: str = Tariff.Unit.DAY
    day_mode: str = Tariff.DayMode.FROM_ISSUE
    day_boundary: time | None = None


def parse_unit(data: dict[str, Any]) -> tuple[str, str, time | None]:
    unit = str(data.get("unit") or Tariff.Unit.DAY)
    if unit not in Tariff.Unit.values:
        raise errors.validation_error("unit", code="CHOICE_REQUIRED")
    if unit != Tariff.Unit.DAY:
        return unit, "", None
    mode = str(data.get("day_mode") or Tariff.DayMode.FROM_ISSUE)
    if mode not in Tariff.DayMode.values:
        raise errors.validation_error("day_mode", code="CHOICE_REQUIRED")
    if mode == Tariff.DayMode.FROM_ISSUE:
        return unit, mode, None
    raw = data.get("day_boundary")
    try:
        boundary = time.fromisoformat(str(raw)).replace(second=0, microsecond=0)
    except ValueError as exc:
        raise errors.validation_error("day_boundary", code="TIME_INVALID") from exc
    return unit, mode, boundary


def create_tariff(ctx: CommandContext, company: Company, data: NewTariff) -> Tariff:
    """A tariff of any unit of § 13.1.4; unit, day mode and currency do not change later
    (decision 22.10.2026)."""
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
        unit=data.unit,
        day_mode=data.day_mode,
        day_boundary_time=data.day_boundary,
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


# --- Terms of a tariff for a vehicle (§ 13.1.6, § 13.4.1, § 22.1.8) -------------------------


@dataclass(frozen=True)
class VehicleTerms:
    mileage_mode: str
    included_per_unit: Decimal | None
    overage_price: Decimal | None
    deposit_required: bool
    deposit_amount: Decimal | None
    deposit_hold_days: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mileage": {
                "mode": self.mileage_mode,
                "included_per_unit": _text(self.included_per_unit),
                "overage_price": _text(self.overage_price),
            },
            "deposit": {
                "required": self.deposit_required,
                "amount": _text(self.deposit_amount),
                "hold_days": self.deposit_hold_days,
            },
        }


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _decimal(value: Any, field: str, places: int) -> Decimal:
    """A positive decimal string or integer (no JSON floats)."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise errors.validation_error(field, code="AMOUNT_INVALID")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise errors.validation_error(field, code="AMOUNT_INVALID") from exc
    if not number.is_finite() or number <= 0:
        raise errors.validation_error(field, code="AMOUNT_INVALID")
    exponent = number.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > places:
        raise errors.validation_error(field, code="AMOUNT_TOO_PRECISE", max_places=places)
    return number


def parse_vehicle_terms(data: dict[str, Any]) -> VehicleTerms:
    """Mileage «unlimited» or «included» with the included distance per tariff unit and the price
    of each unit over; deposit «not required» or «required» with a fixed amount and an optional
    hold term in days (decision 22.10.2026). Both choices are explicit."""
    mileage = data.get("mileage")
    deposit = data.get("deposit")
    if not isinstance(mileage, dict) or mileage.get("mode") not in (
        TariffVehicleTerms.MileageMode.values
    ):
        raise errors.validation_error("mileage.mode", code="CHOICE_REQUIRED")
    if not isinstance(deposit, dict) or not isinstance(deposit.get("required"), bool):
        raise errors.validation_error("deposit.required", code="CHOICE_REQUIRED")
    included = mileage["mode"] == TariffVehicleTerms.MileageMode.INCLUDED
    required = bool(deposit["required"])
    hold = deposit.get("hold_days")
    if hold is not None and (isinstance(hold, bool) or not isinstance(hold, int) or hold < 1):
        raise errors.validation_error("deposit.hold_days", code="RANGE_BOUND_INVALID")
    if not required and (deposit.get("amount") is not None or hold is not None):
        raise errors.validation_error("deposit.amount", code="DEPOSIT_NOT_REQUIRED")
    if not included and (
        mileage.get("included_per_unit") is not None or mileage.get("overage_price") is not None
    ):
        raise errors.validation_error("mileage.included_per_unit", code="MILEAGE_UNLIMITED")
    return VehicleTerms(
        mileage_mode=str(mileage["mode"]),
        included_per_unit=_decimal(mileage.get("included_per_unit"), "mileage.included_per_unit", 1)
        if included
        else None,
        overage_price=_decimal(mileage.get("overage_price"), "mileage.overage_price", RATE_PLACES)
        if included
        else None,
        deposit_required=required,
        deposit_amount=_decimal(deposit.get("amount"), "deposit.amount", RATE_PLACES)
        if required
        else None,
        deposit_hold_days=hold if required else None,
    )


def terms_of(record: TariffVehicleTerms) -> VehicleTerms:
    return VehicleTerms(
        mileage_mode=record.mileage_mode,
        included_per_unit=record.included_per_unit,
        overage_price=record.overage_price,
        deposit_required=record.deposit_required,
        deposit_amount=record.deposit_amount,
        deposit_hold_days=record.deposit_hold_days,
    )


def terms_payload(record: TariffVehicleTerms) -> dict[str, Any]:
    return {
        "vehicle_card_id": str(record.vehicle_card_id),
        "mileage_unit": record.mileage_unit,
        "currency": record.tariff.currency_id,
        "version": record.version,
        **terms_of(record).as_dict(),
    }


def set_vehicle_terms(
    ctx: CommandContext, tariff: Tariff, card: VehicleCard, terms: VehicleTerms
) -> TariffVehicleTerms:
    """Assigns the tariff to the vehicle or replaces its terms. New terms apply to new rentals
    only; created rentals keep their snapshot (§ 13.1.6)."""
    record = (
        TariffVehicleTerms.objects.select_for_update()
        .filter(tariff=tariff, vehicle_card=card)
        .first()
    )
    before = None if record is None else terms_of(record).as_dict()
    if record is None:
        record = TariffVehicleTerms(
            company_id=tariff.company_id,
            tariff=tariff,
            vehicle_card=card,
            created_at=timezone.now(),
        )
    else:
        record.version += 1
    record.mileage_mode = terms.mileage_mode
    record.included_per_unit = terms.included_per_unit
    record.overage_price = terms.overage_price
    record.mileage_unit = card.odometer_unit
    record.deposit_required = terms.deposit_required
    record.deposit_amount = terms.deposit_amount
    record.deposit_hold_days = terms.deposit_hold_days
    record.save()
    ctx.audit(
        "tariff.vehicle_terms_set",
        object_type="tariff",
        object_id=tariff.id,
        before=before,
        after={"vehicle_card": str(card.id), **terms.as_dict()},
    )
    return record


def tariffs_for_vehicle(card: VehicleCard) -> QuerySet[Tariff]:
    """Tariffs to choose from when issuing the vehicle (§ 13.1.2): assigned to it with terms,
    active and available in its accounting location. An archived tariff drops out without
    removing its terms (decision 22.10.2026); restoring it brings it back."""
    if card.accounting_location_id is None:
        return Tariff.objects.none()
    return available_at(card.company_id, card.accounting_location_id).filter(
        vehicle_terms__vehicle_card=card
    )


def rental_snapshot(tariff: Tariff, terms: TariffVehicleTerms) -> dict[str, Any]:
    """The tariff and its terms for the vehicle as agreed at issue (§ 13.6.1). The rental is
    calculated from this snapshot; later catalog changes do not touch it."""
    return {
        "tariff_id": str(tariff.id),
        "tariff_version": tariff.version,
        **snapshot(tariff),
        "currency_minor_units": tariff.currency.minor_units,
        "vehicle_terms": {**terms_of(terms).as_dict(), "mileage_unit": terms.mileage_unit},
    }


def vehicle_terms(tariff: Tariff, card: VehicleCard) -> TariffVehicleTerms | None:
    return TariffVehicleTerms.objects.filter(tariff=tariff, vehicle_card=card).first()
