"""Odometer of a vehicle: the sequence of readings, the replacement of the odometer and the change
of its unit (ТЗ § 8.2.9–8.2.10, § 8.2.12–8.2.13, § 13.4.3).

* Every reading keeps the moment of the measurement; the current one is the latest measured
  (§ 8.2.12). A reading that breaks the sequence of its neighbours is refused with them.
* «Odometer replacement» is the system service work (§ 8.2.13): the moment, the last reading of
  the old odometer and the first one of the new — not necessarily zero. The accumulated mileage
  is kept: after the replacement it grows by the difference between a later reading and the
  first one of the new odometer. The sequence is checked by the accumulated mileage, so a reading
  of the new odometer smaller than the old ones is not a conflict. Intervals of maintenance and
  the actual mileage of a rental count by the accumulated mileage; the dashboard reading is kept
  apart.
* A change of the unit is explicit (§ 8.2.10): the label only, or a conversion by 1.609344 of the
  whole history — readings, replacements and the values other modules registered (intervals and
  thresholds of maintenance, mileage limits and the price of a unit of mileage, the latter by the
  inverse factor). The original numbers and units, the method, the employee and the moment are
  kept. Inspections and signed documents keep their original form.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.fleet.models import (
    OdometerReading,
    OdometerReplacement,
    OdometerUnitChange,
    VehicleCard,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

FACTOR = Decimal("1.609344")
TENTH = Decimal("0.1")
REPLACEMENT_SOURCE = "odometer_replacement"
# The code of the system work in the reference of works of the service module (§ 8.2.13).
REPLACEMENT_WORK = "odometer_replacement"
# The id of a reading being checked before it is saved.
PROBE = UUID(int=0)


@dataclass(frozen=True)
class Point:
    """A point of the sequence: a reading, or the last reading of a replaced odometer."""

    at: datetime
    order: int  # at the same moment the old odometer comes before the new one
    accumulated: Decimal
    value: Decimal
    reading_id: UUID | None
    kind: str  # "reading" or "replaced"


def _replacements(card_id: UUID) -> list[OdometerReplacement]:
    return list(OdometerReplacement.objects.filter(vehicle_card_id=card_id).order_by("replaced_at"))


def _offset(replacements: list[OdometerReplacement], at: datetime, *, inclusive: bool) -> Decimal:
    """What the replacements up to the moment add to a dashboard reading."""
    return sum(
        (
            r.old_final - r.new_start
            for r in replacements
            if r.replaced_at < at or (inclusive and r.replaced_at == at)
        ),
        Decimal(0),
    )


def accumulated_value(card_id: UUID, value: Decimal, at: datetime) -> Decimal:
    return value + _offset(_replacements(card_id), at, inclusive=True)


def accumulated(reading: OdometerReading) -> Decimal:
    """The accumulated mileage of a reading (§ 8.2.13)."""
    return accumulated_value(reading.vehicle_card_id, reading.value, reading.measured_at)


def _readings(card_id: UUID) -> list[OdometerReading]:
    return list(
        OdometerReading.objects.filter(vehicle_card_id=card_id, replaced_by__isnull=True).order_by(
            "measured_at", "entered_at"
        )
    )


def points(
    card_id: UUID,
    *,
    readings: list[OdometerReading] | None = None,
    replacements: list[OdometerReplacement] | None = None,
) -> list[Point]:
    found = _readings(card_id) if readings is None else readings
    reps = _replacements(card_id) if replacements is None else replacements
    result = [
        Point(
            at=r.measured_at,
            order=1,
            accumulated=r.value + _offset(reps, r.measured_at, inclusive=True),
            value=r.value,
            reading_id=r.id,
            kind="reading",
        )
        for r in found
    ]
    result += [
        Point(
            at=r.replaced_at,
            order=0,
            accumulated=r.old_final + _offset(reps, r.replaced_at, inclusive=False),
            value=r.old_final,
            reading_id=None,
            kind="replaced",
        )
        for r in reps
    ]
    return sorted(result, key=lambda p: (p.at, p.order))


def conflicts_of(sequence: list[Point], new: set[UUID]) -> list[Point]:
    """Points that break the order with a point of `new` (by the accumulated mileage): an earlier
    one above it, a later one below it, one at the same moment with another value."""
    found: list[Point] = []
    for target in sequence:
        if target.reading_id not in new:
            continue
        for p in sequence:
            if p is target:
                continue
            same = p.at == target.at and p.order == target.order
            if (
                (same and p.accumulated != target.accumulated)
                or (
                    (p.at, p.order) < (target.at, target.order)
                    and p.accumulated > target.accumulated
                )
                or (
                    (p.at, p.order) > (target.at, target.order)
                    and p.accumulated < target.accumulated
                )
            ):
                found.append(p)
    return found


def broken(sequence: list[Point]) -> list[Point]:
    """Adjacent points out of order anywhere in the history."""
    result: list[Point] = []
    for a, b in zip(sequence, sequence[1:], strict=False):
        if b.accumulated < a.accumulated or (
            (a.at, a.order) == (b.at, b.order) and a.accumulated != b.accumulated
        ):
            result += [a, b]
    return result


def refuse(found: list[Point]) -> errors.ApiError:
    unique: dict[str, dict[str, str]] = {}
    for p in found:
        if p.reading_id == PROBE:
            continue
        key = str(p.reading_id) if p.reading_id else f"replaced:{p.at.isoformat()}"
        unique[key] = {
            "id": str(p.reading_id) if p.reading_id else "",
            "kind": p.kind,
            "measured_at": p.at.isoformat(),
            "value": str(p.value),
        }
    return errors.business_rule(
        "ODOMETER_SEQUENCE_CONFLICT", field="odometer", conflicts=list(unique.values())[:10]
    )


def check_new_reading(
    card: VehicleCard,
    measured_at: datetime,
    value: Decimal,
    *,
    exclude: UUID | None = None,
) -> None:
    """§ 8.2.12 with the replacements of § 8.2.13: the new reading against all the others."""
    readings = [r for r in _readings(card.id) if r.id != exclude]
    probe = OdometerReading(
        id=PROBE, vehicle_card_id=card.id, measured_at=measured_at, value=value, unit=""
    )
    sequence = points(card.id, readings=[*readings, probe])
    found = conflicts_of(sequence, {PROBE})
    if found:
        raise refuse(found)


def current(card_id: UUID) -> OdometerReading | None:
    return (
        OdometerReading.objects.filter(vehicle_card_id=card_id, replaced_by__isnull=True)
        .order_by("-measured_at", "-entered_at")
        .first()
    )


def current_accumulated(card_id: UUID) -> Decimal | None:
    reading = current(card_id)
    return accumulated(reading) if reading else None


def reading_of_source(card_id: UUID, source_type: str, source_id: UUID) -> OdometerReading | None:
    return OdometerReading.objects.filter(
        vehicle_card_id=card_id,
        source_type=source_type,
        source_id=source_id,
        replaced_by__isnull=True,
    ).first()


def accumulated_of_source(card_id: UUID, source_type: str, source_id: UUID) -> Decimal | None:
    reading = reading_of_source(card_id, source_type, source_id)
    return accumulated(reading) if reading else None


def accumulated_of_sources(
    card_id: UUID, source_type: str, source_ids: list[UUID]
) -> dict[UUID, Decimal]:
    reps = _replacements(card_id)
    return {
        r.source_id: r.value + _offset(reps, r.measured_at, inclusive=True)
        for r in OdometerReading.objects.filter(
            vehicle_card_id=card_id,
            source_type=source_type,
            source_id__in=source_ids,
            replaced_by__isnull=True,
        )
        if r.source_id is not None
    }


def replacements_payload(card_id: UUID) -> list[dict[str, Any]]:
    reps = _replacements(card_id)
    return [
        {
            "id": str(r.id),
            "replaced_at": r.replaced_at.isoformat(),
            "old_final": str(r.old_final),
            "new_start": str(r.new_start),
            "unit": r.unit,
            "accumulated": str(r.old_final + _offset(reps, r.replaced_at, inclusive=False)),
            "source_type": r.source_type,
            "source_id": str(r.source_id) if r.source_id else None,
        }
        for r in reps
    ]


# --- Odometer replacement (§ 8.2.13) -----------------------------------------------------------


def _check_replacement_values(old_final: Decimal, new_start: Decimal) -> None:
    if old_final < 0:
        raise errors.validation_error("old_final", code="ODOMETER_INVALID")
    if new_start < 0:
        raise errors.validation_error("new_start", code="ODOMETER_INVALID")


def preview_replacement(
    card: VehicleCard, replaced_at: datetime, old_final: Decimal, new_start: Decimal
) -> dict[str, Any]:
    """What the replacement gives before it is saved: the accumulated mileage at the moment and
    the readings it would conflict with."""
    _check_replacement_values(old_final, new_start)
    reps = _replacements(card.id)
    at_moment = old_final + _offset(reps, replaced_at, inclusive=False)
    sequence = _with_replacement(card, replaced_at, old_final, new_start)
    found = broken(sequence)
    return {
        "accumulated": str(at_moment),
        "unit": card.odometer_unit,
        "conflicts": refuse(found).params["conflicts"] if found else [],
    }


def _with_replacement(
    card: VehicleCard, replaced_at: datetime, old_final: Decimal, new_start: Decimal
) -> list[Point]:
    readings = _readings(card.id)
    probe_reading = OdometerReading(
        id=PROBE, vehicle_card_id=card.id, measured_at=replaced_at, value=new_start, unit=""
    )
    probe = OdometerReplacement(
        vehicle_card_id=card.id, replaced_at=replaced_at, old_final=old_final, new_start=new_start
    )
    reps = sorted([*_replacements(card.id), probe], key=lambda r: r.replaced_at)
    return points(card.id, readings=[*readings, probe_reading], replacements=reps)


def record_replacement(
    ctx: CommandContext,
    card: VehicleCard,
    *,
    replaced_at: datetime,
    old_final: Decimal,
    new_start: Decimal,
    source_type: str,
    source_id: UUID | None,
) -> OdometerReplacement:
    """Records the replacement inside the caller's command (the system service work). The card row
    is locked so that concurrent readings are checked against each other."""
    _check_replacement_values(old_final, new_start)
    VehicleCard.objects.select_for_update().filter(pk=card.pk).first()
    if OdometerReplacement.objects.filter(vehicle_card=card, replaced_at=replaced_at).exists():
        raise errors.business_rule("ODOMETER_REPLACEMENT_EXISTS", field="actual_end")
    found = broken(_with_replacement(card, replaced_at, old_final, new_start))
    if found:
        raise refuse(found)
    now = timezone.now()
    reading = OdometerReading.objects.create(
        company_id=card.company_id,
        vehicle_card=card,
        measured_at=replaced_at,
        entered_at=now,
        entered_by_user_id=ctx.actor.user_id,
        value=new_start,
        unit=card.odometer_unit,
        source_type=source_type,
        source_id=source_id,
    )
    replacement = OdometerReplacement.objects.create(
        company_id=card.company_id,
        vehicle_card=card,
        replaced_at=replaced_at,
        old_final=old_final,
        new_start=new_start,
        unit=card.odometer_unit,
        reading=reading,
        source_type=source_type,
        source_id=source_id,
        entered_by_user_id=ctx.actor.user_id,
        entered_at=now,
    )
    ctx.audit(
        "vehicle.odometer_replaced",
        object_type="vehicle_card",
        object_id=card.id,
        after={
            "replacement": str(replacement.id),
            "replaced_at": replaced_at,
            "old_final": old_final,
            "new_start": new_start,
            "unit": card.odometer_unit,
            "accumulated": accumulated(reading),
            "source_type": source_type,
            "source_id": str(source_id) if source_id else None,
        },
    )
    return replacement


# --- Change of the unit (§ 8.2.10) ---------------------------------------------------------------


@dataclass(frozen=True)
class Conversion:
    """How the values of one change of the unit are turned: `distance` for mileage, intervals and
    limits; `price` for the price of a unit of mileage (the inverse factor). Without a conversion
    (the label only) both keep the numbers."""

    method: str
    from_unit: str
    to_unit: str

    @property
    def converts(self) -> bool:
        return self.method == OdometerUnitChange.Method.CONVERT

    def distance(self, value: Decimal, places: Decimal = TENTH) -> Decimal:
        if not self.converts:
            return value
        result = value / FACTOR if self.to_unit == VehicleCard.OdometerUnit.MI else value * FACTOR
        return result.quantize(places, rounding=ROUND_HALF_UP)

    def price(self, value: Decimal, places: Decimal) -> Decimal:
        if not self.converts:
            return value
        result = value * FACTOR if self.to_unit == VehicleCard.OdometerUnit.MI else value / FACTOR
        return result.quantize(places, rounding=ROUND_HALF_UP)


# (company_id, card_id, conversion, apply) -> the values changed, as {"what", "id", "field",
# "before", "after"}; with apply=False nothing is saved (the preview). Registered by the modules
# that keep values in the odometer unit of the vehicle.
UnitParticipant = Callable[[UUID, UUID, Conversion, bool], list[dict[str, Any]]]
_participants: dict[str, UnitParticipant] = {}


def register_unit_participant(name: str, participant: UnitParticipant) -> None:
    _participants[name] = participant


def _own_values(card: VehicleCard, conv: Conversion, apply: bool) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    readings = OdometerReading.objects.filter(vehicle_card=card)
    replacements = OdometerReplacement.objects.filter(vehicle_card=card)
    if apply:
        # The preview reads only; the change locks what it rewrites.
        readings, replacements = readings.select_for_update(), replacements.select_for_update()
    for r in readings:
        after = conv.distance(r.value)
        changes.append(
            {
                "what": "reading",
                "id": str(r.id),
                "field": "value",
                "before": f"{r.value} {r.unit}",
                "after": f"{after} {conv.to_unit}",
            }
        )
        if apply:
            r.value, r.unit = after, conv.to_unit
            r.save(update_fields=["value", "unit"])
    for rep in replacements:
        old_final, new_start = conv.distance(rep.old_final), conv.distance(rep.new_start)
        changes.append(
            {
                "what": "replacement",
                "id": str(rep.id),
                "field": "old_final/new_start",
                "before": f"{rep.old_final}/{rep.new_start} {rep.unit}",
                "after": f"{old_final}/{new_start} {conv.to_unit}",
            }
        )
        if apply:
            rep.old_final, rep.new_start, rep.unit = old_final, new_start, conv.to_unit
            rep.save(update_fields=["old_final", "new_start", "unit"])
    return changes


def unit_changes(
    card: VehicleCard, to_unit: str, method: str, *, apply: bool
) -> dict[str, list[dict[str, Any]]]:
    conv = Conversion(method=method, from_unit=card.odometer_unit, to_unit=to_unit)
    result = {"vehicle": _own_values(card, conv, apply)}
    for name, participant in _participants.items():
        result[name] = participant(card.company_id, card.id, conv, apply)
    return result


def _check_unit(card: VehicleCard, to_unit: str, method: str) -> None:
    if to_unit not in VehicleCard.OdometerUnit.values:
        raise errors.validation_error("to_unit", code="VALUE_UNKNOWN")
    if method not in OdometerUnitChange.Method.values:
        raise errors.validation_error("method", code="CHOICE_REQUIRED")
    if to_unit == card.odometer_unit:
        raise errors.validation_error("to_unit", code="ODOMETER_UNIT_SAME")
    if card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_ARCHIVED")


def preview_unit_change(card: VehicleCard, to_unit: str, method: str) -> dict[str, Any]:
    _check_unit(card, to_unit, method)
    return {
        "from_unit": card.odometer_unit,
        "to_unit": to_unit,
        "method": method,
        "factor": str(FACTOR),
        "changes": unit_changes(card, to_unit, method, apply=False),
    }


def change_unit(
    ctx: CommandContext,
    company_id: UUID,
    card_id: UUID,
    *,
    to_unit: str,
    method: str,
    reason: str,
    expected_version: int,
) -> OdometerUnitChange:
    """The chosen method applies to the whole history of readings (§ 8.2.10); signed documents and
    saved inspections keep their form."""
    card = VehicleCard.objects.select_for_update().filter(company_id=company_id, pk=card_id).first()
    if card is None:
        raise errors.not_found()
    if card.version != expected_version:
        raise errors.version_conflict()
    _check_unit(card, to_unit, method)
    from_unit = card.odometer_unit
    details = unit_changes(card, to_unit, method, apply=True)
    card.odometer_unit = to_unit
    card.version += 1
    card.save(update_fields=["odometer_unit", "version", "updated_at"])
    change = OdometerUnitChange.objects.create(
        company_id=company_id,
        vehicle_card=card,
        method=method,
        from_unit=from_unit,
        to_unit=to_unit,
        reason=reason,
        details=details,
        changed_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "vehicle.odometer_unit_changed",
        object_type="vehicle_card",
        object_id=card.id,
        before={"odometer_unit": from_unit},
        after={
            "odometer_unit": to_unit,
            "method": method,
            "change": str(change.id),
            "converted": sum(len(v) for v in details.values()),
        },
        reason=reason,
    )
    return change


def unit_history(card_id: UUID) -> list[dict[str, Any]]:
    return [
        {
            "id": str(c.id),
            "method": c.method,
            "from_unit": c.from_unit,
            "to_unit": c.to_unit,
            "reason": c.reason,
            "changed_at": c.changed_at.isoformat(),
            "changed_by_user_id": str(c.changed_by_user_id),
            "count": sum(len(v) for v in c.details.values()),
        }
        for c in OdometerUnitChange.objects.filter(vehicle_card_id=card_id).order_by("-changed_at")
    ]
