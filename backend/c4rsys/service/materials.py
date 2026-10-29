"""Parts and materials of service works (ТЗ § 16.5.1–16.5.2, § 16.7.3).

A part from a warehouse is added to a concrete work and through it enters the same order; the
work and its materials are priced apart, without a separate sale of the same part. Assigning is
not using: until the work is done the part may be returned unused at any time. Doing the work
confirms the consumption of what is still issued to it; one consumption and one cost are counted
once. Before the operation's formalities are finished every issued material is consumed or
returned (§ 16.7.3); what is left is shown and keeps the order from closing.

PROPOSAL (OPEN): the issue needs the warehouse right of issue in the warehouse's location; the
mechanic's consumption goes with «done» of their work (§ 23.2.4).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from c4rsys.companies.models import Company
from c4rsys.finance import charges
from c4rsys.finance.charges import charge_ids_for_source
from c4rsys.finance.models import ChargeEntry
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money
from c4rsys.service.models import ServiceJob, ServiceMaterial
from c4rsys.warehouse import work_issues
from c4rsys.warehouse.operations import Take

SOURCE = ChargeEntry.SourceType.SERVICE_MATERIAL
Job = ServiceJob.State


@dataclass(frozen=True)
class State:
    material: ServiceMaterial
    returned: Decimal
    consumed: Decimal

    @property
    def outstanding(self) -> Decimal:
        return self.material.quantity - self.returned - self.consumed


def states_of(materials: list[ServiceMaterial]) -> list[State]:
    found = work_issues.states([m.warehouse_line_id for m in materials])
    result = []
    for m in materials:
        line = found.get(m.warehouse_line_id)
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
    if client_id is None:
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
    result: dict[str, Decimal] = {}
    for m in ServiceMaterial.objects.filter(
        company_id=company_id, order_id=order_id, price__isnull=False
    ):
        if m.price is None:
            continue
        minor = Currency.objects.get(pk=m.currency_id).minor_units
        value = round_money(m.price * m.quantity / m.price_per, minor)
        result[m.currency_id] = result.get(m.currency_id, Decimal(0)) + value
    return result


def unsettled(job_ids: list[UUID]) -> bool:
    return any(s.outstanding > 0 for s in states_of(of_jobs(job_ids)))


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
        "job": str(m.job_id),
        "item": m.item_name,
        "label": m.label,
        "quantity": str(m.quantity),
        "returned": str(state.returned),
        "consumed": str(state.consumed),
        "price": str(m.price) if m.price is not None else None,
        "price_per": str(m.price_per),
    }


# --- Commands -------------------------------------------------------------------------------------


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
    ctx: CommandContext, material: ServiceMaterial, quantity: Decimal | None
) -> ServiceMaterial:
    """The part goes back to the warehouse unused; no consumption arises (§ 16.5.1)."""
    before = states_of([material])[0]
    amount = _quantity(before, quantity)
    work_issues.return_line(ctx, material.company_id, material.warehouse_line_id, amount)
    _changed(ctx, material, "service_material.returned", snapshot(before))
    return material


def consume(
    ctx: CommandContext, material: ServiceMaterial, quantity: Decimal | None, reason: str
) -> ServiceMaterial:
    """Confirms the use of an issued quantity and charges it."""
    before = states_of([material])[0]
    amount = _quantity(before, quantity)
    work_issues.consume_line(ctx, material.company_id, material.warehouse_line_id, amount)
    _changed(ctx, material, "service_material.consumed", snapshot(before))
    _charge(ctx, material, reason)
    return material


def consume_for_done_job(ctx: CommandContext, job: ServiceJob) -> None:
    """«Done» confirms the consumption of what is still issued to the work (§ 16.5.1)."""
    for state in states_of(list(ServiceMaterial.objects.select_for_update().filter(job=job))):
        if state.outstanding > 0:
            consume(ctx, state.material, None, "service_job_done")


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
