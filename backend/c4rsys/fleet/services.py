"""Vehicle cards, odometer history and readiness for rental (ТЗ § 8.1, § 8.2.12, § 8.5.1,
§ 8.7.7)."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import Company
from c4rsys.fleet import characteristics, owners
from c4rsys.fleet.characteristics import Characteristics
from c4rsys.fleet.models import (
    OdometerReading,
    Vehicle,
    VehicleCard,
    VehicleCardContext,
    VehiclePlate,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.registry import allocate_public_id

# Context types a vehicle may be allowed in (§ 8.5.1): all three of the MVP (decision 22.10.2026).
USAGE_CONTEXT_TYPES = frozenset({"rental", "service", "warehouse"})


def cards_of(company_id: UUID) -> QuerySet[VehicleCard]:
    return (
        VehicleCard.objects.filter(company_id=company_id)
        .select_related("vehicle", "vehicle__make", "vehicle__model")
        .prefetch_related("contexts")
        .order_by("name", "created_at")
    )


def get_card(company_id: UUID, card_id: UUID) -> VehicleCard:
    card = cards_of(company_id).filter(pk=card_id).first()
    if card is None:
        raise errors.not_found()
    return card


def allowed_contexts(card: VehicleCard) -> set[str]:
    return {c.context_type for c in card.contexts.all()}


# --- Creation and change (§ 8.1.3, § 8.1.10, § 8.1.14) ------------------------------------


@dataclass(frozen=True)
class NewVehicle:
    name: str
    location_id: UUID
    odometer_unit: str
    details: Characteristics = field(default_factory=Characteristics)
    relation: str = VehicleCard.Relation.OWN
    owner: owners.Owner = field(default_factory=owners.Owner)


def create_vehicle(ctx: CommandContext, company: Company, data: NewVehicle) -> VehicleCard:
    """A new global vehicle with its VHL ID and the company's own card in the garage, bound to a
    location (§ 8.1.3). Only the name is required (§ 8.1.10). The usage contexts are the
    applicable contexts existing in the company at creation (§ 8.5.1)."""
    locations.require_usable(company.id, data.location_id, "location_id")
    # The name may be formed from the make, the model and the plate (§ 8.1.10).
    name = data.name or characteristics.make_name(data.details)
    if not name:
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    now = timezone.now()
    vehicle = Vehicle(created_at=now)
    characteristics.apply(vehicle, data.details)
    vehicle.public_id = allocate_public_id("VHL", entity_type="vehicle", entity_id=vehicle.id)
    vehicle.save(force_insert=True)
    if data.details.plate:
        VehiclePlate.objects.create(
            vehicle=vehicle,
            number=data.details.plate,
            number_normalized=characteristics.normalize_plate(data.details.plate),
            country=data.details.plate_country,
            valid_from=now,
        )
    card = VehicleCard.objects.create(
        company=company,
        vehicle=vehicle,
        name=name,
        relation=data.relation,
        accounting_location_id=data.location_id,
        odometer_unit=data.odometer_unit,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )
    contexts = sorted(locations.context_types_in_company(company.id) & USAGE_CONTEXT_TYPES)
    VehicleCardContext.objects.bulk_create(
        VehicleCardContext(company=company, vehicle_card=card, context_type=t) for t in contexts
    )
    ctx.audit(
        "vehicle.created",
        object_type="vehicle_card",
        object_id=card.id,
        after={
            "vehicle": vehicle.public_id,
            "name": card.name,
            "relation": card.relation,
            "accounting_location": str(data.location_id),
            "odometer_unit": card.odometer_unit,
            "contexts": contexts,
            "characteristics": data.details.payload(),
            "identifier_matches": characteristics.matches(vehicle),
        },
    )
    owners.declare(ctx, company, card, data.owner)
    return card


@dataclass(frozen=True)
class VehicleChanges:
    """The name and the usage contexts change; location and odometer unit do not (location by
    transfer or return, unit by § 8.2.10 later)."""

    name: str | None = None
    contexts: frozenset[str] | None = None


def update_vehicle(
    ctx: CommandContext,
    company_id: UUID,
    card_id: UUID,
    changes: VehicleChanges,
    expected_version: int,
) -> VehicleCard:
    """The asset ID never changes (§ 8.1.14). A context switched off forbids new operations in it
    but does not stop started ones; a context switched on is the company's explicit choice, later
    contexts are not added automatically (§ 8.5.1, decision 22.10.2026)."""
    card = VehicleCard.objects.select_for_update().filter(company_id=company_id, pk=card_id).first()
    if card is None:
        raise errors.not_found()
    if card.version != expected_version:
        raise errors.version_conflict()
    before: dict[str, Any] = {"name": card.name, "contexts": sorted(allowed_contexts(card))}
    if changes.name is not None:
        card.name = changes.name
    if changes.contexts is not None:
        VehicleCardContext.objects.filter(vehicle_card=card).exclude(
            context_type__in=changes.contexts
        ).delete()
        present = set(
            VehicleCardContext.objects.filter(vehicle_card=card).values_list(
                "context_type", flat=True
            )
        )
        VehicleCardContext.objects.bulk_create(
            VehicleCardContext(company_id=company_id, vehicle_card=card, context_type=t)
            for t in sorted(changes.contexts - present)
        )
    card.version += 1
    card.save(update_fields=["name", "version", "updated_at"])
    after = {
        "name": card.name,
        "contexts": sorted(changes.contexts)
        if changes.contexts is not None
        else before["contexts"],
    }
    ctx.audit(
        "vehicle.changed",
        object_type="vehicle_card",
        object_id=card.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return card


# --- Odometer (§ 8.2.12) --------------------------------------------------------------------


def current_reading(card_id: UUID) -> OdometerReading | None:
    """The latest measurement, not the latest entry."""
    return (
        OdometerReading.objects.filter(vehicle_card_id=card_id)
        .order_by("-measured_at", "-entered_at")
        .first()
    )


def record_reading(
    ctx: CommandContext,
    card: VehicleCard,
    *,
    measured_at: datetime,
    value: Decimal,
    source_type: str,
    source_id: UUID | None = None,
) -> OdometerReading:
    """Stores a reading inside the caller's command (inspections). The card row is locked so that
    concurrent readings are checked against each other. A value below the nearest earlier
    measurement or above the nearest later one is refused with the conflicting readings; an
    odometer replacement is a separate service procedure (§ 8.2.13, later)."""
    if value < 0:
        raise errors.validation_error("odometer", code="ODOMETER_INVALID")
    VehicleCard.objects.select_for_update().filter(pk=card.pk).first()
    readings = OdometerReading.objects.filter(vehicle_card=card)
    earlier = readings.filter(measured_at__lte=measured_at).order_by("-measured_at").first()
    later = readings.filter(measured_at__gte=measured_at).order_by("measured_at").first()
    conflicts = [
        r
        for r in (earlier, later)
        if r is not None
        and (
            (r.measured_at < measured_at and r.value > value)
            or (r.measured_at > measured_at and r.value < value)
            or (r.measured_at == measured_at and r.value != value)
        )
    ]
    if conflicts:
        raise errors.business_rule(
            "ODOMETER_SEQUENCE_CONFLICT",
            field="odometer",
            conflicts=[
                {"id": str(r.id), "measured_at": r.measured_at.isoformat(), "value": str(r.value)}
                for r in conflicts
            ],
        )
    reading = OdometerReading.objects.create(
        company_id=card.company_id,
        vehicle_card=card,
        measured_at=measured_at,
        entered_at=timezone.now(),
        entered_by_user_id=ctx.actor.user_id,
        value=value,
        unit=card.odometer_unit,
        source_type=source_type,
        source_id=source_id,
    )
    ctx.audit(
        "vehicle.odometer_recorded",
        object_type="vehicle_card",
        object_id=card.id,
        after={
            "reading": str(reading.id),
            "measured_at": measured_at,
            "value": value,
            "unit": reading.unit,
            "source_type": source_type,
            "source_id": str(source_id) if source_id else None,
        },
    )
    return reading


# --- Readiness for rental (§ 8.7.7) ---------------------------------------------------------


def rental_readiness(card: VehicleCard) -> dict[str, Any]:
    """Own or managed, bound to a location with an enabled rental context, allowed for rental.
    An enabled context counts without a tariff; the tariff is checked at issue (decision
    22.10.2026). Documents and optional characteristics are not required (§ 8.7.7)."""
    missing = []
    if card.state != VehicleCard.State.ACTIVE:
        missing.append("card_archived")
    if card.relation not in (VehicleCard.Relation.OWN, VehicleCard.Relation.MANAGED):
        missing.append("not_owned_or_managed")
    location_id = card.accounting_location_id
    if location_id is None or "rental" not in locations.enabled_context_types(
        card.company_id, location_id
    ):
        missing.append("location_without_rental_context")
    if "rental" not in allowed_contexts(card):
        missing.append("rental_not_allowed")
    return {"ready": not missing, "missing": missing}


def lock_card(company_id: UUID, card_id: UUID) -> VehicleCard:
    """Locks the card first in a command (lock order: vehicle → rental → order → cash desk)."""
    card = VehicleCard.objects.select_for_update().filter(company_id=company_id, pk=card_id).first()
    if card is None:
        raise errors.not_found()
    return card


def move_to_location(
    ctx: CommandContext, card: VehicleCard, location_id: UUID, reason: str
) -> None:
    """Changes the accounting location after an event that moves it, for example a return from
    rental accepted in another location (§ 8.5.4). The change is kept in the history."""
    if card.accounting_location_id == location_id:
        return
    before = card.accounting_location_id
    card.accounting_location_id = location_id
    card.version += 1
    card.save(update_fields=["accounting_location", "version", "updated_at"])
    ctx.audit(
        "vehicle.location_changed",
        object_type="vehicle_card",
        object_id=card.id,
        before={"accounting_location": str(before) if before else None},
        after={"accounting_location": str(location_id)},
        reason=reason,
    )


def location_blockers(company_id: UUID, location_id: UUID, context_type: str | None) -> list[str]:
    """Vehicles are moved to other locations before the location is deactivated (§ 4.4.1); they
    do not block switching off a context (§ 4.4.2)."""
    if context_type is not None:
        return []
    present = VehicleCard.objects.filter(
        company_id=company_id, accounting_location_id=location_id, state=VehicleCard.State.ACTIVE
    ).exists()
    return ["vehicles_in_location"] if present else []
