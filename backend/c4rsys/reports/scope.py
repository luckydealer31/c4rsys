"""Period, time zone, filters and the area of a report (ТЗ § 21.1.1–21.1.2, § 21.2.2, § 21.2.7).

* The period is inclusive by dates; the zone is the company's, or the location's for a report of
  one location, unless the user chooses another (§ 21.1.2).
* A block is counted only over the data the employee may see by the right of its section; with
  partial access the area of the calculation is stated, and a block without any access is hidden
  (§ 21.1.1). The rights of reports never widen this.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.http import QueryDict
from django.utils import timezone

from c4rsys.companies import access, locations
from c4rsys.companies.models import Company, Location
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor

MAX_DAYS = 3660


@dataclass(frozen=True)
class Filters:
    start: date
    end: date
    zone: str
    location_id: UUID | None = None
    vehicle_id: UUID | None = None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.zone)

    @property
    def start_at(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=self.tz)

    @property
    def end_at(self) -> datetime:
        """The moment right after the last day (exclusive)."""
        return datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=self.tz)

    def today(self) -> date:
        return timezone.now().astimezone(self.tz).date()

    def day_bounds(self, day: date) -> tuple[datetime, datetime]:
        start = datetime.combine(day, time.min, tzinfo=self.tz)
        return start, start + timedelta(days=1)

    def payload(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "zone": self.zone,
            "location_id": str(self.location_id) if self.location_id else None,
            "vehicle_id": str(self.vehicle_id) if self.vehicle_id else None,
        }


def _uuid(params: QueryDict, key: str) -> UUID | None:
    value = params.get(key)
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _date(params: QueryDict, key: str) -> date | None:
    value = params.get(key)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise errors.validation_error(key, code="DATE_INVALID") from exc


def parse(params: QueryDict, company: Company) -> Filters:
    """By default the current month in the zone of the report."""
    location_id = _uuid(params, "location")
    if location_id is not None and not locations.location_exists(company.id, location_id):
        raise errors.validation_error("location", code="LOCATION_UNKNOWN")
    zone = params.get("tz") or ""
    if not zone and location_id is not None:
        info = locations.location_info(company.id, location_id)
        zone = (info.timezone if info else "") or ""
    zone = zone or company.timezone or "UTC"
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise errors.validation_error("tz") from exc
    today = timezone.now().astimezone(ZoneInfo(zone)).date()
    start = _date(params, "start") or today.replace(day=1)
    end = _date(params, "end") or today
    if end < start or (end - start).days > MAX_DAYS:
        raise errors.validation_error("end", code="PERIOD_INVALID")
    return Filters(start, end, zone, location_id, _uuid(params, "vehicle"))


@dataclass(frozen=True)
class Area:
    """Where a block is counted: the whole company, or the listed locations."""

    whole: bool
    location_ids: frozenset[UUID]

    def includes(self, location_id: UUID | None) -> bool:
        if self.whole:
            return True
        return location_id is not None and location_id in self.location_ids

    def payload(self, names: dict[UUID, str]) -> dict[str, Any]:
        return {
            "whole": self.whole,
            "locations": sorted(names.get(x, "") for x in self.location_ids),
        }


def area(actor: Actor, permission: str, company: Company, filters: Filters) -> Area | None:
    """None — no access at all: the block is hidden (§ 21.1.1)."""
    resolver = access.RightsResolver()
    ids = list(Location.objects.filter(company=company).values_list("id", flat=True))
    allowed = {x for x in ids if resolver.has_permission(actor, permission, location_id=x)}
    whole = bool(resolver.has_permission(actor, permission, whole_company=True))
    if filters.location_id is not None:
        if whole or filters.location_id in allowed:
            return Area(False, frozenset({filters.location_id}))
        return None
    if whole:
        return Area(True, frozenset(ids))
    if not allowed:
        return None
    return Area(False, frozenset(allowed))


def location_names(company: Company) -> dict[UUID, str]:
    return dict(Location.objects.filter(company=company).values_list("id", "name"))
