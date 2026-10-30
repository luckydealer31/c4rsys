"""Inspection records (ТЗ § 10.1–10.2, § 10.3.3–10.3.5, § 10.6, § 14.3.1, § 14.5.5).

* The readings entered in the rental card at the issue and the return are saved as an inspection
  record of that rental (§ 10.1.4); the separate form makes an inspection of any kind and
  application: a rental, a service operation or on its own (§ 10.1.1, § 23.4.1).
* States (§ 10.6.3): a draft is filled in and saved; a planned one is started — then it is in
  progress — or cancelled with a reason; a draft or a started one is terminated with a reason, its
  data stay but are not a result; a saved one is never changed, it is recognised invalid with a
  reason by a separate right (§ 10.1.6, § 23.4.2). Only an unused draft is deleted.
* The moment is substituted at the filling and may be changed to the actual one before the save;
  a future moment is refused (§ 10.1.6). The performer is the employee filling it in unless
  another one is given; the author and the moment of the entry are kept apart (§ 10.1.5).
* A required check left undone does not block anything; in a rental it is a conflict, and the
  rental is not closed without a recorded reason (§ 10.3.3).
"""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.companies.models import Employment
from c4rsys.files import services as files
from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import VehicleCard
from c4rsys.inspections import damages, links, policy, templates
from c4rsys.inspections.models import (
    Inspection,
    InspectionFile,
    InspectionMissingReason,
    InspectionTemplateVersion,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

State = Inspection.State
Kind = Inspection.Kind
Application = Inspection.Application
FILLING = (State.DRAFT, State.IN_PROGRESS)
# The kinds whose required check a rental needs before it is closed (§ 10.3.3).
RENTAL_KINDS = (Kind.ISSUE, Kind.RETURN)


# --- Readings of the rental card (§ 10.1.4, § 14.3.1, § 14.5.5) ---------------------------------


@dataclass(frozen=True)
class Readings:
    performed_at: datetime
    place_kind: str
    place_location_id: UUID
    place_note: str
    check_status: str
    odometer: Decimal | None
    fuel: Decimal | None
    check_required: bool = False


def _of_rental_card(
    ctx: CommandContext,
    card: VehicleCard,
    kind: str,
    rental_id: UUID | None,
    readings: Readings,
    state: str,
) -> Inspection:
    if ctx.actor.employment_id is None:
        raise RuntimeError("An inspection is performed by an employee of the company")
    now = timezone.now()
    return Inspection.objects.create(
        company_id=card.company_id,
        vehicle_card=card,
        rental_id=rental_id,
        application=Application.RENTAL if rental_id else Application.STANDALONE,
        origin=Inspection.Origin.RENTAL,
        kind=kind,
        state=state,
        check_status=readings.check_status,
        check_required=readings.check_required,
        performed_at=readings.performed_at,
        place_kind=readings.place_kind,
        place_location_id=readings.place_location_id,
        place_note=readings.place_note,
        performer_employment_id=ctx.actor.employment_id,
        author_user_id=ctx.actor.user_id,
        entered_at=now,
        odometer_value=readings.odometer,
        odometer_unit=card.odometer_unit,
        fuel_value=readings.fuel,
        saved_at=now if state == State.SAVED else None,
    )


def create_saved(
    ctx: CommandContext,
    card: VehicleCard,
    *,
    kind: str,
    rental_id: UUID | None,
    readings: Readings,
) -> Inspection:
    """A saved inspection inside the operation's command. The performer is the acting employee;
    the author and the moment of entry are stored separately (§ 10.1.5)."""
    inspection = _of_rental_card(ctx, card, kind, rental_id, readings, State.SAVED)
    ctx.audit(
        "inspection.saved",
        object_type="inspection",
        object_id=inspection.id,
        after={
            "kind": kind,
            "rental": str(rental_id) if rental_id else None,
            "vehicle_card": str(card.id),
            "check_status": readings.check_status,
            "check_required": readings.check_required,
            "performed_at": readings.performed_at,
            "place_kind": readings.place_kind,
            "place_location": str(readings.place_location_id),
            "place_note": readings.place_note,
            "odometer": readings.odometer,
            "odometer_unit": card.odometer_unit,
            "fuel": readings.fuel,
        },
    )
    return inspection


def create_draft(
    ctx: CommandContext,
    card: VehicleCard,
    *,
    kind: str,
    rental_id: UUID | None,
    readings: Readings,
) -> Inspection:
    """An incomplete record: the return is fixed without the readings (§ 14.5.5)."""
    inspection = _of_rental_card(ctx, card, kind, rental_id, readings, State.DRAFT)
    ctx.audit(
        "inspection.draft_created",
        object_type="inspection",
        object_id=inspection.id,
        after={
            "kind": kind,
            "rental": str(rental_id) if rental_id else None,
            "odometer": readings.odometer,
            "fuel": readings.fuel,
        },
    )
    return inspection


def save_draft(
    ctx: CommandContext,
    inspection: Inspection,
    *,
    odometer: Decimal,
    fuel: Decimal,
    check_status: str,
    check_required: bool | None = None,
) -> Inspection:
    """Completes a draft with the readings and saves it; after that it is immutable (§ 10.1.6,
    § 14.5.5). The moment and place of the inspection stay as recorded."""
    if inspection.state not in FILLING:
        raise errors.business_rule("INSPECTION_NOT_DRAFT")
    before = {"odometer": inspection.odometer_value, "fuel": inspection.fuel_value}
    inspection.odometer_value = odometer
    inspection.fuel_value = fuel
    inspection.check_status = check_status
    if check_required is not None:
        inspection.check_required = check_required
    _require_complete(inspection, policy_of(inspection))
    inspection.state = State.SAVED
    inspection.saved_at = timezone.now()
    inspection.version += 1
    inspection.save()
    ctx.audit(
        "inspection.saved",
        object_type="inspection",
        object_id=inspection.id,
        before=before,
        after={"odometer": odometer, "fuel": fuel, "check_status": check_status},
    )
    damages.record(ctx, inspection)
    return inspection


def of_rental(rental_id: UUID) -> list[Inspection]:
    """Inspections of a rental that count: neither invalid, cancelled nor terminated."""
    return list(
        Inspection.objects.filter(rental_id=rental_id)
        .exclude(state__in=(State.INVALID, State.CANCELLED, State.TERMINATED))
        .order_by("performed_at", "entered_at")
    )


def readings_of_rental(rental_id: UUID, kind: str) -> Inspection | None:
    """The saved readings of the kind; else the record still to be completed (a draft)."""
    found = [i for i in of_rental(rental_id) if i.kind == kind]
    saved = [
        i
        for i in found
        if i.state == State.SAVED and i.odometer_value is not None and i.fuel_value is not None
    ]
    if saved:
        return saved[-1]
    return found[-1] if found else None


# --- The form of an inspection (§ 10.1–10.2) -----------------------------------------------------


@dataclass(frozen=True)
class Place:
    kind: str = Inspection.PlaceKind.LOCATION
    location_id: UUID | None = None
    note: str = ""
    address: str = ""
    lat: Decimal | None = None
    lng: Decimal | None = None


@dataclass(frozen=True)
class Participant:
    kind: str = ""
    client_id: UUID | None = None
    counterparty_id: UUID | None = None
    employment_id: UUID | None = None
    text: str = ""


@dataclass(frozen=True)
class AttachedFile:
    file_id: UUID
    kind: str
    position: str = ""


@dataclass(frozen=True)
class Content:
    """What the employee fills in; all of it may change until the save."""

    performed_at: datetime | None = None
    place: Place = Place()
    participant: Participant = Participant()
    performer_employment_id: UUID | None = None
    odometer: Decimal | None = None
    fuel: Decimal | None = None
    cleanliness: str = ""
    damages: list[dict[str, Any]] = field(default_factory=list)
    files: list[AttachedFile] = field(default_factory=list)


@dataclass(frozen=True)
class NewInspection:
    vehicle_card_id: UUID
    kind: str
    application: str
    content: Content
    rental_id: UUID | None = None
    service_operation_id: UUID | None = None
    template_id: UUID | None = None
    planned_at: datetime | None = None  # a planned inspection (§ 10.6.3)


def _not_future(moment: datetime, name: str) -> None:
    if moment > timezone.now():
        raise errors.business_rule("FUTURE_TIME_NOT_ALLOWED", field=name)


def _link(company_id: UUID, card: VehicleCard, data: NewInspection) -> None:
    """The application and its operation, of the same vehicle (§ 10.1.1, § 23.4.1)."""
    if data.kind not in Kind.values:
        raise errors.validation_error("kind", code="CHOICE_REQUIRED")
    if data.application not in Application.values:
        raise errors.validation_error("application", code="CHOICE_REQUIRED")
    if data.kind == Kind.INTERMEDIATE and data.application == Application.STANDALONE:
        raise errors.validation_error("application", code="INSPECTION_APPLICATION_INVALID")
    if data.kind == Kind.OUT_OF_RENTAL and data.application == Application.RENTAL:
        raise errors.validation_error("application", code="INSPECTION_APPLICATION_INVALID")
    if data.application == Application.RENTAL:
        if data.rental_id is None or data.service_operation_id is not None:
            raise errors.validation_error("rental_id", code="FIELD_REQUIRED")
        rental = links.rental(company_id, data.rental_id)
        if rental is None:
            raise errors.validation_error("rental_id", code="RENTAL_UNKNOWN")
        if rental.vehicle_card_id != card.id:
            raise errors.business_rule("INSPECTION_VEHICLE_MISMATCH", field="rental_id")
        if rental.closed:
            raise errors.business_rule("RENTAL_FORMALIZATION_COMPLETED", field="rental_id")
    elif data.application == Application.SERVICE:
        if data.service_operation_id is None or data.rental_id is not None:
            raise errors.validation_error("service_operation_id", code="FIELD_REQUIRED")
        op = links.service_operation(company_id, data.service_operation_id)
        if op is None:
            raise errors.validation_error("service_operation_id", code="SERVICE_OPERATION_UNKNOWN")
        if op.vehicle_card_id != card.id:
            raise errors.business_rule("INSPECTION_VEHICLE_MISMATCH", field="service_operation_id")
    elif data.rental_id is not None or data.service_operation_id is not None:
        raise errors.validation_error("application", code="INSPECTION_APPLICATION_INVALID")


def _place(company_id: UUID, card: VehicleCard, place: Place) -> Place:
    """By default the vehicle's location; another location of the company; or «another place»
    described by a comment, an address or coordinates (§ 10.1.2). The vehicle stays where it is
    accounted."""
    from c4rsys.companies import locations

    if place.kind == Inspection.PlaceKind.LOCATION:
        location_id = place.location_id or card.accounting_location_id
        if location_id is None:
            raise errors.validation_error("place_location_id", code="FIELD_REQUIRED")
        if not locations.location_exists(company_id, location_id):
            raise errors.validation_error("place_location_id", code="LOCATION_UNKNOWN")
        return Place(Inspection.PlaceKind.LOCATION, location_id)
    if place.kind != Inspection.PlaceKind.OTHER:
        raise errors.validation_error("place_kind")
    if (place.lat is None) != (place.lng is None):
        raise errors.validation_error("place_lat", code="COORDINATES_INVALID")
    if not (place.note or place.address or place.lat is not None):
        raise errors.validation_error("place_note", code="FIELD_REQUIRED")
    if place.location_id is not None and not locations.location_exists(
        company_id, place.location_id
    ):
        raise errors.validation_error("place_location_id", code="LOCATION_UNKNOWN")
    return place


def _participant(company_id: UUID, value: Participant) -> Participant:
    """§ 10.1.3: a client card, a counterparty, an employee or a described person."""
    from c4rsys.parties import counterparties
    from c4rsys.parties import services as parties

    kind = value.kind
    if kind == Inspection.Participant.NONE:
        return Participant()
    if kind == Inspection.Participant.CLIENT and value.client_id:
        parties.get_client(company_id, value.client_id)
        return Participant(kind, client_id=value.client_id)
    if kind == Inspection.Participant.COUNTERPARTY and value.counterparty_id:
        counterparties.get(company_id, value.counterparty_id)
        return Participant(kind, counterparty_id=value.counterparty_id)
    if kind == Inspection.Participant.EMPLOYEE and value.employment_id:
        if not Employment.objects.filter(company_id=company_id, pk=value.employment_id).exists():
            raise errors.validation_error("participant_employment_id", code="EMPLOYEE_UNKNOWN")
        return Participant(kind, employment_id=value.employment_id)
    if kind == Inspection.Participant.OTHER and value.text:
        return Participant(kind, text=value.text)
    if kind not in Inspection.Participant.values:
        raise errors.validation_error("participant_kind")
    raise errors.validation_error("participant", code="FIELD_REQUIRED")


def _performer(ctx: CommandContext, company_id: UUID, employment_id: UUID | None) -> UUID:
    if employment_id is None:
        if ctx.actor.employment_id is None:
            raise RuntimeError("An inspection is performed by an employee of the company")
        return ctx.actor.employment_id
    found = Employment.objects.filter(
        company_id=company_id, pk=employment_id, state=Employment.State.ACTIVE
    ).exists()
    if not found:
        raise errors.validation_error("performer_employment_id", code="EMPLOYEE_NOT_ACTIVE")
    return employment_id


def _apply(ctx: CommandContext, inspection: Inspection, content: Content) -> None:
    company_id = inspection.company_id
    card = inspection.vehicle_card
    if content.performed_at is not None:
        _not_future(content.performed_at, "performed_at")
        inspection.performed_at = content.performed_at
    place = _place(company_id, card, content.place)
    inspection.place_kind = place.kind
    inspection.place_location_id = place.location_id
    inspection.place_note = place.note
    inspection.place_address = place.address
    inspection.place_lat = place.lat
    inspection.place_lng = place.lng
    participant = _participant(company_id, content.participant)
    inspection.participant_kind = participant.kind
    inspection.participant_client_id = participant.client_id
    inspection.participant_counterparty_id = participant.counterparty_id
    inspection.participant_employment_id = participant.employment_id
    inspection.participant_text = participant.text
    inspection.performer_employment_id = _performer(
        ctx, company_id, content.performer_employment_id
    )
    inspection.odometer_value = content.odometer
    inspection.fuel_value = content.fuel
    inspection.cleanliness = content.cleanliness
    inspection.damages_draft = content.damages


def _set_files(ctx: CommandContext, inspection: Inspection, wanted: list[AttachedFile]) -> None:
    """The files of the inspection become exactly the given ones; a new file is linked by its
    uploader (A10)."""
    positions = _positions(inspection.template_version)
    current = {f.file_id: f for f in InspectionFile.objects.filter(inspection=inspection)}
    keep: set[UUID] = set()
    for index, item in enumerate(wanted):
        name = f"files[{index}]"
        if item.kind not in InspectionFile.Kind.values:
            raise errors.validation_error(f"{name}.kind")
        if item.kind == InspectionFile.Kind.VIDEO and item.position:
            raise errors.validation_error(f"{name}.position", code="VIDEO_WITHOUT_POSITION")
        if item.position and item.position not in positions:
            raise errors.validation_error(f"{name}.position", code="POSITION_UNKNOWN")
        if item.file_id in keep:
            raise errors.validation_error(f"{name}.file_id", code="FILE_REPEATED")
        keep.add(item.file_id)
        existing = current.get(item.file_id)
        if existing is not None:
            if (existing.kind, existing.position) == (item.kind, item.position):
                continue
            existing.delete()
        else:
            purpose = (
                files.INSPECTION_PHOTO
                if item.kind == InspectionFile.Kind.PHOTO
                else files.INSPECTION_VIDEO
            )
            files.usable(
                item.file_id,
                purpose=purpose,
                user_id=ctx.actor.user_id,
                company_id=inspection.company_id,
            )
            files.attach_to_company(item.file_id, inspection.company_id)
        InspectionFile.objects.create(
            company_id=inspection.company_id,
            inspection=inspection,
            file_id=item.file_id,
            kind=item.kind,
            position=item.position,
            added_by_user_id=ctx.actor.user_id,
            added_at=timezone.now(),
        )
    for file_id, stored in current.items():
        if file_id not in keep:
            stored.delete()


def _positions(version: InspectionTemplateVersion | None) -> dict[str, dict[str, Any]]:
    if version is None:
        return {}
    return {p["code"]: p for p in version.positions if p.get("enabled")}


def snapshot(inspection: Inspection) -> dict[str, Any]:
    return {
        "kind": inspection.kind,
        "application": inspection.application,
        "rental": str(inspection.rental_id) if inspection.rental_id else None,
        "service_operation": str(inspection.service_operation_id)
        if inspection.service_operation_id
        else None,
        "vehicle_card": str(inspection.vehicle_card_id),
        "state": inspection.state,
        "performed_at": inspection.performed_at,
        "place_kind": inspection.place_kind,
        "place_location": str(inspection.place_location_id)
        if inspection.place_location_id
        else None,
        "place_note": inspection.place_note,
        "place_address": inspection.place_address,
        "participant_kind": inspection.participant_kind,
        "performer": str(inspection.performer_employment_id),
        "odometer": inspection.odometer_value,
        "fuel": inspection.fuel_value,
        "cleanliness": inspection.cleanliness,
        "damages": len(inspection.damages_draft or []),
        "files": InspectionFile.objects.filter(inspection=inspection).count(),
    }


def create(ctx: CommandContext, company_id: UUID, data: NewInspection) -> Inspection:
    """A draft, or a planned inspection when its planned moment is given (§ 10.6.3)."""
    card = fleet.lock_card(company_id, data.vehicle_card_id)
    _link(company_id, card, data)
    version = templates.version_for(company_id, card, data.template_id)
    now = timezone.now()
    planned = data.planned_at is not None
    inspection = Inspection(
        company_id=company_id,
        vehicle_card=card,
        kind=data.kind,
        application=data.application,
        rental_id=data.rental_id,
        service_operation_id=data.service_operation_id,
        origin=Inspection.Origin.FORM,
        state=State.PLANNED if planned else State.DRAFT,
        check_status=Inspection.CheckStatus.NOT_REQUIRED,
        performed_at=data.planned_at if planned and data.planned_at else now,
        planned_at=data.planned_at,
        author_user_id=ctx.actor.user_id,
        entered_at=now,
        odometer_unit=card.odometer_unit,
        template_version=version,
    )
    content = data.content
    if planned and (content.files or content.damages):
        raise errors.validation_error("files", code="INSPECTION_NOT_STARTED")
    if planned and content.performed_at is not None:
        content = replace(content, performed_at=None)
    _apply(ctx, inspection, content)
    inspection.check_required = policy_of(inspection).check_required
    inspection.save(force_insert=True)
    _set_files(ctx, inspection, content.files)
    ctx.audit(
        "inspection.planned" if planned else "inspection.draft_created",
        object_type="inspection",
        object_id=inspection.id,
        after={
            **snapshot(inspection),
            "planned_at": data.planned_at,
            "template_version": str(version.id) if version else None,
        },
    )
    return inspection


def locked(company_id: UUID, inspection_id: UUID, expected_version: int) -> Inspection:
    found = Inspection.objects.filter(company_id=company_id, pk=inspection_id).first()
    if found is None:
        raise errors.not_found()
    fleet.lock_card(company_id, found.vehicle_card_id)  # lock order: vehicle → inspection
    inspection = Inspection.objects.select_for_update().get(pk=found.pk)
    if inspection.version != expected_version:
        raise errors.version_conflict()
    return inspection


def _bump(inspection: Inspection) -> None:
    inspection.version += 1
    inspection.save()


def update(ctx: CommandContext, inspection: Inspection, content: Content) -> Inspection:
    """A draft, a planned or a started inspection is changed until the save (§ 10.1.6)."""
    if inspection.state not in (*FILLING, State.PLANNED):
        raise errors.business_rule("INSPECTION_NOT_EDITABLE", state=inspection.state)
    if inspection.state == State.PLANNED and (content.files or content.damages):
        raise errors.business_rule("INSPECTION_NOT_STARTED")
    before = snapshot(inspection)
    if inspection.state == State.PLANNED:
        content = replace(content, performed_at=None)
    _apply(ctx, inspection, content)
    _bump(inspection)
    _set_files(ctx, inspection, content.files)
    after = snapshot(inspection)
    ctx.audit(
        "inspection.changed",
        object_type="inspection",
        object_id=inspection.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return inspection


def start(ctx: CommandContext, inspection: Inspection) -> Inspection:
    """A planned inspection begins; its moment becomes the actual one (§ 10.1.6)."""
    if inspection.state != State.PLANNED:
        raise errors.business_rule("INSPECTION_NOT_PLANNED", state=inspection.state)
    now = timezone.now()
    inspection.state = State.IN_PROGRESS
    inspection.started_at = now
    inspection.performed_at = now
    _bump(inspection)
    ctx.audit(
        "inspection.started",
        object_type="inspection",
        object_id=inspection.id,
        before={"state": State.PLANNED},
        after={"state": inspection.state, "started_at": now},
    )
    return inspection


def policy_of(inspection: Inspection) -> policy.Effective:
    location_id = inspection.place_location_id
    if inspection.rental_id is not None and inspection.application == Application.RENTAL:
        rental = links.rental(inspection.company_id, inspection.rental_id)
        if rental is not None:
            location_id = (
                rental.issue_location_id
                if inspection.kind == Kind.ISSUE
                else rental.return_location_id or location_id
            )
    return policy.effective(
        inspection.company_id, inspection.kind, inspection.application, location_id
    )


def _require_complete(inspection: Inspection, rules: policy.Effective) -> None:
    """Required readings are required whether or not the check is done (§ 10.3.4); the required
    photos of the template only for a conducted check (§ 10.3.4–10.3.5)."""
    missing = []
    if rules.odometer_required and inspection.odometer_value is None:
        missing.append("odometer")
    if rules.fuel_required and inspection.fuel_value is None:
        missing.append("fuel")
    if rules.cleanliness_required and not inspection.cleanliness:
        missing.append("cleanliness")
    if inspection.check_status == Inspection.CheckStatus.CONDUCTED:
        taken: dict[str, int] = {}
        for code in InspectionFile.objects.filter(
            inspection=inspection, kind=InspectionFile.Kind.PHOTO
        ).values_list("position", flat=True):
            taken[code] = taken.get(code, 0) + 1
        for code, position in _positions(inspection.template_version).items():
            if position.get("required") and taken.get(code, 0) < position["count"]:
                missing.append(f"photo:{code}")
    if missing:
        raise errors.business_rule("INSPECTION_REQUIRED_MISSING", missing=missing)


def complete(ctx: CommandContext, inspection: Inspection, conducted: bool) -> Inspection:
    """Saves a draft or a started inspection; after that nothing changes (§ 10.1.6). The check
    is «conducted», «not required» or «required, not done» (§ 10.1.5). The odometer goes to the
    vehicle (§ 8.2.12) and the damages become records (§ 10.5.2)."""
    if inspection.state not in FILLING:
        raise errors.business_rule("INSPECTION_NOT_DRAFT", state=inspection.state)
    _not_future(inspection.performed_at, "performed_at")
    rules = policy_of(inspection)
    inspection.check_required = inspection.check_required or rules.check_required
    inspection.check_status = (
        Inspection.CheckStatus.CONDUCTED
        if conducted
        else Inspection.CheckStatus.REQUIRED_NOT_DONE
        if inspection.check_required
        else Inspection.CheckStatus.NOT_REQUIRED
    )
    _require_complete(inspection, rules)
    if inspection.rental_id is not None:
        rental = links.rental(inspection.company_id, inspection.rental_id)
        if rental is not None and rental.closed:
            raise errors.business_rule("RENTAL_FORMALIZATION_COMPLETED")
    now = timezone.now()
    before = inspection.state
    inspection.state = State.SAVED
    inspection.saved_at = now
    _bump(inspection)
    if inspection.odometer_value is not None:
        fleet.record_reading(
            ctx,
            inspection.vehicle_card,
            measured_at=inspection.performed_at,
            value=inspection.odometer_value,
            source_type="inspection",
            source_id=inspection.id,
        )
    ctx.audit(
        "inspection.saved",
        object_type="inspection",
        object_id=inspection.id,
        before={"state": before},
        after={
            **snapshot(inspection),
            "check_status": inspection.check_status,
            "check_required": inspection.check_required,
            "template_version": str(inspection.template_version_id)
            if inspection.template_version_id
            else None,
        },
    )
    damages.record(ctx, inspection)
    links.saved(ctx, inspection)
    return inspection


def _reason(reason: str) -> str:
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    return reason.strip()


def _end(ctx: CommandContext, inspection: Inspection, state: str, reason: str) -> Inspection:
    before = inspection.state
    inspection.state = state
    inspection.end_reason = _reason(reason)
    inspection.ended_at = timezone.now()
    inspection.ended_by_user_id = ctx.actor.user_id
    _bump(inspection)
    ctx.audit(
        f"inspection.{state}",
        object_type="inspection",
        object_id=inspection.id,
        before={"state": before},
        after={"state": state},
        reason=inspection.end_reason,
    )
    return inspection


def cancel(ctx: CommandContext, inspection: Inspection, reason: str) -> Inspection:
    """A planned inspection not started (§ 10.6.3)."""
    if inspection.state != State.PLANNED:
        raise errors.business_rule("INSPECTION_NOT_PLANNED", state=inspection.state)
    return _end(ctx, inspection, State.CANCELLED, reason)


def terminate(ctx: CommandContext, inspection: Inspection, reason: str) -> Inspection:
    """A started inspection: the data stay, but are not the result (§ 10.6.3). A draft is
    being filled in, so it counts as started."""
    if inspection.state not in FILLING:
        raise errors.business_rule("INSPECTION_NOT_STARTED", state=inspection.state)
    return _end(ctx, inspection, State.TERMINATED, reason)


def invalidate(ctx: CommandContext, inspection: Inspection, reason: str) -> Inspection:
    """A saved inspection is recognised invalid; the record, its history and signatures stay
    (§ 10.1.6, § 10.2.2); its damages become erroneous (§ 10.5.1).
    TODO(spec): whether the odometer reading of the invalid inspection stays in the vehicle's
    history is not stated; it stays, a correction of readings is a separate procedure."""
    if inspection.state != State.SAVED:
        raise errors.business_rule("INSPECTION_NOT_SAVED", state=inspection.state)
    text = _reason(reason)
    inspection.state = State.INVALID
    inspection.invalidated_reason = text
    inspection.invalidated_at = timezone.now()
    inspection.invalidated_by_user_id = ctx.actor.user_id
    inspection.version += 1
    inspection.save(
        update_fields=[
            "state",
            "invalidated_reason",
            "invalidated_at",
            "invalidated_by_user_id",
            "version",
        ]
    )
    ctx.audit(
        "inspection.invalidated",
        object_type="inspection",
        object_id=inspection.id,
        before={"state": State.SAVED},
        after={"state": State.INVALID},
        reason=text,
    )
    damages.recognise_erroneous(ctx, inspection, text)
    return inspection


def delete_draft(ctx: CommandContext, inspection: Inspection) -> None:
    """An unused draft is deleted (§ 10.6.3). The draft of the rental card is used by its rental.
    TODO(О6): a draft sent to the participant for signing is not deleted."""
    if inspection.state != State.DRAFT:
        raise errors.business_rule("INSPECTION_NOT_DRAFT", state=inspection.state)
    if inspection.origin == Inspection.Origin.RENTAL:
        raise errors.business_rule("INSPECTION_DRAFT_USED")
    before = snapshot(inspection)
    InspectionFile.objects.filter(inspection=inspection).delete()
    inspection_id = inspection.id
    inspection.delete()
    ctx.audit(
        "inspection.draft_deleted",
        object_type="inspection",
        object_id=inspection_id,
        before=before,
    )


# --- A required check of a rental (§ 10.3.3, § 10.6.3) -------------------------------------------


def check_conflicts(rental_id: UUID) -> list[str]:
    """Kinds whose required check is not done in the rental — none saved as conducted, or it was
    cancelled, terminated or invalidated — and no reason is recorded (§ 10.3.3, § 10.6.3)."""
    rows = list(
        Inspection.objects.filter(rental_id=rental_id, kind__in=RENTAL_KINDS).values_list(
            "kind", "state", "check_status", "check_required"
        )
    )
    reasons = set(
        InspectionMissingReason.objects.filter(rental_id=rental_id).values_list("kind", flat=True)
    )
    result = []
    for kind in RENTAL_KINDS:
        required = any(r[3] for r in rows if r[0] == kind)
        done = any(
            r[1] == State.SAVED and r[2] == Inspection.CheckStatus.CONDUCTED
            for r in rows
            if r[0] == kind
        )
        if required and not done and kind not in reasons:
            result.append(str(kind))
    return result


def record_missing_reason(
    ctx: CommandContext, company_id: UUID, rental_id: UUID, kind: str, reason: str
) -> InspectionMissingReason:
    """The reason why the required inspection was not done, cancelled, terminated or invalid; it
    lets the rental be closed (§ 10.3.3)."""
    if kind not in RENTAL_KINDS:
        raise errors.validation_error("kind", code="CHOICE_REQUIRED")
    rental = links.require_rental(company_id, rental_id)
    if not rental.issued:
        raise errors.business_rule("RENTAL_NOT_ISSUED")
    if kind not in check_conflicts(rental.id):
        raise errors.business_rule("INSPECTION_NOT_MISSING", field="kind")
    item = InspectionMissingReason.objects.create(
        company_id=company_id,
        rental_id=rental.id,
        kind=kind,
        reason=_reason(reason),
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "inspection.missing_reason_recorded",
        object_type="rental",
        object_id=rental.id,
        after={"kind": kind},
        reason=item.reason,
    )
    return item


def missing_reasons(rental_id: UUID) -> list[dict[str, Any]]:
    return [
        {
            "kind": r.kind,
            "reason": r.reason,
            "created_by_user_id": str(r.created_by_user_id),
            "created_at": r.created_at.isoformat(),
        }
        for r in InspectionMissingReason.objects.filter(rental_id=rental_id).order_by("created_at")
    ]


def closing_conditions(rental_id: UUID) -> list[str]:
    """What keeps the rental from being closed on the side of inspections (§ 10.3.3, § 10.5.5)."""
    result = [f"{kind}_inspection_check_missing" for kind in check_conflicts(rental_id)]
    if damages.closing_blockers(rental_id):
        result.append("damage_estimate_missing")
    return result


# --- Reading ----------------------------------------------------------------------------------


def of_vehicle(company_id: UUID, card_id: UUID) -> list[Inspection]:
    return list(
        Inspection.objects.filter(company_id=company_id, vehicle_card_id=card_id).order_by(
            "-performed_at", "-entered_at"
        )
    )


def _utc(value: datetime | None) -> str | None:
    """Moments are answered in UTC (§ 4.2.2)."""
    return value.astimezone(UTC).isoformat() if value else None


def payload(inspection: Inspection, *, detail: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(inspection.id),
        "vehicle_card_id": str(inspection.vehicle_card_id),
        "kind": inspection.kind,
        "application": inspection.application,
        "rental_id": str(inspection.rental_id) if inspection.rental_id else None,
        "service_operation_id": str(inspection.service_operation_id)
        if inspection.service_operation_id
        else None,
        "origin": inspection.origin,
        "state": inspection.state,
        "check_status": inspection.check_status,
        "check_required": inspection.check_required,
        "performed_at": _utc(inspection.performed_at),
        "planned_at": _utc(inspection.planned_at),
        "started_at": _utc(inspection.started_at),
        "saved_at": _utc(inspection.saved_at),
        "place": {
            "kind": inspection.place_kind,
            "location_id": str(inspection.place_location_id)
            if inspection.place_location_id
            else None,
            "note": inspection.place_note,
            "address": inspection.place_address,
            "lat": str(inspection.place_lat) if inspection.place_lat is not None else None,
            "lng": str(inspection.place_lng) if inspection.place_lng is not None else None,
        },
        "participant": {
            "kind": inspection.participant_kind or None,
            "client_id": str(inspection.participant_client_id)
            if inspection.participant_client_id
            else None,
            "counterparty_id": str(inspection.participant_counterparty_id)
            if inspection.participant_counterparty_id
            else None,
            "employment_id": str(inspection.participant_employment_id)
            if inspection.participant_employment_id
            else None,
            "text": inspection.participant_text,
        },
        "performer_employment_id": str(inspection.performer_employment_id),
        "author_user_id": str(inspection.author_user_id),
        "entered_at": _utc(inspection.entered_at),
        "odometer": str(inspection.odometer_value)
        if inspection.odometer_value is not None
        else None,
        "odometer_unit": inspection.odometer_unit,
        "fuel": str(inspection.fuel_value) if inspection.fuel_value is not None else None,
        "cleanliness": inspection.cleanliness,
        "end_reason": inspection.end_reason,
        "ended_at": _utc(inspection.ended_at),
        "invalidated_reason": inspection.invalidated_reason,
        "invalidated_at": _utc(inspection.invalidated_at),
        "version": inspection.version,
    }
    data["files"] = [
        {"file_id": str(f.file_id), "kind": f.kind, "position": f.position or None}
        for f in InspectionFile.objects.filter(inspection=inspection).order_by("added_at", "id")
    ]
    if detail:
        data["template"] = templates.version_payload(inspection.template_version)
        data["damages_draft"] = inspection.damages_draft
        from c4rsys.inspections.models import Damage

        data["damages"] = [
            damages.payload(d, history=False)
            for d in Damage.objects.filter(inspection=inspection).order_by("created_at")
        ]
    return data
