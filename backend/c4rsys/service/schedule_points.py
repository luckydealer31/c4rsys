"""Planned moments of service records for the conflicts of a changed schedule (ТЗ § 22.1.6):
the planned start and end of a planned operation of the location. PROPOSAL (agent, not
confirmed): only operations not yet accepted; an accepted vehicle is already on the spot."""

from datetime import datetime
from uuid import UUID

from c4rsys.companies.schedules import Point
from c4rsys.service.models import ServiceOperation


def points(company_id: UUID, location_id: UUID, after: datetime) -> list[Point]:
    result = []
    for op in ServiceOperation.objects.filter(
        company_id=company_id,
        location_id=location_id,
        state=ServiceOperation.State.PLANNED,
        planned_end__gte=after,
    ):
        if op.planned_start is None or op.planned_end is None:
            continue
        number = str(op.number)
        if op.planned_start >= after:
            result.append(
                Point("service_operation", op.id, number, "start", op.planned_start, "service")
            )
        result.append(Point("service_operation", op.id, number, "end", op.planned_end, "service"))
    return result
