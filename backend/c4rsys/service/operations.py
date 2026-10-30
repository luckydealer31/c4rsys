"""Service operations and their works (ТЗ § 16.1.6–16.1.7, § 16.2–16.4, § 16.7.2; customer's
decisions С-3…С-7 of 29.10.2026).

* An operation is created in an order — an existing one or a new one of the client or of the
  company itself — for one vehicle and a reason; the works may be added at once or after the
  diagnostics (§ 16.1.6). It is planned with or without a post; a post takes no overlapping
  records (§ 16.2.5), the general zone has no limit (§ 16.3.4).
* The vehicle is accepted into the general zone or onto a post; that does not start the works
  (§ 16.2.8). Each work starts separately by its executor (§ 16.4.1); one executor per work.
* Work states: planned, in progress, done, cancelled before the start, terminated after it
  (§ 16.4.7). «Works done» needs a final result of every work (§ 16.7.2); the operation may also
  be cancelled before any start or terminated after one (§ 16.4.8). A final state frees the post
  and the general zone (§ 16.1.4).
* An operation holds the vehicle: by its planned interval, after the acceptance until its final
  state; a booking and an issue over it are refused (decision С-3).
* A work is charged into the order when done — by its price — or terminated — by the price the
  employee gives; a cancelled one is not; a work without a price is charged once it gets one, and
  a client order with such a work does not close (§ 16.1.4; decision С-4). An internal order
  takes no selling price (§ 16.6.5).
* The vehicle is accepted without a required inspection (decision С-5).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies.models import Company, Employment
from c4rsys.delivery import services as deliveries
from c4rsys.finance import charges
from c4rsys.finance.charges import charge_ids_for_source
from c4rsys.finance.models import ChargeEntry
from c4rsys.fleet import moves
from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import VehicleCard
from c4rsys.inspections import damages
from c4rsys.orders import services as orders
from c4rsys.orders.models import Order
from c4rsys.orders.services import OperationSummary
from c4rsys.parties import counterparties
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.service import catalog, materials
from c4rsys.service.models import (
    OPEN_STATES,
    ServiceHandover,
    ServiceJob,
    ServiceOperation,
    ServicePost,
    ServiceWork,
)

Op = ServiceOperation.State
Job = ServiceJob.State
SOURCE = ChargeEntry.SourceType.SERVICE_JOB
FINAL_OPERATION = (Op.WORKS_DONE, Op.CANCELLED, Op.TERMINATED)
FINAL_JOB = (Job.DONE, Job.CANCELLED, Job.TERMINATED)
HOLDING = (Op.ACCEPTED, Op.IN_PROGRESS)  # the vehicle is in the service
# A service opened again after «works done» keeps its moment of finishing: its occupancy of the
# vehicle and the post stays the historical interval up to that moment, and the opening creates
# no new occupancy (§ 16.8.2).
REOPENED = Q(state__in=HOLDING, finished_at__isnull=False)


def _not_future(moment: datetime, field: str) -> None:
    if moment > timezone.now():
        raise errors.business_rule("FUTURE_TIME_NOT_ALLOWED", field=field)


# --- Reading -------------------------------------------------------------------------------------


def operations_of(company_id: UUID) -> QuerySet[ServiceOperation]:
    return ServiceOperation.objects.filter(company_id=company_id)


def get_operation(company_id: UUID, operation_id: UUID) -> ServiceOperation:
    operation = operations_of(company_id).filter(pk=operation_id).first()
    if operation is None:
        raise errors.not_found()
    return operation


def jobs_of(operation: ServiceOperation) -> list[ServiceJob]:
    return list(ServiceJob.objects.filter(operation=operation).order_by("created_at", "id"))


def get_job(company_id: UUID, job_id: UUID) -> ServiceJob:
    job = (
        ServiceJob.objects.select_related("operation")
        .filter(company_id=company_id, pk=job_id)
        .first()
    )
    if job is None:
        raise errors.not_found()
    return job


def post_in_use(post_id: UUID) -> bool:
    return ServiceOperation.objects.filter(post_id=post_id, state__in=OPEN_STATES).exists()


def post_taken_by(post_id: UUID, exclude: UUID | None = None) -> ServiceOperation | None:
    """The operation whose vehicle stands on the post now."""
    query = ServiceOperation.objects.filter(post_id=post_id, state__in=HOLDING).exclude(REOPENED)
    if exclude is not None:
        query = query.exclude(pk=exclude)
    return query.first()


# --- Charges (decision С-4) ----------------------------------------------------------------------


def _charge(ctx: CommandContext, job: ServiceJob, reason: str) -> None:
    client_id = orders.chargeable_client(job.order_id)
    if client_id is None:
        return
    if job.state in (Job.DONE, Job.TERMINATED) and job.price is not None:
        amount = job.price
    elif job.state in (Job.DONE, Job.TERMINATED):
        return
    else:
        amount = Decimal(0)
    charges.set_charge(
        ctx,
        company_id=job.company_id,
        order_id=job.order_id,
        client_id=client_id,
        source_type=SOURCE,
        source_id=job.id,
        amount=amount,
        currency=job.currency_id,
        reason=reason,
        calculation={**job.snapshot, "price": catalog.amount_text(job.price)},
    )


def on_client_assigned(ctx: CommandContext, company_id: UUID, order_id: UUID) -> None:
    """The order got its client: what was estimated is charged (decision Р25)."""
    for job in ServiceJob.objects.select_for_update().filter(
        company_id=company_id, order_id=order_id
    ):
        _charge(ctx, job, "client_assigned")
    materials.charge_all(ctx, company_id, order_id, "client_assigned")


def estimated_for_order(company_id: UUID, order_id: UUID) -> dict[str, Decimal]:
    """The works of an order without a client or of a preliminary one, not charged."""
    if orders.chargeable_client(order_id) is not None:
        return {}
    result: dict[str, Decimal] = {}
    for job in ServiceJob.objects.filter(
        company_id=company_id, order_id=order_id, price__isnull=False
    ).exclude(state=Job.CANCELLED):
        if job.price is None:
            continue
        result[job.currency_id] = result.get(job.currency_id, Decimal(0)) + job.price
    for currency, value in materials.estimated(company_id, order_id).items():
        result[currency] = result.get(currency, Decimal(0)) + value
    return result


# --- The order sees its operations (§ 12.1.9) ----------------------------------------------------


def blockers(operation: ServiceOperation, internal: bool) -> tuple[str, ...]:
    """Every issued material is consumed or returned (§ 16.7.3); a done or terminated work and
    a consumed material of a client order need a price or an explicit zero (§ 16.1.4)."""
    job_ids = list(ServiceJob.objects.filter(operation=operation).values_list("id", flat=True))
    result = []
    if materials.unsettled(job_ids):
        result.append("service_materials_unsettled")
    # A work opened for editing is closed before the order closes (decision of 30.10.2026).
    if ServiceJob.objects.filter(operation=operation, editing_since__isnull=False).exists():
        result.append("service_job_editing")
    if internal:
        return tuple(result)
    unpriced = ServiceJob.objects.filter(
        operation=operation, state__in=(Job.DONE, Job.TERMINATED), price__isnull=True
    ).exists()
    if unpriced or materials.unpriced(job_ids):
        result.append("service_price_missing")
    if operation.state == Op.WORKS_DONE:
        # The act of completed works by the company's setting (§ 16.7.6; decision Р-9).
        from c4rsys.documents import requirements as document_requirements

        result.extend(
            document_requirements.service_act_blockers(operation.company_id, operation.id)
        )
    return tuple(result)


def operations_for_order(company_id: UUID, order_id: UUID) -> list[OperationSummary]:
    internal = Order.objects.filter(pk=order_id).values_list("is_internal", flat=True).first()
    result = []
    for op in operations_of(company_id).filter(order_id=order_id).order_by("number"):
        result.append(
            OperationSummary(
                type="service_work",
                id=op.id,
                state=op.state,
                location_id=op.location_id,
                finished=op.state in FINAL_OPERATION,
                blockers=blockers(op, bool(internal)),
            )
        )
    return result


def history_for_order(company_id: UUID, order_id: UUID) -> list[tuple[str, str]]:
    objects: list[tuple[str, str]] = []
    for op in operations_of(company_id).filter(order_id=order_id):
        objects.append(("service_operation", str(op.id)))
        for job in ServiceJob.objects.filter(operation=op):
            objects.append(("service_job", str(job.id)))
            objects += [("charge_entry", str(c)) for c in charge_ids_for_source(SOURCE, job.id)]
        objects += materials.history(
            list(ServiceJob.objects.filter(operation=op).values_list("id", flat=True))
        )
    return objects


def on_order_cancel(ctx: CommandContext, company_id: UUID, order_id: UUID, reason: str) -> None:
    """The cancelled order takes its operations not started; a started work refuses (§ 12.2.1)."""
    for op in ServiceOperation.objects.select_for_update().filter(
        company_id=company_id, order_id=order_id
    ):
        if op.state in FINAL_OPERATION:
            continue
        if (
            ServiceJob.objects.filter(operation=op)
            .exclude(state__in=(Job.PLANNED, Job.CANCELLED))
            .exists()
        ):
            raise errors.business_rule("ORDER_HAS_STARTED_OPERATIONS")
        _cancel(ctx, op, reason)


# --- Vehicle occupancy (decision С-3) ------------------------------------------------------------


def occupancy(
    company_id: UUID, card_id: UUID, start: datetime, end: datetime | None
) -> list[dict[str, Any]]:
    """Operations holding the vehicle in [start, end): the planned interval of a planned one; from
    the acceptance up to the final state of an accepted one. A reopened one holds only its
    historical interval up to its first finish (§ 16.8.2)."""
    result = []
    query = ServiceOperation.objects.filter(company_id=company_id, vehicle_card_id=card_id)
    planned = query.filter(state=Op.PLANNED, planned_end__gt=start)
    if end is not None:
        planned = planned.filter(planned_start__lt=end)
    held = query.filter(state__in=HOLDING, accepted_at__isnull=False).exclude(REOPENED)
    finished = query.filter(
        Q(state__in=FINAL_OPERATION) | REOPENED, accepted_at__isnull=False, finished_at__gt=start
    )
    if end is not None:
        held = held.filter(accepted_at__lt=end)
        finished = finished.filter(accepted_at__lt=end)
    for op in (*planned, *held, *finished):
        result.append({"kind": "service", "id": str(op.id), "number": op.number})
    return result


def vehicle_busy(company_id: UUID, card_id: UUID) -> list[str]:
    """A vehicle accepted into the service is not moved until the operation ends."""
    return (
        ["in_service"]
        if ServiceOperation.objects.filter(
            company_id=company_id,
            vehicle_card_id=card_id,
            state__in=HOLDING,
            accepted_at__isnull=False,
        )
        .exclude(REOPENED)
        .exists()
        else []
    )


def started_after(company_id: UUID, card_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    """Service operations that accepted the vehicle at or after the moment (decision Д-11)."""
    return [
        {"kind": "service", "id": str(op.id), "number": op.number}
        for op in ServiceOperation.objects.filter(
            company_id=company_id, vehicle_card_id=card_id, accepted_at__gte=moment
        ).exclude(state=Op.CANCELLED)
    ]


def location_blockers(company_id: UUID, location_id: UUID, context_type: str | None) -> list[str]:
    """Operations not finished keep the location and its service context (§ 4.4.1–4.4.2)."""
    if context_type not in (None, "service"):
        return []
    unfinished = ServiceOperation.objects.filter(
        company_id=company_id, location_id=location_id
    ).exclude(state__in=FINAL_OPERATION)
    return ["service_operations_not_finished"] if unfinished.exists() else []


def company_blockers(company_id: UUID) -> list[str]:
    """§ 3.6.1: service orders not finished block deactivation of the company."""
    unfinished = ServiceOperation.objects.filter(company_id=company_id).exclude(
        state__in=FINAL_OPERATION
    )
    return ["service_operations_not_finished"] if unfinished.exists() else []


# --- Creation (§ 16.1.6, § 16.2.1–16.2.7) --------------------------------------------------------


def _check_vehicle(card: VehicleCard) -> None:
    if card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_ARCHIVED", field="vehicle_card_id")
    if card.storage_context_id is not None:
        # § 16.2.1: a vehicle of a warehouse is first returned to the garage (§ 8.1.5).
        raise errors.business_rule("VEHICLE_IN_WAREHOUSE", field="vehicle_card_id")


def _post(company_id: UUID, location_id: UUID | None, post_id: UUID | None) -> ServicePost | None:
    if post_id is None:
        return None
    if location_id is None:
        raise errors.validation_error("post_id", code="POST_UNKNOWN")
    post = ServicePost.objects.filter(
        company_id=company_id, pk=post_id, location_id=location_id
    ).first()
    if post is None:
        raise errors.validation_error("post_id", code="POST_UNKNOWN")
    if post.state != ServicePost.State.ACTIVE:
        raise errors.business_rule("POST_ARCHIVED", field="post_id")
    return post


@dataclass(frozen=True)
class Plan:
    start: datetime | None
    end: datetime | None
    post_id: UUID | None

    def validate(self) -> None:
        if (self.start is None) != (self.end is None):
            raise errors.validation_error(
                "planned_end" if self.end is None else "planned_start", code="FIELD_REQUIRED"
            )
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise errors.validation_error("planned_end", code="PERIOD_INVALID")


def _save_plan(op: ServiceOperation, plan: Plan, *fields: str) -> None:
    op.planned_start = plan.start
    op.planned_end = plan.end
    op.planned_period = (plan.start, plan.end) if plan.start is not None else None
    op.post_id = plan.post_id
    try:
        with transaction.atomic():
            if fields:
                op.save(update_fields=list(fields))
            else:
                op.save()
    except IntegrityError as exc:
        # § 16.2.5: the post is taken in this interval; another time or post is chosen.
        raise errors.business_rule("POST_NOT_AVAILABLE", field="post_id") from exc


@dataclass(frozen=True)
class NewOperation:
    vehicle_card_id: UUID
    location_id: UUID | None  # empty — only at an external counterparty (§ 16.6.1)
    counterparty_id: UUID | None
    reason: str
    order_id: UUID | None
    client_id: UUID | None
    internal: bool
    plan: Plan
    work_ids: tuple[UUID, ...]


def snapshot(op: ServiceOperation) -> dict[str, Any]:
    return {
        "number": op.number,
        "order": str(op.order_id),
        "vehicle": str(op.vehicle_card_id),
        "location": str(op.location_id) if op.location_id else None,
        "counterparty": str(op.counterparty_id) if op.counterparty_id else None,
        "reason": op.reason,
        "planned_start": op.planned_start,
        "planned_end": op.planned_end,
        "post": str(op.post_id) if op.post_id else None,
        "state": op.state,
    }


def create_operation(ctx: CommandContext, company: Company, data: NewOperation) -> ServiceOperation:
    data.plan.validate()
    card = fleet.lock_card(company.id, data.vehicle_card_id)  # vehicle → operation → order
    _check_vehicle(card)
    if data.location_id is not None:
        catalog.require_service_location(company.id, data.location_id, "location_id")
    elif data.counterparty_id is None:
        raise errors.validation_error("location_id", code="FIELD_REQUIRED")
    if data.counterparty_id is not None:
        counterparties.usable(company.id, data.counterparty_id, "counterparty_id")
    _post(company.id, data.location_id, data.plan.post_id)
    if data.order_id is not None:
        order = orders.lock_open_order(company.id, data.order_id, preliminary_ok=True)
    elif data.internal:
        order = orders.create_internal_order(
            ctx, company_id=company.id, responsible_employment_id=ctx.actor.employment_id
        )
    elif data.client_id is not None:
        order = orders.create_order(
            ctx,
            company_id=company.id,
            client_id=data.client_id,
            responsible_employment_id=ctx.actor.employment_id,
        )
    else:
        raise errors.business_rule("CLIENT_REQUIRED", field="client_id")
    op = ServiceOperation(
        company=company,
        number=next_number(company.id, "service_operation"),
        order=order,
        vehicle_card=card,
        location_id=data.location_id,
        counterparty_id=data.counterparty_id,
        reason=data.reason,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    _save_plan(op, data.plan)
    ctx.audit(
        "service_operation.created",
        object_type="service_operation",
        object_id=op.id,
        after=snapshot(op),
    )
    for work_id in data.work_ids:
        _add_job(ctx, op, work_id)
    return op


def locked_operation(
    company_id: UUID, operation_id: UUID, expected_version: int | None = None
) -> ServiceOperation:
    found = (
        operations_of(company_id).filter(pk=operation_id).values_list("vehicle_card_id", flat=True)
    )
    card_id = found.first()
    if card_id is None:
        raise errors.not_found()
    fleet.lock_card(company_id, card_id)
    op = ServiceOperation.objects.select_for_update().get(pk=operation_id)
    if expected_version is not None and op.version != expected_version:
        raise errors.version_conflict()
    return op


def _bump(op: ServiceOperation, *fields: str) -> None:
    op.version += 1
    op.save(update_fields=[*fields, "version", "updated_at"])


def _require_not_final(op: ServiceOperation) -> None:
    if op.state in FINAL_OPERATION:
        raise errors.business_rule("SERVICE_OPERATION_FINISHED", state=op.state)


def change_plan(ctx: CommandContext, op: ServiceOperation, plan: Plan) -> ServiceOperation:
    """Until the service starts its planned time and post change (§ 16.2.6); after the start
    the actual times are corrected instead."""
    plan.validate()
    if op.state not in (Op.PLANNED, Op.ACCEPTED):
        raise errors.business_rule("SERVICE_OPERATION_STARTED")
    orders.ensure_editable(op.company_id, op.order_id)
    _post(op.company_id, op.location_id, plan.post_id)
    if op.state == Op.ACCEPTED and plan.post_id is not None:
        taken = post_taken_by(plan.post_id, exclude=op.id)
        if taken is not None:
            raise errors.business_rule("POST_OCCUPIED", field="post_id", operation=taken.number)
    before = snapshot(op)
    op.version += 1
    _save_plan(
        op,
        plan,
        "planned_start",
        "planned_end",
        "planned_period",
        "post",
        "version",
        "updated_at",
    )
    after = snapshot(op)
    if before == after:
        raise errors.business_rule("NOTHING_CHANGED")
    ctx.audit(
        "service_operation.plan_changed",
        object_type="service_operation",
        object_id=op.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return op


# --- Acceptance and place (§ 16.2.8, § 16.3) -----------------------------------------------------


def accept(
    ctx: CommandContext, op: ServiceOperation, at: datetime, post_id: UUID | None
) -> ServiceOperation:
    """The vehicle arrives: into the general zone or onto a free post. An own or managed vehicle
    must be bound to the service location — another location needs an internal move first
    (§ 16.2.1, § 16.2.8). The acceptance does not start the works."""
    if op.location_id is None:
        # § 16.6.1: at an external counterparty the vehicle is handed over instead.
        raise errors.business_rule("SERVICE_EXTERNAL")
    if op.state != Op.PLANNED:
        raise errors.business_rule("SERVICE_ALREADY_ACCEPTED", state=op.state)
    orders.ensure_open(op.company_id, op.order_id)
    _not_future(at, "accepted_at")
    # § 16.2.8: the acceptance completes the delivery of the same order that brings the vehicle.
    deliveries.arrive_on_acceptance(
        ctx, op.company_id, op.order_id, op.vehicle_card_id, op.location_id, at
    )
    card = VehicleCard.objects.get(pk=op.vehicle_card_id)
    _check_vehicle(card)
    if (
        card.relation != VehicleCard.Relation.CLIENT
        and card.accounting_location_id != op.location_id
    ):
        raise errors.business_rule(
            "VEHICLE_LOCATION_MISMATCH",
            field="vehicle_card_id",
            vehicle_location=str(card.accounting_location_id),
        )
    # TODO(spec): § 8.5.2 lets a vehicle be serviced during its rental; until that path is
    # detailed a vehicle that is in a rental or moving is not accepted.
    busy = [code for code in moves.busy_reasons(op.company_id, card.id) if code != "in_service"]
    if busy:
        raise errors.business_rule("VEHICLE_BUSY", reasons=busy)
    _post(op.company_id, op.location_id, post_id)
    if post_id is not None:
        taken = post_taken_by(post_id, exclude=op.id)
        if taken is not None:
            raise errors.business_rule("POST_OCCUPIED", field="post_id", operation=taken.number)
    op.state = Op.ACCEPTED
    op.accepted_at = at
    op.version += 1
    _save_plan(
        op,
        Plan(op.planned_start, op.planned_end, post_id),
        "state",
        "accepted_at",
        "post",
        "planned_start",
        "planned_end",
        "planned_period",
        "version",
        "updated_at",
    )
    ctx.audit(
        "service_operation.vehicle_accepted",
        object_type="service_operation",
        object_id=op.id,
        before={"state": Op.PLANNED},
        after={"state": op.state, "accepted_at": at, "post": str(post_id) if post_id else None},
    )
    return op


def place(ctx: CommandContext, op: ServiceOperation, post_id: UUID | None) -> ServiceOperation:
    """Moves the vehicle between a post and the general zone; leaving the post frees it without
    finishing the service (§ 16.3.4–16.3.5)."""
    if op.state not in HOLDING or op.accepted_at is None or op.location_id is None:
        raise errors.business_rule("SERVICE_VEHICLE_NOT_ACCEPTED", state=op.state)
    if op.post_id == post_id:
        raise errors.business_rule("NOTHING_CHANGED")
    _post(op.company_id, op.location_id, post_id)
    if post_id is not None:
        taken = post_taken_by(post_id, exclude=op.id)
        if taken is not None:
            raise errors.business_rule("POST_OCCUPIED", field="post_id", operation=taken.number)
    before = str(op.post_id) if op.post_id else None
    op.version += 1
    _save_plan(
        op,
        Plan(op.planned_start, op.planned_end, post_id),
        "post",
        "planned_start",
        "planned_end",
        "planned_period",
        "version",
        "updated_at",
    )
    ctx.audit(
        "service_operation.placed",
        object_type="service_operation",
        object_id=op.id,
        before={"post": before},
        after={"post": str(post_id) if post_id else None},
    )
    return op


# --- Works of the operation (§ 16.1.3, § 16.4) ---------------------------------------------------


def job_snapshot(job: ServiceJob) -> dict[str, Any]:
    return {
        "operation": str(job.operation_id),
        "work": str(job.work_id),
        "name": job.snapshot.get("name"),
        "price": catalog.amount_text(job.price),
        "currency": job.currency_id,
        "executor": str(job.executor_id) if job.executor_id else None,
        "contractor": str(job.contractor_id) if job.contractor_id else None,
        "contractor_cost": catalog.amount_text(job.contractor_cost),
        "state": job.state,
        "actual_start": job.actual_start,
        "actual_end": job.actual_end,
        "odometer": catalog.amount_text(job.odometer),
    }


def _add_job(
    ctx: CommandContext,
    op: ServiceOperation,
    work_id: UUID,
    executor_id: UUID | None = None,
    contractor_id: UUID | None = None,
) -> ServiceJob:
    """A work of the company or of an external contractor (§ 16.6.2); at an operation only at a
    counterparty its works go to that counterparty."""
    if contractor_id is None and op.location_id is None:
        contractor_id = op.counterparty_id
    if contractor_id is not None:
        if executor_id is not None:
            raise errors.validation_error("executor_id", code="SERVICE_JOB_CONTRACTOR")
        counterparties.usable(op.company_id, contractor_id, "contractor_id")
    work = (
        ServiceWork.objects.select_related("currency")
        .filter(company_id=op.company_id, pk=work_id)
        .first()
    )
    if work is None:
        raise errors.validation_error("work_id", code="SERVICE_WORK_UNKNOWN")
    if work.state != ServiceWork.State.ACTIVE:
        raise errors.business_rule("SERVICE_WORK_ARCHIVED", field="work_id")
    terms = catalog.terms_for(work, op.location_id)
    job = ServiceJob.objects.create(
        company_id=op.company_id,
        operation=op,
        order_id=op.order_id,
        work=work,
        snapshot=terms.snapshot,
        price=terms.price,
        currency=work.currency,
        executor_id=_employee(op.company_id, executor_id),
        contractor_id=contractor_id,
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "service_job.added",
        object_type="service_job",
        object_id=job.id,
        after=job_snapshot(job),
    )
    from c4rsys.orders import approvals

    approvals.on_added(ctx, op.order_id, "service_job", job.id, str(terms.snapshot.get("name", "")))
    return job


def add_job(
    ctx: CommandContext,
    op: ServiceOperation,
    work_id: UUID,
    executor_id: UUID | None,
    contractor_id: UUID | None = None,
) -> ServiceJob:
    """A work is added at once or after the diagnostics (§ 16.1.6) while the operation is not
    finished."""
    _require_not_final(op)
    orders.ensure_editable(op.company_id, op.order_id)
    job = _add_job(ctx, op, work_id, executor_id, contractor_id)
    _bump(op)
    return job


def _employee(company_id: UUID, employment_id: UUID | None) -> UUID | None:
    if employment_id is None:
        return None
    found = Employment.objects.filter(
        company_id=company_id, pk=employment_id, state=Employment.State.ACTIVE
    ).exists()
    if not found:
        raise errors.validation_error("executor_id", code="EMPLOYEE_NOT_ACTIVE")
    return employment_id


def locked_job(company_id: UUID, job_id: UUID, expected_version: int) -> ServiceJob:
    job = get_job(company_id, job_id)
    op = locked_operation(company_id, job.operation_id)
    job = ServiceJob.objects.select_for_update().get(pk=job_id)
    job.operation = op
    if job.version != expected_version:
        raise errors.version_conflict()
    return job


def _save_job(ctx: CommandContext, job: ServiceJob, action: str, before: dict[str, Any]) -> None:
    job.version += 1
    job.save()
    after = job_snapshot(job)
    ctx.audit(
        action,
        object_type="service_job",
        object_id=job.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    _bump(job.operation)


def assign(ctx: CommandContext, job: ServiceJob, executor_id: UUID | None) -> ServiceJob:
    """One main executor per work, appointed or changed later (§ 16.4.1)."""
    if job.state not in (Job.PLANNED, Job.IN_PROGRESS):
        raise errors.business_rule("SERVICE_JOB_FINISHED", state=job.state)
    if job.contractor_id is not None:
        raise errors.business_rule("SERVICE_JOB_CONTRACTOR")
    if job.executor_id == executor_id:
        raise errors.business_rule("NOTHING_CHANGED")
    before = job_snapshot(job)
    job.executor_id = _employee(job.company_id, executor_id)
    _save_job(ctx, job, "service_job.assigned", before)
    return job


def take(ctx: CommandContext, job: ServiceJob) -> ServiceJob:
    """A mechanic takes a free work and becomes its executor (§ 23.2.4; decision С-7); a work of
    another executor is not taken without reassignment."""
    if job.state != Job.PLANNED:
        raise errors.business_rule("SERVICE_JOB_FINISHED", state=job.state)
    if job.executor_id is not None:
        raise errors.business_rule("SERVICE_JOB_ASSIGNED")
    if job.contractor_id is not None:
        raise errors.business_rule("SERVICE_JOB_CONTRACTOR")
    if ctx.actor.employment_id is None:
        raise errors.permission_denied("service.work")
    before = job_snapshot(job)
    job.executor_id = ctx.actor.employment_id
    _save_job(ctx, job, "service_job.taken", before)
    return job


def start(ctx: CommandContext, job: ServiceJob, at: datetime) -> ServiceJob:
    """A work of the company starts when the vehicle is accepted (§ 16.2.8); without an executor
    the one who starts it becomes the executor. A contractor's work starts without that: the
    vehicle or the part is with the contractor (§ 16.6.1). The start may be entered afterwards
    (§ 16.4.6)."""
    op = job.operation
    if job.state != Job.PLANNED:
        raise errors.business_rule("SERVICE_JOB_ALREADY_STARTED", state=job.state)
    _require_not_final(op)
    if job.contractor_id is None and op.state not in HOLDING:
        raise errors.business_rule("SERVICE_VEHICLE_NOT_ACCEPTED", state=op.state)
    orders.ensure_open(op.company_id, op.order_id)
    from c4rsys.orders import approvals

    approvals.ensure_startable(job.company_id, "service_job", job.id)
    # TODO(spec): a work started in a reopened service (§ 16.8.2) does not take the vehicle or
    # the post again; whether its start must lie inside the historical interval is not defined.
    _not_future(at, "actual_start")
    if op.accepted_at is not None and at < op.accepted_at:
        raise errors.validation_error("actual_start", code="START_BEFORE_ACCEPTANCE")
    before = job_snapshot(job)
    if job.executor_id is None and job.contractor_id is None:
        job.executor_id = ctx.actor.employment_id
    job.state = Job.IN_PROGRESS
    job.actual_start = at
    _save_job(ctx, job, "service_job.started", before)
    if op.state in (Op.PLANNED, Op.ACCEPTED):
        op.state = Op.IN_PROGRESS
        op.save(update_fields=["state", "updated_at"])
    return job


def _record_result(ctx: CommandContext, job: ServiceJob, at: datetime, comment: str) -> None:
    if job.state != Job.IN_PROGRESS:
        raise errors.business_rule("SERVICE_JOB_NOT_STARTED", state=job.state)
    orders.ensure_open(job.company_id, job.order_id)
    _not_future(at, "actual_end")
    if job.actual_start is not None and at < job.actual_start:
        raise errors.validation_error("actual_end", code="PERIOD_INVALID")
    job.actual_end = at
    job.comment = comment
    job.result_by_user_id = ctx.actor.user_id
    job.result_at = timezone.now()


def complete(
    ctx: CommandContext,
    job: ServiceJob,
    at: datetime,
    odometer: Decimal | None,
    comment: str,
    contractor_cost: Decimal | None = None,
    fixed_damages: list[damages.Fix] | None = None,
) -> ServiceJob:
    """«Done» with the actual time and odometer; the author is kept apart from the executor
    (§ 16.4.2–16.4.3). The odometer reading goes to the vehicle (§ 8.2.12). A contractor's work
    is done when an employee of the company accepts it; the contractor's word is not the
    acceptance, and the odometer may be unknown (§ 16.6.8). The damages fixed by the work are
    chosen explicitly; finishing it fixes nothing by itself (§ 16.5.4, § 10.5.1)."""
    if odometer is None and job.contractor_id is None:
        raise errors.validation_error("odometer", code="FIELD_REQUIRED")
    before = job_snapshot(job)
    _record_result(ctx, job, at, comment)
    if odometer is not None:
        card = VehicleCard.objects.get(pk=job.operation.vehicle_card_id)
        fleet.record_reading(
            ctx, card, measured_at=at, value=odometer, source_type="service_job", source_id=job.id
        )
    _contractor_cost(job, contractor_cost)
    job.odometer = odometer
    job.state = Job.DONE
    _save_job(ctx, job, "service_job.completed", before)
    _charge(ctx, job, "service_job_done")
    materials.consume_for_done_job(ctx, job)
    if fixed_damages:
        damages.fix_by_job(
            ctx,
            company_id=job.company_id,
            vehicle_card_id=job.operation.vehicle_card_id,
            job_id=job.id,
            at=at,
            odometer=odometer,
            fixes=fixed_damages,
        )
    return job


def terminate(
    ctx: CommandContext,
    job: ServiceJob,
    at: datetime,
    reason: str,
    price: Decimal | None,
    comment: str = "",
    contractor_cost: Decimal | None = None,
) -> ServiceJob:
    """«Terminated» after the start with a reason; the facts are kept; the price is the one the
    employee gives, maybe zero (§ 16.4.4; decision С-4)."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    before = job_snapshot(job)
    _record_result(ctx, job, at, comment)
    _contractor_cost(job, contractor_cost)
    job.state = Job.TERMINATED
    job.end_reason = reason
    job.price = price
    _save_job(ctx, job, "service_job.terminated", before)
    _charge(ctx, job, "service_job_terminated")
    return job


def cancel(ctx: CommandContext, job: ServiceJob, reason: str) -> ServiceJob:
    """«Cancelled» — not started (§ 16.4.4); it is not charged."""
    if job.state != Job.PLANNED:
        raise errors.business_rule("SERVICE_JOB_ALREADY_STARTED", state=job.state)
    orders.ensure_editable(job.company_id, job.order_id)
    _require_returned([job.id])
    before = job_snapshot(job)
    job.state = Job.CANCELLED
    job.end_reason = reason
    _save_job(ctx, job, "service_job.cancelled", before)
    _charge(ctx, job, "service_job_cancelled")
    _withdraw(ctx, job, reason)
    return job


def _withdraw(ctx: CommandContext, job: ServiceJob, reason: str) -> None:
    """A work cancelled before the client's answer takes its request with it (§ 12.3)."""
    from c4rsys.orders import approvals

    approvals.withdraw(ctx, job.company_id, "service_job", job.id, reason)


def cancel_refused(ctx: CommandContext, company_id: UUID, job_id: UUID, reason: str) -> bool:
    """The client refused a work (§ 12.3.1): a work not started is cancelled. A work with issued
    parts is not cancelled here — the parts go back to the warehouse first (§ 16.7.3) — and the
    refusal stays recorded; the work cannot start after it."""
    job = ServiceJob.objects.filter(company_id=company_id, pk=job_id).first()
    if job is None:
        return False
    op = locked_operation(company_id, job.operation_id)  # vehicle → operation → work
    job = ServiceJob.objects.select_for_update().get(pk=job_id)
    job.operation = op
    if job.state != Job.PLANNED:
        return False
    if materials.unsettled([job.id]):
        ctx.audit(
            "service_job.refusal_not_cancelled",
            object_type="service_job",
            object_id=job.id,
            after={"code": "SERVICE_MATERIALS_ISSUED"},
            reason=reason,
        )
        return False
    cancel(ctx, job, reason)
    return True


def set_price(ctx: CommandContext, job: ServiceJob, price: Decimal) -> ServiceJob:
    """The price of a work in the order is changed by an employee with the right (§ 16.1.3,
    § 16.1.7); an explicit zero differs from no price (§ 16.1.4). A done work is recharged."""
    if job.state == Job.CANCELLED:
        raise errors.business_rule("SERVICE_JOB_FINISHED", state=job.state)
    orders.ensure_editable(job.company_id, job.order_id)
    if job.price == price:
        raise errors.business_rule("NOTHING_CHANGED")
    before = job_snapshot(job)
    job.price = price
    _save_job(ctx, job, "service_job.price_changed", before)
    _charge(ctx, job, "service_job_price_changed")
    return job


def _contractor_cost(job: ServiceJob, cost: Decimal | None) -> None:
    if cost is None:
        return
    if job.contractor_id is None:
        raise errors.validation_error("contractor_cost", code="SERVICE_JOB_NOT_CONTRACTOR")
    job.contractor_cost = cost
    job.contractor_currency_id = job.currency_id


def set_contractor_cost(ctx: CommandContext, job: ServiceJob, cost: Decimal) -> ServiceJob:
    """The cost of the contractor's work for the company, apart from the client's price; owed to
    the contractor once the work is done or terminated (§ 16.6.3)."""
    if job.contractor_id is None:
        raise errors.business_rule("SERVICE_JOB_NOT_CONTRACTOR")
    orders.ensure_editable(job.company_id, job.order_id)
    if job.contractor_cost == cost:
        raise errors.business_rule("NOTHING_CHANGED")
    before = job_snapshot(job)
    _contractor_cost(job, cost)
    _save_job(ctx, job, "service_job.contractor_cost_changed", before)
    return job


@dataclass(frozen=True)
class Obligation:
    counterparty_id: UUID
    currency: str
    amount: Decimal


def contractor_obligations(company_id: UUID, counterparty_id: UUID | None) -> list[Obligation]:
    """What the company owes contractors for works done or terminated, for finance (§ 16.6.3).
    Paying them is a separate money operation."""
    query = ServiceJob.objects.filter(
        company_id=company_id,
        contractor__isnull=False,
        contractor_cost__isnull=False,
        state__in=(Job.DONE, Job.TERMINATED),
    )
    if counterparty_id is not None:
        query = query.filter(contractor_id=counterparty_id)
    return [
        Obligation(j.contractor_id, j.contractor_currency_id or j.currency_id, j.contractor_cost)
        for j in query
        if j.contractor_id is not None and j.contractor_cost is not None
    ]


# --- Handing over to a contractor (§ 16.6.1, § 16.6.6) ------------------------------------------


def hand_over(
    ctx: CommandContext,
    op: ServiceOperation,
    *,
    kind: str,
    description: str,
    counterparty_id: UUID | None,
    at: datetime,
) -> ServiceHandover:
    """The vehicle or a part goes to a contractor; the vehicle keeps its location of accounting
    (§ 16.6.6) and is held by the operation from now on (decision С-3)."""
    _require_not_final(op)
    orders.ensure_open(op.company_id, op.order_id)
    _not_future(at, "handed_at")
    if kind not in ServiceHandover.Kind.values:
        raise errors.validation_error("kind", code="CHOICE_REQUIRED")
    if kind == ServiceHandover.Kind.PART and not description.strip():
        raise errors.validation_error("description", code="FIELD_REQUIRED")
    counterparty_id = counterparty_id or op.counterparty_id
    if counterparty_id is None:
        raise errors.validation_error("counterparty_id", code="FIELD_REQUIRED")
    counterparties.usable(op.company_id, counterparty_id, "counterparty_id")
    if kind == ServiceHandover.Kind.VEHICLE:
        busy = [
            c for c in moves.busy_reasons(op.company_id, op.vehicle_card_id) if c != "in_service"
        ]
        if busy:
            raise errors.business_rule("VEHICLE_BUSY", reasons=busy)
        # From the own service to a contractor the post is left.
        op.post_id = None
        if op.accepted_at is None:
            op.accepted_at = at
        if op.state == Op.PLANNED:
            op.state = Op.ACCEPTED
        op.version += 1
        op.save(update_fields=["accepted_at", "state", "post", "version", "updated_at"])
    try:
        with transaction.atomic():
            handover = ServiceHandover.objects.create(
                company_id=op.company_id,
                operation=op,
                counterparty_id=counterparty_id,
                kind=kind,
                description=description.strip(),
                handed_at=at,
                created_by_user_id=ctx.actor.user_id,
            )
    except IntegrityError as exc:
        raise errors.business_rule("VEHICLE_ALREADY_HANDED_OVER") from exc
    ctx.audit(
        "service_handover.handed_over",
        object_type="service_handover",
        object_id=handover.id,
        after={
            "operation": str(op.id),
            "kind": kind,
            "description": handover.description,
            "counterparty": str(counterparty_id),
            "handed_at": at,
        },
    )
    return handover


def mark_returned(ctx: CommandContext, handover: ServiceHandover, at: datetime) -> ServiceHandover:
    """The actual return of the property, apart from the acceptance of the works (§ 16.6.6)."""
    if handover.returned_at is not None:
        raise errors.business_rule("SERVICE_HANDOVER_RETURNED")
    _not_future(at, "returned_at")
    if at < handover.handed_at:
        raise errors.validation_error("returned_at", code="PERIOD_INVALID")
    handover.returned_at = at
    handover.version += 1
    handover.save(update_fields=["returned_at", "version"])
    ctx.audit(
        "service_handover.returned",
        object_type="service_handover",
        object_id=handover.id,
        before={"returned_at": None},
        after={"returned_at": at},
    )
    return handover


# --- Ending the operation (§ 16.4.8, § 16.7.2) ---------------------------------------------------


def finish(ctx: CommandContext, op: ServiceOperation) -> ServiceOperation:
    """«Works done»: every work has a final result, cancelled and terminated included (§ 16.7.2).
    The post and the general zone are freed (§ 16.1.4); the vehicle's location is not changed
    (§ 16.6.7)."""
    if op.state not in HOLDING:
        raise errors.business_rule("SERVICE_VEHICLE_NOT_ACCEPTED", state=op.state)
    orders.ensure_open(op.company_id, op.order_id)
    jobs = jobs_of(op)
    if not jobs:
        raise errors.business_rule("SERVICE_WITHOUT_JOBS")
    open_jobs = [str(j.id) for j in jobs if j.state not in FINAL_JOB or j.editing_since]
    if open_jobs:
        raise errors.business_rule("SERVICE_JOBS_NOT_FINISHED", jobs=open_jobs)
    before = op.state
    op.state = Op.WORKS_DONE
    # A reopened service is finished again without a new occupancy: the moment of its first
    # finish stays the end of the vehicle's and the post's interval (§ 16.8.2).
    op.finished_at = op.finished_at or timezone.now()
    _bump(op, "state", "finished_at")
    ctx.audit(
        "service_operation.works_done",
        object_type="service_operation",
        object_id=op.id,
        before={"state": before},
        after={"state": op.state, "finished_at": op.finished_at},
    )
    return op


def _require_returned(job_ids: list[UUID]) -> None:
    """Issued parts go back to the warehouse before a work not done is cancelled."""
    if materials.unsettled(job_ids):
        raise errors.business_rule("SERVICE_MATERIALS_ISSUED")


def _cancel(ctx: CommandContext, op: ServiceOperation, reason: str) -> None:
    _require_returned(list(ServiceJob.objects.filter(operation=op).values_list("id", flat=True)))
    for job in ServiceJob.objects.select_for_update().filter(operation=op, state=Job.PLANNED):
        before = job_snapshot(job)
        job.state = Job.CANCELLED
        job.end_reason = reason
        job.version += 1
        job.save()
        ctx.audit(
            "service_job.cancelled",
            object_type="service_job",
            object_id=job.id,
            before={"state": before["state"]},
            after={"state": job.state},
        )
        _charge(ctx, job, "service_job_cancelled")
        _withdraw(ctx, job, reason)
    previous = op.state
    op.state = Op.CANCELLED
    op.end_reason = reason
    op.finished_at = timezone.now()
    _bump(op, "state", "end_reason", "finished_at")
    ctx.audit(
        "service_operation.cancelled",
        object_type="service_operation",
        object_id=op.id,
        before={"state": previous},
        after={"state": op.state},
        reason=reason,
    )


def cancel_operation(ctx: CommandContext, op: ServiceOperation, reason: str) -> ServiceOperation:
    """«Cancelled» — no work was started (§ 16.4.8)."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    if op.state not in (Op.PLANNED, Op.ACCEPTED):
        raise errors.business_rule("SERVICE_OPERATION_STARTED", state=op.state)
    if (
        ServiceJob.objects.filter(operation=op)
        .exclude(state__in=(Job.PLANNED, Job.CANCELLED))
        .exists()
    ):
        raise errors.business_rule("SERVICE_OPERATION_STARTED", state=op.state)
    orders.ensure_editable(op.company_id, op.order_id)
    _cancel(ctx, op, reason)
    return op


def terminate_operation(ctx: CommandContext, op: ServiceOperation, reason: str) -> ServiceOperation:
    """«Terminated» by a separate action after the start without doing all the plan (§ 16.4.8):
    works not started are cancelled, works in progress are terminated now and get their price by
    hand before the order closes (decision С-4)."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    if op.state != Op.IN_PROGRESS:
        raise errors.business_rule("SERVICE_OPERATION_NOT_STARTED", state=op.state)
    orders.ensure_open(op.company_id, op.order_id)
    now = timezone.now()
    for job in ServiceJob.objects.select_for_update().filter(
        operation=op, state__in=(Job.PLANNED, Job.IN_PROGRESS)
    ):
        before = job_snapshot(job)
        if job.state == Job.PLANNED:
            job.state = Job.CANCELLED
        else:
            job.state = Job.TERMINATED
            job.actual_end = max(now, job.actual_start or now)
            job.price = None
            job.result_by_user_id = ctx.actor.user_id
            job.result_at = now
        job.end_reason = reason
        job.version += 1
        job.save()
        after = job_snapshot(job)
        ctx.audit(
            "service_job.cancelled" if job.state == Job.CANCELLED else "service_job.terminated",
            object_type="service_job",
            object_id=job.id,
            before={k: v for k, v in before.items() if v != after[k]},
            after={k: v for k, v in after.items() if v != before[k]},
        )
        _charge(ctx, job, "service_operation_terminated")
        if job.state == Job.CANCELLED:
            _withdraw(ctx, job, reason)
    op.state = Op.TERMINATED
    op.end_reason = reason
    # A reopened service keeps its historical occupancy (§ 16.8.2).
    op.finished_at = op.finished_at or now
    _bump(op, "state", "end_reason", "finished_at")
    ctx.audit(
        "service_operation.terminated",
        object_type="service_operation",
        object_id=op.id,
        before={"state": Op.IN_PROGRESS},
        after={"state": op.state},
        reason=reason,
    )
    return op


def unassigned_jobs(company_id: UUID, location_ids: set[UUID] | None) -> QuerySet[ServiceJob]:
    """The queue of free works for a mechanic (§ 23.2.4; decision С-7)."""
    query = ServiceJob.objects.filter(
        company_id=company_id, state=Job.PLANNED, executor__isnull=True, contractor__isnull=True
    ).filter(~Q(operation__state__in=FINAL_OPERATION))
    if location_ids is not None:
        query = query.filter(operation__location_id__in=location_ids)
    return query


def client_view_for_order(company_id: UUID, order_id: UUID) -> list[dict[str, Any]]:
    """Service operations of an order for the client: the vehicle, the works and the parts with
    their dates (decision Б-1); no executors, posts, contractors or internal comments."""
    result: list[dict[str, Any]] = []
    for op in (
        ServiceOperation.objects.select_related("vehicle_card")
        .filter(company_id=company_id, order_id=order_id)
        .order_by("number")
    ):
        names: dict[str, str] = {}
        works = []
        materials = []
        for job in op.jobs.prefetch_related("materials").order_by("created_at"):
            name = str(job.snapshot.get("name", ""))
            names[str(job.id)] = name
            works.append(
                {
                    "name": name,
                    "state": job.state,
                    "start": job.actual_start.isoformat() if job.actual_start else None,
                    "end": job.actual_end.isoformat() if job.actual_end else None,
                }
            )
            for m in job.materials.all():
                names[str(m.id)] = m.item_name
                materials.append({"name": m.item_name, "quantity": str(m.quantity), "unit": m.unit})
        start = op.accepted_at or op.planned_start
        result.append(
            {
                "type": "service",
                "id": str(op.id),
                "number": op.number,
                "state": op.state,
                "vehicle": op.vehicle_card.name,
                "start": start.isoformat() if start else None,
                "end": op.finished_at.isoformat() if op.finished_at else None,
                "works": works,
                "materials": materials,
                "names": names,
            }
        )
    return result


# --- Corrections (§ 16.8; part С6) ---------------------------------------------------------------


def reopen_operation(ctx: CommandContext, op: ServiceOperation, reason: str) -> ServiceOperation:
    """A service finished by mistake is opened again by a separate right (§ 16.8.2): works may be
    added or corrected and it is finished again. The works, their results and the past occupancy
    of the vehicle and the post stay; no new occupancy is created: the moment of the finish is
    kept as the end of that interval (see REOPENED). A closed order is reopened first
    (§ 12.2.3)."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    if op.state != Op.WORKS_DONE:
        raise errors.business_rule("SERVICE_OPERATION_NOT_FINISHED", state=op.state)
    orders.ensure_open(op.company_id, op.order_id)
    if op.post_id is not None and op.planned_period is not None:
        # The record of the post is open again (§ 16.2.5): its plan must not overlap another
        # open record of the post made after the finish.
        taken = (
            ServiceOperation.objects.filter(
                post_id=op.post_id, state__in=OPEN_STATES, planned_period__overlap=op.planned_period
            )
            .exclude(pk=op.id)
            .first()
        )
        if taken is not None:
            raise errors.business_rule(
                "SERVICE_REOPEN_POST_TAKEN", field="post_id", operation=taken.number
            )
    op.state = Op.IN_PROGRESS
    op.version += 1
    try:
        with transaction.atomic():
            op.save(update_fields=["state", "version", "updated_at"])
    except IntegrityError as exc:
        raise errors.business_rule("SERVICE_REOPEN_POST_TAKEN", field="post_id") from exc
    ctx.audit(
        "service_operation.reopened",
        object_type="service_operation",
        object_id=op.id,
        before={"state": Op.WORKS_DONE},
        after={"state": op.state, "finished_at": op.finished_at},
        reason=reason,
    )
    return op


def open_job(ctx: CommandContext, job: ServiceJob, reason: str) -> ServiceJob:
    """A finished work is opened for editing with a reason (§ 16.8.2; customer's decision of
    30.10.2026). It stays done or terminated: its times, result and charge do not change by the
    opening, and the maintenance keeps counting from it. While it is open, the service is not
    finished and the order is not closed."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    if job.state not in (Job.DONE, Job.TERMINATED):
        raise errors.business_rule("SERVICE_JOB_NOT_FINISHED", state=job.state)
    if job.editing_since is not None:
        raise errors.business_rule("SERVICE_JOB_ALREADY_OPEN")
    orders.ensure_open(job.company_id, job.order_id)
    job.editing_since = timezone.now()
    job.editing_by_user_id = ctx.actor.user_id
    job.version += 1
    job.save(update_fields=["editing_since", "editing_by_user_id", "version", "updated_at"])
    ctx.audit(
        "service_job.opened_for_editing",
        object_type="service_job",
        object_id=job.id,
        after={"state": job.state},
        reason=reason,
    )
    _bump(job.operation)
    return job


def close_job(ctx: CommandContext, job: ServiceJob) -> ServiceJob:
    """The corrections are done: the work is closed for changes again."""
    if job.editing_since is None:
        raise errors.business_rule("SERVICE_JOB_NOT_OPEN")
    opened = job.editing_since
    job.editing_since = None
    job.editing_by_user_id = None
    job.version += 1
    job.save(update_fields=["editing_since", "editing_by_user_id", "version", "updated_at"])
    ctx.audit(
        "service_job.closed_after_editing",
        object_type="service_job",
        object_id=job.id,
        before={"editing_since": opened},
    )
    _bump(job.operation)
    return job


def correct_job(
    ctx: CommandContext,
    job: ServiceJob,
    *,
    reason: str,
    actual_start: datetime | None,
    actual_end: datetime | None,
    odometer: Decimal | None,
    comment: str | None,
) -> ServiceJob:
    """Corrects the facts of a work opened for editing; the original facts stay in the history
    (§ 16.8.2). The charge, the materials and the documents are not changed by it (§ 16.8.1).
    An impossible chronology is not saved. A corrected odometer replaces the reading of the
    work, so the next maintenance counts from the corrected facts (§ 9.1.11)."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    if job.editing_since is None:
        raise errors.business_rule("SERVICE_JOB_NOT_OPEN")
    op = job.operation
    orders.ensure_open(op.company_id, op.order_id)
    start = _corrected(actual_start, job.actual_start)
    end = _corrected(actual_end, job.actual_end)
    for moment, field in ((start, "actual_start"), (end, "actual_end")):
        if moment is not None:
            _not_future(moment, field)
    if op.accepted_at is not None and start is not None and job.contractor_id is None:
        if start < op.accepted_at:
            raise errors.validation_error("actual_start", code="START_BEFORE_ACCEPTANCE")
    if start is not None and end is not None and end < start:
        raise errors.validation_error("actual_end", code="PERIOD_INVALID")
    if odometer is not None and job.state != Job.DONE:
        raise errors.validation_error("odometer", code="FIELD_NOT_APPLICABLE")
    before = job_snapshot(job)
    before["comment"] = job.comment
    job.actual_start = start
    job.actual_end = end
    if comment is not None:
        job.comment = comment
    reading_moved = end != before["actual_end"]
    if odometer is not None or (reading_moved and job.odometer is not None):
        value = odometer if odometer is not None else job.odometer
        if value is not None and end is not None:
            card = VehicleCard.objects.get(pk=op.vehicle_card_id)
            fleet.replace_reading(
                ctx,
                card,
                source_type="service_job",
                source_id=job.id,
                measured_at=end,
                value=value,
            )
            job.odometer = value
    job.version += 1
    job.save()
    after = job_snapshot(job)
    after["comment"] = job.comment
    changed = {k for k in after if after[k] != before.get(k)}
    if not changed:
        raise errors.business_rule("NOTHING_CHANGED")
    ctx.audit(
        "service_job.corrected",
        object_type="service_job",
        object_id=job.id,
        before={k: before.get(k) for k in changed},
        after={k: after[k] for k in changed},
        reason=reason,
    )
    _bump(op)
    return job


def _corrected(given: datetime | None, stored: datetime | None) -> datetime | None:
    """The form shows the time to the minute: the same minute keeps the stored moment, so that
    a correction of the comment alone does not move the times or the odometer reading."""
    if given is None:
        return stored
    if stored is not None and given.replace(second=0, microsecond=0) == stored.replace(
        second=0, microsecond=0
    ):
        return stored
    return given


def schedule(
    company_id: UUID, card_ids: list[UUID], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Service operations on the schedule of vehicles (decision В-4): a planned one over its
    plan, an accepted one from the acceptance to the finish (to the planned end or now while it
    lasts)."""
    now = timezone.now()
    result: list[dict[str, Any]] = []
    for op in (
        ServiceOperation.objects.filter(company_id=company_id, vehicle_card_id__in=card_ids)
        .exclude(state=Op.CANCELLED)
        .exclude(finished_at__lte=start)
        .exclude(state=Op.PLANNED, planned_end__lte=start)
    ):
        if op.state == Op.PLANNED:
            begin, finish = op.planned_start, op.planned_end
        else:
            begin = op.accepted_at
            finish = op.finished_at or max(op.planned_end or now, now)
        if begin is None or finish is None or begin >= end or finish <= start:
            continue
        result.append(
            {
                "card_id": str(op.vehicle_card_id),
                "kind": "service",
                "id": str(op.id),
                "start": begin.isoformat(),
                "end": finish.isoformat(),
                "state": op.state,
                "label": f"№ {op.number}",
                "planned": op.state == Op.PLANNED,
            }
        )
    return result


def inspection_link(company_id: UUID, operation_id: UUID) -> Any:
    """What inspections know of a service operation (module inspections, links.ServiceLink)."""
    from c4rsys.inspections.links import ServiceLink

    op = ServiceOperation.objects.filter(company_id=company_id, pk=operation_id).first()
    if op is None:
        return None
    return ServiceLink(
        id=op.id, vehicle_card_id=op.vehicle_card_id, state=op.state, location_id=op.location_id
    )
