"""Vehicle cards, odometer history and readiness for rental (ТЗ § 8.1, § 8.2.12, § 8.5.1,
§ 8.7.7)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import Company
from c4rsys.fleet import characteristics, identity, owners
from c4rsys.fleet.characteristics import Characteristics
from c4rsys.fleet.models import (
    OdometerReading,
    Vehicle,
    VehicleCard,
    VehicleCardContext,
    VehicleCardWorkingValue,
    VehiclePlate,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.registry import allocate_public_id

# Operational context types a vehicle is switched on for (§ 8.5.1): rental now; taxi, cargo and
# the like later. Service is implied for every vehicle of the company and is not switched; the
# warehouse is not a usage context — an own vehicle is transferred there by a separate operation
# (§ 8.1.5, § 8.3.9). Customer's decision of 24.10.2026, replaces that of 22.10.2026.
USAGE_CONTEXT_TYPES = frozenset({"rental"})

# (company_id, vehicle_card_id, context_type) -> codes of unfinished operations in that context;
# registered by the module of the context. A context is switched off only without them
# (decision of 24.10.2026).
ContextBlockers = Callable[[UUID, UUID, str], list[str]]
_context_blockers: list[ContextBlockers] = []


def register_context_blockers(check: ContextBlockers) -> None:
    _context_blockers.append(check)


def context_blockers(company_id: UUID, card_id: UUID, context_type: str) -> list[str]:
    return [
        code for check in _context_blockers for code in check(company_id, card_id, context_type)
    ]


# (company_id, vehicle_card_id, start, end or None for an open end) -> operations of other
# modules that hold the vehicle in the interval, as {"kind", "id", ...}. A booking and an issue
# are refused over them like over another rental (customer's decision С-3 of 29.10.2026).
Occupancy = Callable[[UUID, UUID, datetime, datetime | None], list[dict[str, Any]]]
_occupancy: list[Occupancy] = []


def register_occupancy(provider: Occupancy) -> None:
    _occupancy.append(provider)


def occupancy(
    company_id: UUID, card_id: UUID, start: datetime, end: datetime | None
) -> list[dict[str, Any]]:
    return [item for p in _occupancy for item in p(company_id, card_id, start, end)]


# The schedule of vehicles (decision Б3, В-4): (company_id, card_ids, start, end) -> intervals
# {"card_id", "kind", "id", "start", "end", "state", "label", "planned"} of each module.
Schedule = Callable[[UUID, list[UUID], datetime, datetime], list[dict[str, Any]]]
_schedule: dict[str, Schedule] = {}


def register_schedule(kind: str, provider: Schedule) -> None:
    _schedule[kind] = provider


def schedule(
    company_id: UUID, card_ids: list[UUID], start: datetime, end: datetime, kinds: set[str]
) -> list[dict[str, Any]]:
    return [
        item
        for kind, provider in _schedule.items()
        if kind in kinds
        for item in provider(company_id, card_ids, start, end)
    ]


# (company_id, vehicle_card_id, moment) -> operations of the vehicle that began at or after the
# moment, as {"kind", "id", ...}: what depends on an earlier move (§ 15.1.4, § 15.3.4; decision
# Д-11).
LaterOperations = Callable[[UUID, UUID, datetime], list[dict[str, Any]]]
_later: list[LaterOperations] = []


def register_later_operations(provider: LaterOperations) -> None:
    _later.append(provider)


def later_operations(company_id: UUID, card_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    return [item for p in _later for item in p(company_id, card_id, moment)]


def cards_of(company_id: UUID, *, include_merged: bool = False) -> QuerySet[VehicleCard]:
    """Cards of the company; a duplicate merged into another card is left out of the lists: it
    redirects to the main card (§ 8.6.6)."""
    found = VehicleCard.objects.filter(company_id=company_id)
    if not include_merged:
        found = found.exclude(state=VehicleCard.State.MERGED)
    return (
        found.select_related("vehicle", "vehicle__make", "vehicle__model")
        .prefetch_related("contexts")
        .order_by("name", "created_at")
    )


def get_card(company_id: UUID, card_id: UUID) -> VehicleCard:
    card = cards_of(company_id, include_merged=True).filter(pk=card_id).first()
    if card is None:
        raise errors.not_found()
    return card


def allowed_contexts(card: VehicleCard) -> set[str]:
    return {c.context_type for c in card.contexts.all()}


# --- Creation and change (§ 8.1.3, § 8.1.10, § 8.1.14) ------------------------------------


@dataclass(frozen=True)
class NewVehicle:
    name: str
    location_id: UUID | None
    odometer_unit: str
    details: Characteristics = field(default_factory=Characteristics)
    relation: str = VehicleCard.Relation.OWN
    owner: owners.Owner = field(default_factory=owners.Owner)
    # Allowed usage contexts given by an import (§ 22.3.1); None — all applicable ones.
    contexts: tuple[str, ...] | None = None
    possible_duplicate: bool = False


def create_vehicle(ctx: CommandContext, company: Company, data: NewVehicle) -> VehicleCard:
    """A new global vehicle with its VHL ID and the company's own card in the garage, bound to a
    location (§ 8.1.3). Only the name is required (§ 8.1.10). The usage contexts are the
    applicable contexts existing in the company at creation (§ 8.5.1)."""
    # A client vehicle is not bound to a location of the company (§ 8.1.2, § 8.1.3).
    if data.relation == VehicleCard.Relation.CLIENT:
        location_id = None
    elif data.location_id is None:
        raise errors.validation_error("location_id", code="FIELD_REQUIRED")
    else:
        location_id = data.location_id
        locations.require_usable(company.id, location_id, "location_id")
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
            region=data.details.plate_region,
            valid_from=now,
        )
    card = VehicleCard.objects.create(
        company=company,
        vehicle=vehicle,
        name=name,
        relation=data.relation,
        accounting_location_id=location_id,
        odometer_unit=data.odometer_unit,
        possible_duplicate=data.possible_duplicate,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )
    usable = USAGE_CONTEXT_TYPES
    if data.relation == VehicleCard.Relation.CLIENT:
        # A client vehicle is only serviced, and service is implied (§ 8.1.2).
        usable = frozenset()
    contexts = sorted(locations.context_types_in_company(company.id) & usable)
    if data.contexts is not None:
        if not set(data.contexts) <= set(contexts):
            raise errors.validation_error("contexts", code="CONTEXT_NOT_APPLICABLE")
        contexts = sorted(set(data.contexts))
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
            "accounting_location": str(location_id) if location_id else None,
            "odometer_unit": card.odometer_unit,
            "contexts": contexts,
            "characteristics": data.details.payload(),
            "identifier_matches": characteristics.matches(vehicle),
        },
    )
    # A match is recorded as a conflict; it never blocks the card (§ 8.6.3).
    identity.refresh(vehicle, now, data.details.originals)
    owners.declare(ctx, company, card, data.owner)
    return card


def update_details(
    ctx: CommandContext,
    company: Company,
    card_id: UUID,
    details: Characteristics,
    owner: owners.Owner,
    name: str,
    expected_version: int,
) -> VehicleCard:
    """Corrects the identification data, characteristics, name and declared owner (decision of
    24.10.2026). They are unconfirmed declarations of the company, so they are changed directly
    (§ 8.2.1 restricts confirmed factory data). A new plate closes the period of the current one
    (§ 8.2.11); a new owner is a new declaration (§ 8.3.13). Relation and location are not
    changed here: location by a move (§ 8.5.2)."""
    card = (
        VehicleCard.objects.select_for_update()
        .select_related("vehicle")
        .filter(company_id=company.id, pk=card_id)
        .first()
    )
    if card is None:
        raise errors.not_found()
    if card.version != expected_version:
        raise errors.version_conflict()
    vehicle = Vehicle.objects.select_for_update().get(pk=card.vehicle_id)
    if identity.resolve(vehicle.id) != vehicle.id:
        # Linked to another entity, waiting for the company's merge of its two cards (§ 8.6.2).
        raise errors.business_rule("VEHICLE_PENDING_LOCAL_MERGE")
    before = characteristics.describe(vehicle)
    before_name = card.name
    now = timezone.now()
    before_working, after_working = _apply_details(ctx, card, vehicle, details, now)
    vehicle.save()
    current = vehicle.plates.filter(valid_to__isnull=True).first()
    plate_changed = (
        (current.number if current else "") != details.plate
        or (current.country if current else "") != details.plate_country
        or (current.region if current else "") != details.plate_region
    )
    if plate_changed:
        if current is not None:
            current.valid_to = now
            current.save(update_fields=["valid_to"])
        if details.plate:
            VehiclePlate.objects.create(
                vehicle=vehicle,
                number=details.plate,
                number_normalized=characteristics.normalize_plate(details.plate),
                country=details.plate_country,
                region=details.plate_region,
                valid_from=now,
            )
    card.name = name or characteristics.make_name(details) or card.name
    card.version += 1
    card.save(update_fields=["name", "version", "updated_at"])
    after = characteristics.describe(vehicle)
    changed = [k for k in after if before.get(k) != after[k] and k != "identifier_matches"]
    working_changed = before_working != after_working
    ctx.audit(
        "vehicle.details_changed",
        object_type="vehicle_card",
        object_id=card.id,
        before={
            "name": before_name,
            **{k: before.get(k) for k in changed},
            **({"working_values": before_working} if working_changed else {}),
        },
        after={
            "name": card.name,
            **{k: after[k] for k in changed},
            **({"working_values": after_working} if working_changed else {}),
        },
    )
    identity.refresh(vehicle, now, details.originals)
    if not owners.is_current(card, owner):
        owners.declare(ctx, company, card, owner)
    return card


def _apply_details(
    ctx: CommandContext,
    card: VehicleCard,
    vehicle: Vehicle,
    details: Characteristics,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The form's values go to the global vehicle, except where the card keeps its own working
    value: there the value of the card changes (or ends when it equals the global one). A
    confirmed factory value is not changed directly: a correction is proposed (§ 8.2.1)."""
    working = {
        w.field: w
        for w in VehicleCardWorkingValue.objects.select_for_update().filter(
            vehicle_card=card, ended_at__isnull=True
        )
    }
    before = {name: w.value for name, w in working.items()}
    after = dict(before)
    vehicle.transport_class = details.transport_class
    for name in characteristics.FIELDS:
        new = characteristics.value_in(details, name)
        current = characteristics.value_of(vehicle, name)
        kept = working.get(name)
        if kept is not None:
            if new == kept.value:
                continue
            kept.ended_at = now
            kept.save(update_fields=["ended_at"])
            after.pop(name)
            if new != current:
                VehicleCardWorkingValue.objects.create(
                    company_id=card.company_id,
                    vehicle_card=card,
                    field=name,
                    value=new,
                    origin=VehicleCardWorkingValue.Origin.CARD,
                    created_by_user_id=ctx.actor.user_id,
                    created_at=now,
                )
                after[name] = new
            continue
        if new == current:
            continue
        if name in vehicle.confirmed_fields:
            raise errors.business_rule("VEHICLE_CHARACTERISTIC_CONFIRMED", field=name)
        characteristics.set_value(vehicle, name, new)
    return before, after


def require_open_for_new(card: VehicleCard) -> None:
    """A duplicate merged into the main card is closed for new operations (§ 8.6.6)."""
    if card.state == VehicleCard.State.MERGED:
        raise errors.business_rule(
            "VEHICLE_CARD_MERGED", field="vehicle_card_id", main=str(card.merged_into_id)
        )


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
        if card.storage_context_id is not None and changes.contexts:
            raise errors.business_rule("VEHICLE_IN_WAREHOUSE", field="contexts")
        found = sorted(
            {
                code
                for removed in set(before["contexts"]) - changes.contexts
                for code in context_blockers(company_id, card.id, removed)
            }
        )
        if found:
            raise errors.business_rule(
                "VEHICLE_CONTEXT_HAS_UNFINISHED", field="contexts", blockers=found
            )
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


def replace_reading(
    ctx: CommandContext,
    card: VehicleCard,
    *,
    source_type: str,
    source_id: UUID,
    measured_at: datetime,
    value: Decimal,
) -> OdometerReading:
    """A corrected reading of a source replaces its wrong one; the wrong one stays with the link
    to its replacement (§ 16.8.2). The sequence is checked without the replaced reading."""
    old = (
        OdometerReading.objects.select_for_update()
        .filter(vehicle_card=card, source_type=source_type, source_id=source_id)
        .filter(replaced_by__isnull=True)
        .first()
    )
    new = record_reading(
        ctx,
        card,
        measured_at=measured_at,
        value=value,
        source_type=source_type,
        source_id=source_id,
        replaces=old,
    )
    if old is not None:
        old.replaced_by = new
        old.save(update_fields=["replaced_by"])
    return new


def current_reading(card_id: UUID) -> OdometerReading | None:
    """The latest measurement, not the latest entry."""
    return (
        OdometerReading.objects.filter(vehicle_card_id=card_id, replaced_by__isnull=True)
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
    replaces: OdometerReading | None = None,
) -> OdometerReading:
    """Stores a reading inside the caller's command (inspections). The card row is locked so that
    concurrent readings are checked against each other. A value below the nearest earlier
    measurement or above the nearest later one is refused with the conflicting readings; an
    odometer replacement is a separate service procedure (§ 8.2.13, later)."""
    if value < 0:
        raise errors.validation_error("odometer", code="ODOMETER_INVALID")
    VehicleCard.objects.select_for_update().filter(pk=card.pk).first()
    readings = OdometerReading.objects.filter(vehicle_card=card, replaced_by__isnull=True)
    if replaces is not None:
        readings = readings.exclude(pk=replaces.pk)
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


def transfer_to_warehouse(
    ctx: CommandContext, company_id: UUID, card_id: UUID, context_id: UUID, expected_version: int
) -> VehicleCard:
    """An own vehicle without any operational context enabled becomes a stock item of a
    warehouse context, keeping its ID, characteristics and history (§ 8.1.5, § 8.1.7; decision
    of 24.10.2026). A managed or client vehicle is not transferred."""
    card = lock_card(company_id, card_id)
    if card.version != expected_version:
        raise errors.version_conflict()
    if card.relation != VehicleCard.Relation.OWN:
        raise errors.business_rule("VEHICLE_NOT_OWN")
    if card.storage_context_id is not None:
        raise errors.business_rule("VEHICLE_IN_WAREHOUSE")
    if allowed_contexts(card):
        raise errors.business_rule("VEHICLE_HAS_CONTEXTS", contexts=sorted(allowed_contexts(card)))
    target = locations.storage_context(company_id, context_id)
    if target is None:
        raise errors.validation_error("context_id", code="WAREHOUSE_UNKNOWN")
    before = {"location": str(card.accounting_location_id) if card.accounting_location_id else None}
    card.storage_context_id = target.id
    card.stored_at = timezone.now()
    card.accounting_location_id = target.location_id
    card.version += 1
    card.save(
        update_fields=[
            "storage_context",
            "stored_at",
            "accounting_location",
            "version",
            "updated_at",
        ]
    )
    ctx.audit(
        "vehicle.transferred_to_warehouse",
        object_type="vehicle_card",
        object_id=card.id,
        before=before,
        after={"storage_context": str(target.id), "location": str(target.location_id)},
    )
    return card


# card_id -> codes of what keeps a vehicle in the warehouse (a reserve in an open order, ...);
# registered by the modules that own them.
GarageBlockers = Callable[[UUID, UUID], list[str]]
_garage_blockers: list[GarageBlockers] = []


def register_garage_blockers(check: GarageBlockers) -> None:
    _garage_blockers.append(check)


def return_to_garage(
    ctx: CommandContext, company_id: UUID, card_id: UUID, expected_version: int
) -> VehicleCard:
    """§ 8.4.1: the vehicle returns from the warehouse to the garage as the same vehicle — ID,
    VIN, characteristics, documents and history stay; after it the usual readiness rules apply.
    Related operations must be finished first (§ 8.1.5). It stays bound to the location of the
    warehouse; operational contexts are enabled afterwards by editing the vehicle."""
    card = lock_card(company_id, card_id)
    if card.version != expected_version:
        raise errors.version_conflict()
    if card.storage_context_id is None:
        raise errors.business_rule("VEHICLE_NOT_IN_WAREHOUSE")
    if card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_ARCHIVED")
    found = [code for check in _garage_blockers for code in check(company_id, card.id)]
    if found:
        raise errors.business_rule("VEHICLE_HAS_OPERATIONS", blockers=found)
    before = {"storage_context": str(card.storage_context_id)}
    card.storage_context_id = None
    card.stored_at = None
    card.version += 1
    card.save(update_fields=["storage_context", "stored_at", "version", "updated_at"])
    ctx.audit(
        "vehicle.returned_to_garage",
        object_type="vehicle_card",
        object_id=card.id,
        before=before,
        after={"storage_context": None, "location": str(card.accounting_location_id)},
    )
    return card


def rental_readiness(card: VehicleCard) -> dict[str, Any]:
    """Own or managed, bound to a location with an enabled rental context, allowed for rental.
    An enabled context counts without a tariff; the tariff is checked at issue (decision
    22.10.2026). Documents and optional characteristics are not required (§ 8.7.7)."""
    missing = []
    if card.state == VehicleCard.State.MERGED:
        missing.append("card_merged")
    elif card.state != VehicleCard.State.ACTIVE:
        missing.append("card_archived")
    if card.storage_context_id is not None:
        missing.append("in_warehouse")
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


def company_vehicle(company_id: UUID, card_id: UUID) -> VehicleCard | None:
    """A vehicle of the company that materials are issued to or used on (customer's decision of
    25.10.2026): an active own or managed vehicle in the garage — not a client's vehicle and not
    one transferred to a warehouse (PROPOSAL)."""
    return VehicleCard.objects.filter(
        company_id=company_id,
        pk=card_id,
        relation__in=[VehicleCard.Relation.OWN, VehicleCard.Relation.MANAGED],
        state=VehicleCard.State.ACTIVE,
        storage_context__isnull=True,
    ).first()


def card_names(company_id: UUID, ids: set[UUID]) -> dict[UUID, str]:
    return dict(
        VehicleCard.objects.filter(company_id=company_id, pk__in=ids).values_list("id", "name")
    )


def record_sale(ctx: CommandContext, company_id: UUID, card_id: UUID, order_id: UUID) -> None:
    """A vehicle sold from a warehouse through a closed order: the seller's ownership ends
    (§ 8.3.9). The card leaves the active garage and the warehouse; its ID and history stay.
    PROPOSAL: the card is archived."""
    card = VehicleCard.objects.select_for_update().filter(company_id=company_id, pk=card_id).first()
    if card is None:
        raise errors.not_found()
    if card.storage_context_id is None:
        raise errors.business_rule("VEHICLE_NOT_IN_WAREHOUSE")
    card.state = VehicleCard.State.ARCHIVED
    card.version += 1
    card.save(update_fields=["state", "version", "updated_at"])
    ctx.audit(
        "vehicle.sold",
        object_type="vehicle_card",
        object_id=card.id,
        after={"order": str(order_id), "state": card.state},
    )


def stored_card(company_id: UUID, card_id: UUID) -> VehicleCard | None:
    """An active vehicle of the company transferred to a warehouse — for sale (§ 8.3.9)."""
    return (
        VehicleCard.objects.select_related("storage_context__location")
        .filter(
            company_id=company_id,
            pk=card_id,
            state=VehicleCard.State.ACTIVE,
            storage_context__isnull=False,
        )
        .first()
    )


def stored_cards(company_id: UUID, context_ids: set[UUID]) -> list[UUID]:
    """Active vehicles of the company kept in the given warehouses."""
    return list(
        VehicleCard.objects.filter(
            company_id=company_id,
            state=VehicleCard.State.ACTIVE,
            storage_context_id__in=context_ids,
        ).values_list("id", flat=True)
    )


def set_acquisition(
    ctx: CommandContext,
    company_id: UUID,
    card_id: UUID,
    cost: Decimal | None,
    currency: str | None,
    expected_version: int,
) -> VehicleCard:
    """Only for an own vehicle: payback is counted for the owner company only (§ 21.4.2)."""
    from c4rsys.platform.models import Currency

    card = lock_card(company_id, card_id)
    if card.version != expected_version:
        raise errors.version_conflict()
    if cost is not None:
        if card.relation != VehicleCard.Relation.OWN:
            raise errors.business_rule("VEHICLE_ACQUISITION_ONLY_OWN")
        if cost < 0:
            raise errors.validation_error("cost", code="AMOUNT_INVALID")
        if not Currency.objects.filter(code=currency).exists():
            raise errors.validation_error("currency", code="CURRENCY_UNKNOWN")
    before = {
        "cost": card.acquisition_cost,
        "currency": card.acquisition_currency_id,
    }
    card.acquisition_cost = cost
    card.acquisition_currency_id = currency if cost is not None else None
    card.version += 1
    card.save(update_fields=["acquisition_cost", "acquisition_currency", "version", "updated_at"])
    ctx.audit(
        "vehicle.acquisition_changed",
        object_type="vehicle_card",
        object_id=card.id,
        before=before,
        after={"cost": cost, "currency": card.acquisition_currency_id},
    )
    return card


def applicable_contexts(company_id: UUID) -> set[str]:
    """Usage contexts a new own or managed vehicle may be switched on for (§ 8.5.1)."""
    return set(locations.context_types_in_company(company_id) & USAGE_CONTEXT_TYPES)


def remove_imported(company_id: UUID, card_ids: list[UUID]) -> None:
    """Cancel of an import (§ 22.2.5): the cards it created, with their contexts, declared
    owners and readings; the global vehicle goes too unless another card refers to it. Any other
    reference raises ProtectedError and the caller refuses the cancel."""
    from c4rsys.fleet.models import VehicleOwnerClaim

    identity.leave(card_ids, timezone.now())
    for card in VehicleCard.objects.select_for_update().filter(
        company_id=company_id, pk__in=card_ids
    ):
        vehicle = card.vehicle
        VehicleCardContext.objects.filter(vehicle_card=card).delete()
        VehicleOwnerClaim.objects.filter(vehicle_card=card).delete()
        VehicleCardWorkingValue.objects.filter(vehicle_card=card).delete()
        OdometerReading.objects.filter(vehicle_card=card).delete()
        card.delete()
        if not VehicleCard.objects.filter(vehicle=vehicle).exists():
            vehicle.plates.all().delete()
            vehicle.delete()
