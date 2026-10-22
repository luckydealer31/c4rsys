"""Locations, their activity contexts and schedules (ТЗ гл. 4, § 22.1.5–22.1.6)."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.companies import readiness
from c4rsys.companies.models import ActivityContext, Company, Location, LocationSchedule
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext


class State:
    DRAFT = "draft"
    ACTIVE = "active"


def context_ready(context: ActivityContext) -> bool:
    """A context is ready when it is enabled and its module's conditions are met (§ 4.3.1)."""
    return context.state == ActivityContext.State.ENABLED and readiness.module_conditions_met(
        context.type, context.company_id, context.location_id
    )


def location_state(location: Location, contexts: list[ActivityContext]) -> str:
    """Active with a time zone and at least one ready context, otherwise draft (§ 4.4.3–4.4.4)."""
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
    location = (
        Location.objects.select_for_update().filter(company_id=company_id, pk=location_id).first()
    )
    if location is None:
        raise errors.not_found()
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


def location_exists(company_id: UUID, location_id: UUID) -> bool:
    return Location.objects.filter(company_id=company_id, pk=location_id).exists()


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
