"""What the service module gives the vehicle (ТЗ § 8.2.10, § 8.3.15, § 8.7.5, § 9.1.4,
§ 9.1.16): unfinished operations, the values in the odometer unit, the works passed to the owner
and to a buyer, the maintenance rules."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q

from c4rsys.fleet.odometer import Conversion
from c4rsys.service.models import MaintenanceRule, ServiceJob, ServiceMaterial, ServiceOperation

Op = ServiceOperation.State
Job = ServiceJob.State
UNFINISHED = (Op.PLANNED, Op.ACCEPTED, Op.IN_PROGRESS)
ACTIVE = (Op.ACCEPTED, Op.IN_PROGRESS)


def pending(company_id: UUID, card_id: UUID, now: datetime) -> list[dict[str, Any]]:
    """Operations not finished: they stop an archive and the end of the management (§ 8.7.5)."""
    return [
        {
            "kind": "service",
            "id": str(op.id),
            "label": str(op.number),
            "active": op.state in ACTIVE,
            "order_id": str(op.order_id),
        }
        for op in ServiceOperation.objects.filter(
            company_id=company_id, vehicle_card_id=card_id, state__in=UNFINISHED
        )
    ]


def _int(value: Decimal) -> int:
    return max(1, int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def unit_values(
    company_id: UUID, card_id: UUID, conv: Conversion, apply: bool
) -> list[dict[str, Any]]:
    """§ 8.2.10: intervals and thresholds of maintenance and the odometer of done works."""
    changes: list[dict[str, Any]] = []
    rules = MaintenanceRule.objects.filter(company_id=company_id, vehicle_card_id=card_id)
    jobs = ServiceJob.objects.filter(
        company_id=company_id, operation__vehicle_card_id=card_id, odometer__isnull=False
    )
    if apply:
        rules, jobs = rules.select_for_update(), jobs.select_for_update()
    for rule in rules:
        fields: dict[str, Any] = {}
        for name in ("interval_distance", "remind_distance"):
            value = getattr(rule, name)
            if value is not None:
                fields[name] = _int(conv.distance(Decimal(value)))
        for name in ("last_done_odometer", "next_due_odometer"):
            value = getattr(rule, name)
            if value is not None:
                fields[name] = conv.distance(value)
        for name, after in fields.items():
            changes.append(
                {
                    "what": "maintenance_rule",
                    "id": str(rule.id),
                    "field": name,
                    "before": f"{getattr(rule, name)} {conv.from_unit}",
                    "after": f"{after} {conv.to_unit}",
                }
            )
        if apply and fields:
            for name, after in fields.items():
                setattr(rule, name, after)
            rule.save(update_fields=list(fields))
    for job in jobs:
        if job.odometer is None:
            continue
        after = conv.distance(job.odometer)
        changes.append(
            {
                "what": "service_job",
                "id": str(job.id),
                "field": "odometer",
                "before": f"{job.odometer} {conv.from_unit}",
                "after": f"{after} {conv.to_unit}",
            }
        )
        if apply:
            ServiceJob.objects.filter(pk=job.pk).update(odometer=after)
    return changes


def _done(company_id: UUID, card_id: UUID, until: datetime | None) -> list[ServiceJob]:
    query = ServiceJob.objects.select_related("operation").filter(
        company_id=company_id, operation__vehicle_card_id=card_id, state=Job.DONE
    )
    if until is not None:
        query = query.filter(Q(actual_end__lte=until))
    return list(query.order_by("-actual_end"))


def works_for_owner(company_id: UUID, card_id: UUID, until: datetime | None) -> list[Any]:
    """The category «service works and materials» (§ 8.3.15): what, when, at which odometer and
    the materials; no prices, no internal comments."""
    jobs = _done(company_id, card_id, until)
    materials: dict[UUID, list[dict[str, Any]]] = {}
    for m in ServiceMaterial.objects.filter(job_id__in=[j.id for j in jobs]):
        materials.setdefault(m.job_id, []).append(
            {"item": m.item_name, "quantity": str(m.quantity), "unit": m.unit}
        )
    return [
        {
            "at": j.actual_end,
            "work": j.snapshot.get("name", ""),
            "system_work": j.snapshot.get("system_work"),
            "odometer": str(j.odometer) if j.odometer is not None else None,
            "materials": materials.get(j.id, []),
        }
        for j in jobs
    ]


def works_for_buyer(company_id: UUID, card_id: UUID, until: datetime | None) -> list[Any]:
    """§ 9.1.4: the date, the mileage, what was repaired or replaced. Internal documents,
    settlements with the service and expenses are not passed."""
    return works_for_owner(company_id, card_id, until)


def rules_for_buyer(company_id: UUID, card_id: UUID) -> list[dict[str, Any]]:
    """§ 9.1.16: the rules stay with the vehicle; the system ones keep the identity of their
    work, the own ones their name and periodicity."""
    return [
        {
            "name": r.name,
            "system_work": r.system_work_id,
            "interval_distance": r.interval_distance,
            "interval_days": r.interval_days,
            "remind_distance": r.remind_distance,
            "remind_days": r.remind_days,
        }
        for r in MaintenanceRule.objects.filter(
            company_id=company_id, vehicle_card_id=card_id, state=MaintenanceRule.State.ACTIVE
        ).order_by("name")
    ]
