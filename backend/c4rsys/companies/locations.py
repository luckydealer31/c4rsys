"""Locations, their activity contexts and schedules (ТЗ гл. 4, § 22.1.5–22.1.6)."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, QuerySet, RestrictedError
from django.utils import timezone

from c4rsys.companies import readiness
from c4rsys.companies.models import ActivityContext, Company, Location, LocationSchedule
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext


class State:
    DRAFT = "draft"
    ACTIVE = "active"
    DEACTIVATED = "deactivated"


# (company_id, location_id, context_type or None for the whole location) -> codes of what is not
# finished there. Each module reports its own operations and assets (§ 4.4.1–4.4.2).
Blockers = Callable[[UUID, UUID, str | None], list[str]]
_blockers: list[Blockers] = []


def register_blockers(check: Blockers) -> None:
    _blockers.append(check)


def blockers(company_id: UUID, location_id: UUID, context_type: str | None = None) -> list[str]:
    return [code for check in _blockers for code in check(company_id, location_id, context_type)]


def context_ready(context: ActivityContext) -> bool:
    """A context is ready when it is enabled and its module's conditions are met (§ 4.3.1)."""
    return context.state == ActivityContext.State.ENABLED and readiness.module_conditions_met(
        context.type, context.company_id, context.location_id
    )


def location_state(location: Location, contexts: list[ActivityContext]) -> str:
    """Active with a time zone and at least one ready context, otherwise draft (§ 4.4.3–4.4.4);
    deactivated by an explicit action (§ 4.4.1)."""
    if location.deactivated_at is not None:
        return State.DEACTIVATED
    if location.timezone and any(context_ready(c) for c in contexts):
        return State.ACTIVE
    return State.DRAFT


def locations_of(company_id: UUID) -> QuerySet[Location]:
    return (
        Location.objects.filter(company_id=company_id)
        .select_related("schedule")
        .prefetch_related("contexts")
        .order_by("name", "created_at")
    )


def get_location(company_id: UUID, location_id: UUID) -> Location:
    location = locations_of(company_id).filter(pk=location_id).first()
    if location is None:
        raise errors.not_found()
    return location


def snapshot(location: Location) -> dict[str, Any]:
    return {"name": location.name, "timezone": location.timezone, "address": location.address}


@dataclass(frozen=True)
class NewLocation:
    name: str
    timezone: str | None = None  # None: taken from the company (§ 4.2.1)
    address: str = ""


def create_location(ctx: CommandContext, company: Company, data: NewLocation) -> Location:
    """A location may be saved with only a name and no contexts (§ 4.1.2–4.1.3). The time zone is
    proposed from the company and may be overridden (§ 3.1.6, § 4.2.1). The weekly schedule is
    24/7 by default (§ 22.1.6)."""
    now = timezone.now()
    location = Location.objects.create(
        company=company,
        name=data.name,
        timezone=company.timezone if data.timezone is None else data.timezone,
        address=data.address,
        created_at=now,
    )
    schedule = LocationSchedule.objects.create(location=location, company=company)
    ctx.audit(
        "location.created",
        object_type="location",
        object_id=location.id,
        after={**snapshot(location), "weekly_schedule": schedule.weekly},
    )
    return location


def update_location(
    ctx: CommandContext,
    company_id: UUID,
    location_id: UUID,
    changes: dict[str, str],
    expected_version: int,
) -> Location:
    """Renaming keeps every link: they use the location ID (§ 4.1.4). A new time zone changes only
    the displayed local time of stored moments (§ 4.2.2)."""
    location = (
        Location.objects.select_for_update().filter(company_id=company_id, pk=location_id).first()
    )
    if location is None:
        raise errors.not_found()
    if location.version != expected_version:
        raise errors.version_conflict()
    before = snapshot(location)
    for key, value in changes.items():
        setattr(location, key, value)
    after = snapshot(location)
    location.version += 1
    location.save(update_fields=[*changes, "version", "updated_at"])
    ctx.audit(
        "location.changed",
        object_type="location",
        object_id=location.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return location


def add_context(
    ctx: CommandContext, company_id: UUID, location_id: UUID, context_type: str
) -> ActivityContext:
    """At most one context of each type per location (§ 4.1.7). The context gets its own
    immutable ID (§ 4.1.8) and inherits the location schedule (§ 4.2.3)."""
    location = _lock(company_id, location_id)
    if location.deactivated_at is not None:
        raise errors.business_rule("LOCATION_DEACTIVATED")
    if ActivityContext.objects.filter(location=location, type=context_type).exists():
        raise errors.business_rule("CONTEXT_ALREADY_EXISTS", field="type")
    try:
        with transaction.atomic():
            context = ActivityContext.objects.create(
                company_id=company_id, location=location, type=context_type
            )
    except IntegrityError as exc:
        raise errors.business_rule("CONTEXT_ALREADY_EXISTS", field="type") from exc
    ctx.audit(
        "location.context_added",
        object_type="activity_context",
        object_id=context.id,
        after={"location": str(location.id), "type": context.type, "state": context.state},
    )
    return context


def _lock(company_id: UUID, location_id: UUID) -> Location:
    location = (
        Location.objects.select_for_update().filter(company_id=company_id, pk=location_id).first()
    )
    if location is None:
        raise errors.not_found()
    return location


def deactivate_location(
    ctx: CommandContext, company_id: UUID, location_id: UUID, expected_version: int
) -> Location:
    """§ 4.4.1: unfinished operations and vehicles of the location block deactivation; the
    history and links stay. Rentals block until they are returned and formalized (decision
    23.10.2026); cash accounts do not block."""
    location = _lock(company_id, location_id)
    if location.version != expected_version:
        raise errors.version_conflict()
    if location.deactivated_at is not None:
        raise errors.business_rule("LOCATION_ALREADY_DEACTIVATED")
    found = blockers(company_id, location_id)
    if found:
        raise errors.business_rule("LOCATION_HAS_UNFINISHED", blockers=found)
    location.deactivated_at = timezone.now()
    location.version += 1
    location.save(update_fields=["deactivated_at", "version", "updated_at"])
    ctx.audit(
        "location.deactivated",
        object_type="location",
        object_id=location.id,
        after={"deactivated_at": location.deactivated_at.isoformat()},
    )
    return location


def restore_location(
    ctx: CommandContext, company_id: UUID, location_id: UUID, expected_version: int
) -> Location:
    location = _lock(company_id, location_id)
    if location.version != expected_version:
        raise errors.version_conflict()
    if location.deactivated_at is None:
        raise errors.business_rule("LOCATION_NOT_DEACTIVATED")
    before = location.deactivated_at.isoformat()
    location.deactivated_at = None
    location.version += 1
    location.save(update_fields=["deactivated_at", "version", "updated_at"])
    ctx.audit(
        "location.restored",
        object_type="location",
        object_id=location.id,
        before={"deactivated_at": before},
        after={"deactivated_at": None},
    )
    return location


def delete_location(ctx: CommandContext, company_id: UUID, location_id: UUID) -> None:
    """A location without history may be deleted (decision 23.10.2026): nothing refers to it —
    no operations, vehicles, tariffs or rights. With history it is only
    deactivated (§ 4.4.1). The references themselves decide: the database refuses the delete."""
    location = _lock(company_id, location_id)
    contexts = sorted(location.contexts.values_list("type", flat=True))
    before = {**snapshot(location), "contexts": contexts}
    try:
        with transaction.atomic():
            LocationSchedule.objects.filter(location=location).delete()
            ActivityContext.objects.filter(location=location).delete()
            location.delete()
    except (ProtectedError, RestrictedError, IntegrityError) as exc:
        raise errors.business_rule("LOCATION_HAS_HISTORY") from exc
    ctx.audit("location.deleted", object_type="location", object_id=location_id, before=before)


def set_context_enabled(
    ctx: CommandContext, company_id: UUID, location_id: UUID, context_id: UUID, enabled: bool
) -> ActivityContext:
    """§ 4.4.2: a context is switched off without deletion while it has no unfinished
    operations; it is switched on again by a separate action. Vehicles keep their location."""
    location = _lock(company_id, location_id)
    context = ActivityContext.objects.filter(location=location, pk=context_id).first()
    if context is None:
        raise errors.not_found()
    target = ActivityContext.State.ENABLED if enabled else ActivityContext.State.DISABLED
    if context.state == target:
        raise errors.business_rule("CONTEXT_ALREADY_IN_STATE", state=target)
    if enabled and location.deactivated_at is not None:
        raise errors.business_rule("LOCATION_DEACTIVATED")
    if not enabled:
        found = blockers(company_id, location_id, context.type)
        if found:
            raise errors.business_rule("CONTEXT_HAS_UNFINISHED", blockers=found)
    before = context.state
    context.state = target
    context.save(update_fields=["state"])
    ctx.audit(
        "location.context_enabled" if enabled else "location.context_disabled",
        object_type="activity_context",
        object_id=context.id,
        before={"state": before},
        after={"location": str(location.id), "type": context.type, "state": target},
    )
    return context


# --- Storage (decision of 25.10.2026: only the warehouse of a location) ---------------------


def storage_contexts(company_id: UUID) -> QuerySet[ActivityContext]:
    """Warehouses of the company with their locations (§ 11.1.1): one at most per location."""
    return (
        ActivityContext.objects.select_related("location")
        .filter(company_id=company_id, type=ActivityContext.Type.WAREHOUSE)
        .order_by("location__name")
    )


# --- For other modules -------------------------------------------------------------------------


def context_types_in_company(company_id: UUID) -> set[str]:
    """Types of the enabled contexts that exist in any location of the company."""
    return set(
        ActivityContext.objects.filter(
            company_id=company_id, state=ActivityContext.State.ENABLED
        ).values_list("type", flat=True)
    )


def enabled_context_types(company_id: UUID, location_id: UUID) -> set[str]:
    return set(
        ActivityContext.objects.filter(
            company_id=company_id, location_id=location_id, state=ActivityContext.State.ENABLED
        ).values_list("type", flat=True)
    )


def storage_context(company_id: UUID, context_id: UUID) -> ActivityContext | None:
    """An enabled warehouse context of an active location of the company — the place an own
    vehicle is transferred to (§ 8.1.5, § 8.1.7)."""
    return (
        ActivityContext.objects.select_related("location")
        .filter(
            company_id=company_id,
            pk=context_id,
            type=ActivityContext.Type.WAREHOUSE,
            state=ActivityContext.State.ENABLED,
            location__deactivated_at__isnull=True,
        )
        .first()
    )


def location_exists(company_id: UUID, location_id: UUID) -> bool:
    return Location.objects.filter(company_id=company_id, pk=location_id).exists()


def require_usable(company_id: UUID, location_id: UUID, field: str) -> None:
    """A location that new operations and vehicles may refer to: it exists and is not
    deactivated (§ 4.4.1)."""
    location = Location.objects.filter(company_id=company_id, pk=location_id).first()
    if location is None:
        raise errors.validation_error(field, code="LOCATION_UNKNOWN")
    if location.deactivated_at is not None:
        raise errors.business_rule("LOCATION_DEACTIVATED", field=field)


@dataclass(frozen=True)
class LocationInfo:
    id: UUID
    timezone: str
    active: bool
    rental_context_id: UUID | None


def location_info(company_id: UUID, location_id: UUID) -> LocationInfo | None:
    """What an operation needs to know about a location: its time zone, whether it accepts new
    operations (§ 4.4.3) and its rental context."""
    location = locations_of(company_id).filter(pk=location_id).first()
    if location is None:
        return None
    contexts = list(location.contexts.all())
    rental = next(
        (
            c
            for c in contexts
            if c.type == ActivityContext.Type.RENTAL and c.state == ActivityContext.State.ENABLED
        ),
        None,
    )
    return LocationInfo(
        id=location.id,
        timezone=location.timezone,
        active=location_state(location, contexts) == State.ACTIVE,
        rental_context_id=rental.id if rental else None,
    )
