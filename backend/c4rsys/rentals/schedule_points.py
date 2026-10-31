"""Planned moments of bookings for the conflicts of a changed schedule (ТЗ § 22.1.6).

A booking gives its planned issue in the issue location and, while it is booked or active, its
planned return in the planned return location. PROPOSAL (agent, not confirmed): without a
planned return location the return is expected in the issue location; a place «other» belongs
to its location (decision В1) and is checked by its schedule too.
"""

from datetime import datetime
from uuid import UUID

from django.db.models import Q

from c4rsys.companies.schedules import Point
from c4rsys.rentals.models import Rental


def points(company_id: UUID, location_id: UUID, after: datetime) -> list[Point]:
    result = []
    issues = Rental.objects.filter(
        company_id=company_id,
        state=Rental.State.BOOKED,
        issue_location_id=location_id,
        planned_start__gte=after,
    )
    for r in issues:
        if r.planned_start is None:
            continue
        number = str(r.booking_number or "")
        result.append(Point("rental", r.id, number, "issue", r.planned_start, "rental"))
    returns = Rental.objects.filter(
        Q(planned_return_location_id=location_id)
        | Q(planned_return_location__isnull=True, issue_location_id=location_id),
        company_id=company_id,
        state__in=(Rental.State.BOOKED, Rental.State.ACTIVE),
        planned_end__gte=after,
    )
    for r in returns:
        if r.planned_end is None:
            continue
        number = str(r.booking_number or "")
        result.append(Point("rental", r.id, number, "return", r.planned_end, "rental"))
    return result
