"""What the finances need to know of service operations (ТЗ § 16.6.5, § 17.3.1–17.3.2).

* A charge of a work or a material belongs to the location of the service operation and to its
  vehicle; an operation at a counterparty belongs to the vehicle's location of accounting.
* The expenses of a work — the contractor's cost, the contractor's own materials and the purchase
  cost of consumed stock — are counted when the work gets its result. For an own or managed
  vehicle they belong to the location that operates the vehicle (its location of accounting) and
  keep the service location as the location of the execution; for a client's vehicle they belong
  to the service location. TODO(spec): the location of accounting is taken as it is now, not at
  the time of the work (no history of the attribution is kept yet).
* An internal order of the company creates no income and no debt of the company to itself: its
  sale prices are not charged (§ 16.6.5, § 17.3.2); only these expenses count.
"""

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from c4rsys.finance.attribution import Expense, Place
from c4rsys.service import materials
from c4rsys.service.models import ContractorMaterial, ServiceJob, ServiceMaterial, ServiceOperation


def _income_place(op: ServiceOperation) -> Place:
    location = op.location_id or op.vehicle_card.accounting_location_id
    return Place(location, op.vehicle_card_id)


def jobs(company_id: UUID, ids: list[UUID]) -> dict[UUID, Place]:
    return {
        j.id: _income_place(j.operation)
        for j in ServiceJob.objects.filter(company_id=company_id, pk__in=ids).select_related(
            "operation__vehicle_card"
        )
    }


def service_materials(company_id: UUID, ids: list[UUID]) -> dict[UUID, Place]:
    return {
        m.id: _income_place(m.job.operation)
        for m in ServiceMaterial.objects.filter(company_id=company_id, pk__in=ids).select_related(
            "job__operation__vehicle_card"
        )
    }


def _result_location(op: ServiceOperation) -> UUID | None:
    card = op.vehicle_card
    if card.relation in ("own", "managed") and card.accounting_location_id is not None:
        return card.accounting_location_id
    return op.location_id or card.accounting_location_id


def expenses(company_id: UUID, start: datetime, end: datetime) -> Iterator[Expense]:
    from c4rsys.companies.models import Company

    main = Company.objects.filter(pk=company_id).values_list("main_currency_id", flat=True).first()
    done = list(
        ServiceJob.objects.filter(
            company_id=company_id,
            state=ServiceJob.State.DONE,
            result_at__gte=start,
            result_at__lt=end,
        ).select_related("operation__vehicle_card")
    )
    for job in done:
        op = job.operation

        def expense(
            kind: str, amount: Decimal, currency: str, complete: bool = True, j: ServiceJob = job
        ) -> Expense:
            return Expense(
                kind=kind,
                amount=amount,
                currency=currency,
                at=j.result_at or j.created_at,
                location_id=_result_location(j.operation),
                execution_location_id=j.operation.location_id,
                vehicle_id=j.operation.vehicle_card_id,
                order_id=j.order_id,
                source_id=j.id,
                complete=complete,
            )

        if job.contractor_cost and job.contractor_currency_id:
            yield expense("contractor", job.contractor_cost, job.contractor_currency_id)
        for own in ContractorMaterial.objects.filter(job=job, removed_at__isnull=True):
            if own.cost:
                yield expense("contractor_material", own.cost, own.currency_id)
        used = list(ServiceMaterial.objects.filter(job=job, warehouse_line__isnull=False))
        if main is None or not used or op is None:
            continue
        costs = materials.costs_of(used)
        total = sum((c.cost for c in costs.values()), Decimal(0))
        if total:
            yield expense("material", total, main, all(c.known for c in costs.values()))
