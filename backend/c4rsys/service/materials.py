"""Parts and materials of service works (ТЗ § 16.5.1–16.5.2, § 16.7.3).

A part from a warehouse is added to a concrete work and through it enters the same order; the
work and its materials are priced apart, without a separate sale of the same part. Assigning is
not using: until the work is done the part may be returned unused at any time. Doing the work
confirms the consumption of what is still issued to it; one consumption and one cost are counted
once. Before the operation's formalities are finished every issued material is consumed or
returned (§ 16.7.3); what is left is shown and keeps the order from closing.

PROPOSAL (OPEN): the issue needs the warehouse right of issue in the warehouse's location; the
mechanic's consumption goes with «done» of their work (§ 23.2.4).

Block 15 (§ 11.7.7, § 11.4.3, § 16.5.3):
* A material may be added to a planned work without its issue: by the company's mode it is
  reserved at once (default) or waits for a manual reserve; a change of the mode touches only new
  additions. The issue uses the reserve; taking the material off, cancelling or terminating the
  work releases it. PROPOSAL: «done» needs every planned material issued or taken off first.
* An issued individual part is installed on the vehicle of the work; «Replaces the installed
  part» makes «done» remove the old part and install the new one at the moment and the odometer
  of the work. A part is removed by a work of the vehicle.
* Materials of an external service from its own stock are kept with their cost, without a
  receipt into the warehouse (ContractorMaterial).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.finance import charges
from c4rsys.finance.charges import charge_ids_for_source
from c4rsys.finance.models import ChargeEntry
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money
from c4rsys.service.models import ContractorMaterial, ServiceJob, ServiceMaterial
from c4rsys.warehouse import parts, work_issues
from c4rsys.warehouse.costs import LineCost
from c4rsys.warehouse.operations import Take

SOURCE = ChargeEntry.SourceType.SERVICE_MATERIAL
Job = ServiceJob.State


@dataclass(frozen=True)
class State:
    material: ServiceMaterial
    returned: Decimal
    consumed: Decimal

    @property
    def issued(self) -> bool:
        return self.material.warehouse_line_id is not None

    @property
    def outstanding(self) -> Decimal:
        if not self.issued:
            return Decimal(0)
        return self.material.quantity - self.returned - self.consumed

    @property
    def state(self) -> str:
        if self.issued:
            return "issued"
        return "removed" if self.material.removed_at else "planned"


def states_of(materials: list[ServiceMaterial]) -> list[State]:
    found = work_issues.states([m.warehouse_line_id for m in materials if m.warehouse_line_id])
    result = []
    for m in materials:
        line = found.get(m.warehouse_line_id) if m.warehouse_line_id else None
        result.append(
            State(m, line.returned if line else Decimal(0), line.consumed if line else Decimal(0))
        )
    return result


def of_jobs(job_ids: list[UUID]) -> list[ServiceMaterial]:
    return list(ServiceMaterial.objects.filter(job_id__in=job_ids).order_by("created_at", "id"))


def amount_of(state: State) -> Decimal | None:
    """The cost of the consumed quantity for the client; unknown while the price is unknown."""
    m = state.material
    if m.price is None:
        return None if state.consumed > 0 else Decimal(0)
    minor = Currency.objects.get(pk=m.currency_id).minor_units
    return round_money(m.price * state.consumed / m.price_per, minor)


def _charge(ctx: CommandContext, material: ServiceMaterial, reason: str) -> None:
    client_id = orders.chargeable_client(material.order_id)
    if client_id is None or material.warehouse_line_id is None:
        return
    state = states_of([material])[0]
    amount = amount_of(state)
    if amount is None:
        return
    charges.set_charge(
        ctx,
        company_id=material.company_id,
        order_id=material.order_id,
        client_id=client_id,
        source_type=SOURCE,
        source_id=material.id,
        amount=amount,
        currency=material.currency_id,
        reason=reason,
        calculation={
            "item": material.item_name,
            "label": material.label,
            "consumed": str(state.consumed),
            "price": str(material.price),
            "price_per": str(material.price_per),
        },
    )


def charge_all(ctx: CommandContext, company_id: UUID, order_id: UUID, reason: str) -> None:
    for m in ServiceMaterial.objects.select_for_update().filter(
        company_id=company_id, order_id=order_id
    ):
        _charge(ctx, m, reason)


def estimated(company_id: UUID, order_id: UUID) -> dict[str, Decimal]:
    """Issued materials of an order not charged: by their price for the issued quantity."""
    return estimated_many(company_id, [order_id])[order_id]


def estimated_many(company_id: UUID, order_ids: list[UUID]) -> dict[UUID, dict[str, Decimal]]:
    """The same for many orders in one reading (audit item 14)."""
    result: dict[UUID, dict[str, Decimal]] = {order_id: {} for order_id in order_ids}
    found = list(
        ServiceMaterial.objects.filter(
            company_id=company_id,
            order_id__in=order_ids,
            price__isnull=False,
            removed_at__isnull=True,
        )
    )
    minor = dict(
        Currency.objects.filter(pk__in={m.currency_id for m in found}).values_list(
            "code", "minor_units"
        )
    )
    for m in found:
        if m.price is None:
            continue
        value = round_money(m.price * m.quantity / m.price_per, minor[m.currency_id])
        sums = result[m.order_id]
        sums[m.currency_id] = sums.get(m.currency_id, Decimal(0)) + value
    return result


def unsettled(job_ids: list[UUID]) -> bool:
    return any(s.outstanding > 0 for s in states_of(of_jobs(job_ids)))


def planned_open(job_ids: list[UUID]) -> bool:
    """Added to a work and neither issued nor taken off yet."""
    return ServiceMaterial.objects.filter(
        job_id__in=job_ids, warehouse_line__isnull=True, removed_at__isnull=True
    ).exists()


def costs_of(materials: list[ServiceMaterial]) -> dict[UUID, LineCost]:
    """The purchase cost of what each material consumed (§ 11.2.9, § 11.4.4)."""
    if not materials:
        return {}
    found = work_issues.line_costs(
        materials[0].company_id, [m.warehouse_line_id for m in materials if m.warehouse_line_id]
    )
    return {m.id: found[m.warehouse_line_id] for m in materials if m.warehouse_line_id in found}


def unpriced(job_ids: list[UUID]) -> bool:
    return any(s.material.price is None and s.consumed > 0 for s in states_of(of_jobs(job_ids)))


def history(job_ids: list[UUID]) -> list[tuple[str, str]]:
    objects: list[tuple[str, str]] = []
    for m in of_jobs(job_ids):
        objects.append(("service_material", str(m.id)))
        objects += [("charge_entry", str(c)) for c in charge_ids_for_source(SOURCE, m.id)]
    return objects


def snapshot(state: State) -> dict[str, Any]:
    m = state.material
    return {
        "state": state.state,
        "reserve": str(m.reserve_id) if m.reserve_id else None,
        "replaces": str(m.replaces_id) if m.replaces_id else None,
        "job": str(m.job_id),
        "item": m.item_name,
        "label": m.label,
        "quantity": str(m.quantity),
        "returned": str(state.returned),
        "consumed": str(state.consumed),
        "price": str(m.price) if m.price is not None else None,
        "price_per": str(m.price_per),
    }


# --- Commands ------------------------------------------------------------------------------------


def add(
    ctx: CommandContext,
    company: Company,
    job: ServiceJob,
    context_id: UUID,
    take: Take,
) -> list[ServiceMaterial]:
    """Issues a part to a work not finished yet; the vehicle accepted or not (§ 16.5.1)."""
    if job.state not in (Job.PLANNED, Job.IN_PROGRESS):
        raise errors.business_rule("SERVICE_JOB_FINISHED", state=job.state)
    orders.ensure_open(job.company_id, job.order_id)
    op = job.operation
    issued = work_issues.issue_to_work(
        ctx,
        company,
        context_id,
        op.vehicle_card_id,
        take,
        f"service {op.number}",
    )
    created = []
    for line in issued:
        material = ServiceMaterial.objects.create(
            company_id=job.company_id,
            job=job,
            order_id=job.order_id,
            warehouse_line_id=line.line_id,
            item_name=line.item_name,
            unit=line.unit,
            label=line.asset_public_id or line.lot_public_id or "",
            quantity=line.quantity,
            price=line.price,
            price_per=line.price_per,
            currency_id=job.currency_id,
            created_by_user_id=ctx.actor.user_id,
        )
        ctx.audit(
            "service_material.added",
            object_type="service_material",
            object_id=material.id,
            after=snapshot(State(material, Decimal(0), Decimal(0))),
        )
        created.append(material)
    return created


def locked(company_id: UUID, material_id: UUID, expected_version: int) -> ServiceMaterial:
    material = (
        ServiceMaterial.objects.select_for_update()
        .select_related("job__operation")
        .filter(company_id=company_id, pk=material_id)
        .first()
    )
    if material is None:
        raise errors.not_found()
    if material.version != expected_version:
        raise errors.version_conflict()
    return material


def _changed(
    ctx: CommandContext, material: ServiceMaterial, action: str, b: dict[str, Any]
) -> None:
    material.version += 1
    material.save()
    a = snapshot(states_of([material])[0])
    ctx.audit(
        action,
        object_type="service_material",
        object_id=material.id,
        before={k: v for k, v in b.items() if v != a[k]},
        after={k: v for k, v in a.items() if v != b[k]},
    )


def _quantity(state: State, quantity: Decimal | None) -> Decimal:
    if state.outstanding <= 0:
        raise errors.business_rule("SERVICE_MATERIAL_SETTLED")
    if quantity is None:
        return state.outstanding
    if quantity <= 0 or quantity > state.outstanding:
        raise errors.business_rule(
            "QUANTITY_EXCEEDS_ISSUED", field="quantity", available=str(state.outstanding)
        )
    return quantity


def return_unused(
    ctx: CommandContext,
    material: ServiceMaterial,
    quantity: Decimal | None,
    target_id: UUID | None = None,
) -> ServiceMaterial:
    """The part goes back to the warehouse unused; no consumption arises (§ 16.5.1). Another
    warehouse may be chosen; the move between them stays in the history (§ 11.4.5)."""
    before = states_of([material])[0]
    amount = _quantity(before, quantity)
    if material.warehouse_line_id is None:
        raise errors.business_rule("SERVICE_MATERIAL_NOT_ISSUED")
    work_issues.return_line(ctx, material.company_id, material.warehouse_line_id, amount, target_id)
    _changed(ctx, material, "service_material.returned", snapshot(before))
    return material


def consume(
    ctx: CommandContext, material: ServiceMaterial, quantity: Decimal | None, reason: str
) -> ServiceMaterial:
    """Confirms the use of an issued quantity and charges it."""
    before = states_of([material])[0]
    amount = _quantity(before, quantity)
    if material.warehouse_line_id is None:
        raise errors.business_rule("SERVICE_MATERIAL_NOT_ISSUED")
    work_issues.consume_line(ctx, material.company_id, material.warehouse_line_id, amount)
    _changed(ctx, material, "service_material.consumed", snapshot(before))
    _charge(ctx, material, reason)
    return material


def consume_for_done_job(ctx: CommandContext, job: ServiceJob) -> None:
    """«Done» confirms the consumption of what is still issued to the work (§ 16.5.1); a part
    marked «Replaces the installed part» is installed and the old one removed (§ 11.4.3)."""
    for state in states_of(list(ServiceMaterial.objects.select_for_update().filter(job=job))):
        if state.outstanding > 0:
            consume(ctx, state.material, None, "service_job_done")
        m = state.material
        if (
            m.replaces_id is not None
            and m.warehouse_line_id is not None
            and job.actual_end
            and not parts.installed_line_ids([m.warehouse_line_id])
        ):
            parts.install(
                ctx,
                m.company_id,
                line_id=m.warehouse_line_id,
                vehicle_id=job.operation.vehicle_card_id,
                job_id=job.id,
                installed_at=job.actual_end,
                odometer=job.odometer,
                replaces_id=m.replaces_id,
            )


def set_price(ctx: CommandContext, material: ServiceMaterial, price: Decimal) -> ServiceMaterial:
    """The price for the client is set or changed in the order (§ 16.1.3, § 16.1.4)."""
    orders.ensure_editable(material.company_id, material.order_id)
    if material.price == price:
        raise errors.business_rule("NOTHING_CHANGED")
    before = snapshot(states_of([material])[0])
    material.price = price
    _changed(ctx, material, "service_material.price_changed", before)
    _charge(ctx, material, "service_material_price_changed")
    return material


# --- Planned materials and reserves (§ 11.7.7) ---------------------------------------------------


def _open_job(job: ServiceJob) -> None:
    if job.state not in (Job.PLANNED, Job.IN_PROGRESS):
        raise errors.business_rule("SERVICE_JOB_FINISHED", state=job.state)
    orders.ensure_open(job.company_id, job.order_id)


def _replaces(company_id: UUID, job: ServiceJob, installation_id: UUID | None) -> UUID | None:
    if installation_id is None:
        return None
    found = [
        i.id
        for i in parts.open_on_vehicle(company_id, job.operation.vehicle_card_id)
        if i.id == installation_id
    ]
    if not found:
        raise errors.validation_error("replaces_id", code="PART_NOT_INSTALLED")
    return installation_id


def plan(
    ctx: CommandContext,
    company: Company,
    job: ServiceJob,
    context_id: UUID,
    take: Take,
    replaces_id: UUID | None = None,
) -> list[ServiceMaterial]:
    """Adds a material to the work without issuing it: reserved at once in the automatic mode,
    or waiting for a manual reserve (§ 11.7.7). Only the available quantity (§ 11.8.3)."""
    _open_job(job)
    replaces = _replaces(company.id, job, replaces_id)
    automatic = work_issues.work_reserve_mode(company.id) == "auto"
    created = []
    for planned in work_issues.plan_for_work(
        ctx, company, context_id, job.id, take, reserve=automatic
    ):
        material = ServiceMaterial.objects.create(
            company_id=job.company_id,
            job=job,
            order_id=job.order_id,
            planned=planned.stored(),
            reserve_id=planned.reserve_id,
            item_name=planned.item_name,
            unit=planned.unit,
            label=planned.label,
            quantity=planned.quantity,
            price=planned.price,
            price_per=planned.price_per,
            currency_id=job.currency_id,
            replaces_id=replaces if planned.take.asset_ids else None,
            created_by_user_id=ctx.actor.user_id,
        )
        ctx.audit(
            "service_material.planned",
            object_type="service_material",
            object_id=material.id,
            after=snapshot(State(material, Decimal(0), Decimal(0))),
        )
        created.append(material)
    return created


def _planned(material: ServiceMaterial) -> None:
    if material.warehouse_line_id is not None or material.removed_at is not None:
        raise errors.business_rule("SERVICE_MATERIAL_NOT_PLANNED")


def reserve(ctx: CommandContext, company: Company, material: ServiceMaterial) -> ServiceMaterial:
    """The manual reserve of a planned material (§ 11.7.7)."""
    _planned(material)
    _open_job(material.job)
    if (
        material.reserve_id is not None
        and work_issues.reserve_state(material.reserve_id) == "active"
    ):
        raise errors.business_rule("SERVICE_MATERIAL_RESERVED")
    before = snapshot(states_of([material])[0])
    context_id, take = work_issues.take_of(material.planned)
    material.reserve = work_issues.reserve_for_work(ctx, company, context_id, material.job_id, take)
    _changed(ctx, material, "service_material.reserved", before)
    return material


def issue(ctx: CommandContext, company: Company, material: ServiceMaterial) -> ServiceMaterial:
    """The planned material is issued to the work; its reserve is used (§ 11.7.1)."""
    _planned(material)
    job = material.job
    _open_job(job)
    before = snapshot(states_of([material])[0])
    op = job.operation
    line = work_issues.issue_planned(
        ctx,
        company,
        material.planned,
        material.reserve_id,
        op.vehicle_card_id,
        f"service {op.number}",
    )
    material.warehouse_line_id = line.line_id
    _changed(ctx, material, "service_material.issued", before)
    return material


def take_off(ctx: CommandContext, material: ServiceMaterial, reason: str) -> ServiceMaterial:
    """A planned material is taken off the work; its reserve is released without consumption."""
    _planned(material)
    before = snapshot(states_of([material])[0])
    if material.reserve_id is not None:
        work_issues.release(ctx, material.company_id, material.reserve_id, reason)
    material.removed_at = timezone.now()
    _changed(ctx, material, "service_material.taken_off", before)
    return material


def release_planned(ctx: CommandContext, job: ServiceJob, reason: str) -> None:
    """A cancelled or terminated work releases the reserves of its planned materials."""
    for m in ServiceMaterial.objects.select_for_update().filter(
        job=job, warehouse_line__isnull=True, removed_at__isnull=True
    ):
        take_off(ctx, m, reason)


def require_issued(job: ServiceJob) -> None:
    """PROPOSAL: a work is done only after its planned materials are issued or taken off."""
    if planned_open([job.id]):
        raise errors.business_rule("SERVICE_MATERIALS_NOT_ISSUED")


def set_replaces(
    ctx: CommandContext, material: ServiceMaterial, installation_id: UUID | None
) -> ServiceMaterial:
    """«Replaces the installed part» (§ 11.4.3) for an individual part of a work not done."""
    _open_job(material.job)
    if material.removed_at is not None or material.unit != "piece":
        raise errors.business_rule("PART_INDIVIDUAL_ONLY")
    if material.warehouse_line_id and parts.installed_line_ids([material.warehouse_line_id]):
        raise errors.business_rule("PART_ALREADY_INSTALLED")
    before = snapshot(states_of([material])[0])
    material.replaces_id = _replaces(material.company_id, material.job, installation_id)
    _changed(ctx, material, "service_material.replaces_set", before)
    return material


# --- Installation and removal (§ 11.4.3) ---------------------------------------------------------


def install(
    ctx: CommandContext,
    material: ServiceMaterial,
    at: datetime,
    odometer: Decimal | None,
    replaces_id: UUID | None,
) -> ServiceMaterial:
    """An issued part of the work is installed on the vehicle of the work; what is still issued
    is consumed and charged."""
    if material.warehouse_line_id is None:
        raise errors.business_rule("SERVICE_MATERIAL_NOT_ISSUED")
    job = material.job
    if job.state in (Job.CANCELLED,):
        raise errors.business_rule("SERVICE_JOB_FINISHED", state=job.state)
    before = snapshot(states_of([material])[0])
    chosen = replaces_id if replaces_id is not None else material.replaces_id
    parts.install(
        ctx,
        material.company_id,
        line_id=material.warehouse_line_id,
        vehicle_id=job.operation.vehicle_card_id,
        job_id=job.id,
        installed_at=at,
        odometer=odometer,
        replaces_id=_replaces(material.company_id, job, chosen),
    )
    material.replaces_id = chosen
    _changed(ctx, material, "service_material.installed", before)
    _charge(ctx, material, "service_material_consumed")
    return material


def remove_part(
    ctx: CommandContext,
    job: ServiceJob,
    installation_id: UUID,
    at: datetime,
    odometer: Decimal | None,
    comment: str,
) -> None:
    """A part installed on the vehicle is removed by this work (§ 11.4.3)."""
    if job.state == Job.CANCELLED:
        raise errors.business_rule("SERVICE_JOB_FINISHED", state=job.state)
    parts.remove(
        ctx,
        job.company_id,
        installation_id,
        vehicle_id=job.operation.vehicle_card_id,
        job_id=job.id,
        removed_at=at,
        odometer=odometer,
        comment=comment,
    )


# --- Materials of an external service (§ 16.5.3) -------------------------------------------------


def own_snapshot(m: ContractorMaterial) -> dict[str, Any]:
    return {
        "job": str(m.job_id),
        "name": m.name,
        "quantity": str(m.quantity),
        "unit": m.unit,
        "cost": str(m.cost) if m.cost is not None else None,
        "currency": m.currency_id,
        "comment": m.comment,
        "removed": m.removed_at is not None,
    }


def add_own(
    ctx: CommandContext,
    job: ServiceJob,
    *,
    name: str,
    quantity: Decimal,
    unit: str,
    cost: Decimal | None,
    currency: str,
    comment: str,
) -> ContractorMaterial:
    """PROPOSAL: for a work of a contractor or a service at a counterparty; the cost is the
    company's cost, kept for the costs of the vehicle; it is neither charged to the client nor a
    separate debt — the contractor's cost of the work stays where it is (§ 16.6.3)."""
    if job.contractor_id is None and job.operation.counterparty_id is None:
        raise errors.business_rule("SERVICE_NOT_EXTERNAL")
    if job.state == Job.CANCELLED:
        raise errors.business_rule("SERVICE_JOB_FINISHED", state=job.state)
    if not Currency.objects.filter(pk=currency).exists():
        raise errors.validation_error("currency")
    material = ContractorMaterial.objects.create(
        company_id=job.company_id,
        job=job,
        name=name,
        quantity=quantity,
        unit=unit,
        cost=cost,
        currency_id=currency,
        comment=comment,
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "service_contractor_material.added",
        object_type="service_contractor_material",
        object_id=material.id,
        after=own_snapshot(material),
    )
    return material


def remove_own(
    ctx: CommandContext, company_id: UUID, material_id: UUID, expected: int, reason: str
) -> ContractorMaterial:
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    material = (
        ContractorMaterial.objects.select_for_update()
        .filter(company_id=company_id, pk=material_id)
        .first()
    )
    if material is None:
        raise errors.not_found()
    if material.version != expected:
        raise errors.version_conflict()
    if material.removed_at is not None:
        raise errors.business_rule("SERVICE_MATERIAL_REMOVED")
    material.removed_at = timezone.now()
    material.removed_by_user_id = ctx.actor.user_id
    material.remove_reason = reason
    material.version += 1
    material.save()
    ctx.audit(
        "service_contractor_material.removed",
        object_type="service_contractor_material",
        object_id=material.id,
        before={"removed": False},
        after={"removed": True},
        reason=reason,
    )
    return material


def own_of_jobs(job_ids: list[UUID]) -> list[ContractorMaterial]:
    return list(ContractorMaterial.objects.filter(job_id__in=job_ids).order_by("created_at", "id"))


def line_guard(line_ids: list[UUID]) -> set[UUID]:
    """A warehouse line of a material of a work is corrected in the work (§ 11.6.2)."""
    return set(
        ServiceMaterial.objects.filter(warehouse_line_id__in=line_ids).values_list(
            "warehouse_line_id", flat=True
        )
    )
