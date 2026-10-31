"""Schedules of locations and contexts (ТЗ § 4.2.3, § 4.2.4, § 22.1.6; block 16 B).

* A location has a weekly schedule (24/7 by default) and exceptions on given dates; a context
  inherits it until it has its own schedule; «restore inheritance» removes the own one and the
  current schedule of the location applies again (§ 4.2.3–4.2.4).
* The schedule of a direction, not of an employee (§ 4.2.3).
* A change does not rewrite operations already made; the future operations that fall outside
  the new schedule are shown as conflicts (§ 22.1.6). Each module reports its planned moments
  through `register_points`, so this module does not read their tables.

Format. `weekly`: every day of the week (`mon` … `sun`) with a list of intervals
`{"from": "HH:MM", "to": "HH:MM"}` in the local time of the location; `to` may be `24:00`; an
empty list is a closed day. `exceptions`: `[{"date": "YYYY-MM-DD", "intervals": [...]}]`, an
empty list of intervals is a closed date. PROPOSAL (agent, not confirmed): up to 6 intervals a
day, intervals do not overlap and do not cross midnight, up to 400 exceptions.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.utils import timezone

from c4rsys.companies.models import (
    WEEKDAYS,
    ActivityContext,
    ContextSchedule,
    Location,
    LocationSchedule,
    schedule_24_7,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

MAX_INTERVALS = 6
MAX_EXCEPTIONS = 400


# --- Format ------------------------------------------------------------------------------------


def _minutes(value: Any, field: str, *, end: bool) -> int:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise errors.validation_error(field, code="SCHEDULE_TIME_INVALID")
    if end and value == "24:00":
        return 24 * 60
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise errors.validation_error(field, code="SCHEDULE_TIME_INVALID") from exc
    return parsed.hour * 60 + parsed.minute


def _intervals(value: Any, field: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > MAX_INTERVALS:
        raise errors.validation_error(field, code="SCHEDULE_INTERVALS_INVALID")
    spans = []
    for i, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"from", "to"}:
            raise errors.validation_error(f"{field}[{i}]", code="SCHEDULE_INTERVALS_INVALID")
        a = _minutes(item["from"], f"{field}[{i}].from", end=False)
        b = _minutes(item["to"], f"{field}[{i}].to", end=True)
        if b <= a:
            raise errors.validation_error(f"{field}[{i}]", code="SCHEDULE_INTERVAL_EMPTY")
        spans.append((a, b, item["from"], item["to"]))
    spans.sort()
    for left, right in zip(spans, spans[1:], strict=False):
        if right[0] < left[1]:
            raise errors.validation_error(field, code="SCHEDULE_INTERVALS_OVERLAP")
    return [{"from": x[2], "to": x[3]} for x in spans]


def validate_weekly(value: Any) -> dict[str, list[dict[str, str]]]:
    if not isinstance(value, dict) or set(value) != set(WEEKDAYS):
        raise errors.validation_error("weekly", code="SCHEDULE_WEEK_INVALID")
    return {day: _intervals(value[day], f"weekly.{day}") for day in WEEKDAYS}


def validate_exceptions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_EXCEPTIONS:
        raise errors.validation_error("exceptions", code="SCHEDULE_EXCEPTIONS_INVALID")
    result: dict[str, list[dict[str, str]]] = {}
    for i, item in enumerate(value):
        where = f"exceptions[{i}]"
        if not isinstance(item, dict) or set(item) != {"date", "intervals"}:
            raise errors.validation_error(where, code="SCHEDULE_EXCEPTIONS_INVALID")
        try:
            on = date.fromisoformat(str(item["date"]))
        except ValueError as exc:
            raise errors.validation_error(f"{where}.date", code="DATE_INVALID") from exc
        if on.isoformat() in result:
            raise errors.validation_error(f"{where}.date", code="SCHEDULE_DATE_REPEATED")
        result[on.isoformat()] = _intervals(item["intervals"], f"{where}.intervals")
    return [{"date": k, "intervals": v} for k, v in sorted(result.items())]


# --- Reading -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Schedule:
    weekly: dict[str, list[dict[str, str]]]
    exceptions: list[dict[str, Any]]

    def intervals_on(self, day: date) -> list[dict[str, str]]:
        for item in self.exceptions:
            if item["date"] == day.isoformat():
                return list(item["intervals"])
        return list(self.weekly.get(WEEKDAYS[day.weekday()], []))

    def is_open(self, moment: datetime, zone: str) -> bool:
        """The local moment lies in an open interval of its date; the end of an interval counts
        as open (a return at the closing time is inside, PROPOSAL)."""
        local = moment.astimezone(ZoneInfo(zone or "UTC"))
        minute = local.hour * 60 + local.minute + (1 if local.second or local.microsecond else 0)
        for item in self.intervals_on(local.date()):
            a = _minutes(item["from"], "from", end=False)
            b = _minutes(item["to"], "to", end=True)
            if a <= minute <= b:
                return True
        # The end of a day open until 24:00 is the start of the next day.
        if minute == 0:
            before = local.date() - timedelta(days=1)
            return any(x["to"] == "24:00" for x in self.intervals_on(before))
        return False


def location_schedule(location: Location) -> Schedule:
    found = LocationSchedule.objects.filter(location=location).first()
    if found is None:
        return Schedule(schedule_24_7(), [])
    return Schedule(found.weekly, found.exceptions)


def context_schedule(context: ActivityContext) -> tuple[Schedule, str]:
    """The schedule in force for the context and its source: `own` or `location` (§ 4.2.4)."""
    own = ContextSchedule.objects.filter(context=context).first()
    if own is not None:
        return Schedule(own.weekly, own.exceptions), "own"
    return location_schedule(context.location), "location"


def payload_of_location(location: Location) -> dict[str, Any]:
    found = LocationSchedule.objects.filter(location=location).first()
    return {
        "weekly": found.weekly if found else schedule_24_7(),
        "exceptions": found.exceptions if found else [],
        "version": found.version if found else 0,
    }


def payload_of_context(context: ActivityContext) -> dict[str, Any]:
    own = ContextSchedule.objects.filter(context=context).first()
    return {
        "source": "own" if own else "location",
        "version": own.version if own else 0,
        "own": {"weekly": own.weekly, "exceptions": own.exceptions} if own else None,
    }


# --- Conflicts (§ 22.1.6) ----------------------------------------------------------------------


@dataclass(frozen=True)
class Point:
    """A planned moment of a future operation in a location: a planned issue or return of a
    booking, the planned start or end of a service record."""

    object_type: str
    object_id: UUID
    number: str
    role: str  # issue | return | start | end
    at: datetime
    context_type: str


# (company_id, location_id, after) -> the planned moments of the module's future operations.
Points = Callable[[UUID, UUID, datetime], list[Point]]
_points: list[Points] = []


def register_points(provider: Points) -> None:
    _points.append(provider)


def conflicts(
    location: Location,
    context_types: set[str] | None = None,
    exclude: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Future operations of the location whose planned moments fall outside the schedule in
    force for their context. Nothing is changed (§ 22.1.6)."""
    now = timezone.now()
    contexts = {c.type: c for c in ActivityContext.objects.filter(location=location)}
    zone = location.timezone
    result = []
    cache: dict[str, Schedule] = {}
    for provider in _points:
        for p in provider(location.company_id, location.id, now):
            if context_types is not None and p.context_type not in context_types:
                continue
            if p.context_type in exclude:
                continue
            if p.context_type not in cache:
                context = contexts.get(p.context_type)
                cache[p.context_type] = (
                    context_schedule(context)[0]
                    if context is not None
                    else location_schedule(location)
                )
            if cache[p.context_type].is_open(p.at, zone):
                continue
            result.append(
                {
                    "object_type": p.object_type,
                    "object_id": str(p.object_id),
                    "number": p.number,
                    "role": p.role,
                    "at": p.at.isoformat(),
                    "context_type": p.context_type,
                }
            )
    result.sort(key=lambda x: x["at"])
    return result


# --- Commands ----------------------------------------------------------------------------------


def _location(company_id: UUID, location_id: UUID) -> Location:
    location = (
        Location.objects.select_for_update().filter(company_id=company_id, pk=location_id).first()
    )
    if location is None:
        raise errors.not_found()
    if location.deactivated_at is not None:
        raise errors.business_rule("LOCATION_DEACTIVATED")
    return location


def set_location_schedule(
    ctx: CommandContext,
    company_id: UUID,
    location_id: UUID,
    weekly: dict[str, list[dict[str, str]]],
    exceptions: list[dict[str, Any]],
    expected_version: int,
) -> list[dict[str, Any]]:
    """Changes the weekly schedule and the date exceptions of the location; contexts without an
    own schedule follow it (§ 4.2.3). Returns the conflicts of future operations (§ 22.1.6)."""
    location = _location(company_id, location_id)
    found = LocationSchedule.objects.select_for_update().filter(location=location).first()
    if found is None:
        # A location of the first days without the row: it had the default 24/7.
        found = LocationSchedule(location=location, company_id=company_id, version=0)
    if found.version != expected_version:
        raise errors.version_conflict()
    before = {"weekly": found.weekly, "exceptions": found.exceptions}
    if before == {"weekly": weekly, "exceptions": exceptions} and found.pk is not None:
        raise errors.business_rule("NOTHING_CHANGED")
    found.weekly = weekly
    found.exceptions = exceptions
    found.version += 1
    found.save()
    # Contexts with an own schedule do not follow the location (§ 4.2.3).
    owned = frozenset(
        ContextSchedule.objects.filter(context__location=location).values_list(
            "context__type", flat=True
        )
    )
    found_conflicts = conflicts(location, exclude=owned)
    ctx.audit(
        "location.schedule_changed",
        object_type="location",
        object_id=location.id,
        before=before,
        after={"weekly": weekly, "exceptions": exceptions, "conflicts": len(found_conflicts)},
    )
    return found_conflicts


def _context(company_id: UUID, location: Location, context_id: UUID) -> ActivityContext:
    context = (
        ActivityContext.objects.select_for_update()
        .filter(company_id=company_id, location=location, pk=context_id)
        .first()
    )
    if context is None:
        raise errors.not_found()
    return context


def set_context_schedule(
    ctx: CommandContext,
    company_id: UUID,
    location_id: UUID,
    context_id: UUID,
    schedule: tuple[dict[str, list[dict[str, str]]], list[dict[str, Any]]] | None,
    expected_version: int,
) -> list[dict[str, Any]]:
    """An own schedule of the context, or `None` — «restore inheritance» (§ 4.2.4): the current
    schedule of the location applies again. `expected_version` is 0 while the context
    inherits."""
    location = _location(company_id, location_id)
    context = _context(company_id, location, context_id)
    own = ContextSchedule.objects.select_for_update().filter(context=context).first()
    if (own.version if own else 0) != expected_version:
        raise errors.version_conflict()
    before = {"weekly": own.weekly, "exceptions": own.exceptions} if own else None
    if schedule is None:
        if own is None:
            raise errors.business_rule("SCHEDULE_ALREADY_INHERITED")
        own.delete()
        action = "location.context_schedule_inherited"
        after: dict[str, Any] | None = None
    else:
        weekly, exceptions = schedule
        if own is not None and before == {"weekly": weekly, "exceptions": exceptions}:
            raise errors.business_rule("NOTHING_CHANGED")
        own = own or ContextSchedule(context=context, company_id=company_id, version=0)
        own.weekly = weekly
        own.exceptions = exceptions
        own.version += 1
        own.save()
        action = "location.context_schedule_changed"
        after = {"weekly": weekly, "exceptions": exceptions}
    found_conflicts = conflicts(location, {context.type})
    ctx.audit(
        action,
        object_type="activity_context",
        object_id=context.id,
        before=before,
        after={"schedule": after, "conflicts": len(found_conflicts)},
    )
    return found_conflicts
