"""Parts installed on vehicles and removed from them (ТЗ § 11.1.2, § 11.2.2, § 11.4.3, § 11.4.7,
§ 11.4.8).

* Each installation of an individually accounted part is a period of its own: the part and the
  vehicle, the moment, the odometer and the service work. A reinstallation keeps the AST ID and
  opens a new period without erasing the old one. One part is never installed on two vehicles.
* The installation is the consumption of the part issued to the work: its cost goes to the
  vehicle at the cost of the part; the price for the client is set apart (§ 11.4.7).
* A removal gives the moment, the odometer and the work; the part then goes to a warehouse with a
  new valuation — a cost, an explicit zero or unknown, never zero for unknown — or is written off.
  The removal brings no income to the vehicle and does not lower the costs counted before. A
  missed removal is entered with its actual moment and odometer; the chronology is checked.
* While a part is installed its duration runs to now and its mileage to the last known reading
  of the vehicle; after the removal the end values are used. Without readings the mileage is
  unknown, not zero; the accumulated mileage of § 8.2.13 is used, and a change of the unit
  converts the values (§ 8.2.10).

The service module calls these functions; it does not read the warehouse tables.

PROPOSAL (block 15): the installation and the removal are made from a service work of the
vehicle by the right of doing works; a removed part is taken into a warehouse by «Receive» there
and written off by «Write off» in the warehouse it was issued from; the installation is on a
vehicle only (an asset inside another asset is not in the MVP, § 11.4.8).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import Company
from c4rsys.fleet import odometer as odometers
from c4rsys.fleet.odometer import Conversion
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse import operations
from c4rsys.warehouse.models import (
    Asset,
    OperationLine,
    PartInstallation,
    StockMovement,
    StockOperation,
)

Disposition = PartInstallation.Disposition


# --- Costs ----------------------------------------------------------------------------------------


def asset_cost_at(asset: Asset, moment: datetime) -> Decimal | None:
    """The cost of a unit at a moment: the valuation of its latest acceptance after a removal
    (§ 11.4.7), otherwise its purchase cost. Unknown — None."""
    valuations = sorted(
        (
            i
            for i in asset.installations.all()
            if i.disposition == Disposition.WAREHOUSE and i.decided_at is not None
        ),
        key=lambda i: i.decided_at or moment,
    )
    if not valuations:
        return asset.purchase_cost
    earlier = [v for v in valuations if v.decided_at is not None and v.decided_at <= moment]
    if earlier:
        return earlier[-1].valuation
    if asset.receipt_line_id is not None and asset.receipt_line is not None:
        return asset.receipt_line.unit_cost
    return valuations[0].cost_before


# --- Commands -------------------------------------------------------------------------------------


def snapshot(i: PartInstallation) -> dict[str, Any]:
    return {
        "asset": str(i.asset_id),
        "vehicle": str(i.vehicle_id),
        "install_job": str(i.install_job_id),
        "installed_at": i.installed_at.isoformat(),
        "installed_odometer": str(i.installed_odometer)
        if i.installed_odometer is not None
        else None,
        "removal_job": str(i.removal_job_id) if i.removal_job_id else None,
        "removed_at": i.removed_at.isoformat() if i.removed_at else None,
        "removed_odometer": str(i.removed_odometer) if i.removed_odometer is not None else None,
        "replaced_by": str(i.replaced_by_id) if i.replaced_by_id else None,
        "disposition": i.disposition,
        "accepted_context": str(i.accepted_context_id) if i.accepted_context_id else None,
        "cost_mode": i.cost_mode,
        "valuation": str(i.valuation) if i.valuation is not None else None,
        "write_off": str(i.write_off_id) if i.write_off_id else None,
    }


def _not_future(moment: datetime, field: str) -> None:
    if moment > timezone.now():
        raise errors.validation_error(field, code="DATE_IN_FUTURE")


def _accumulated(card_id: UUID, value: Decimal | None, at: datetime) -> Decimal | None:
    return None if value is None else odometers.accumulated_value(card_id, value, at)


def _lock(company_id: UUID, installation_id: UUID) -> PartInstallation:
    found = (
        PartInstallation.objects.select_for_update()
        .filter(company_id=company_id, pk=installation_id)
        .first()
    )
    if found is None:
        raise errors.not_found()
    return found


def _close(
    ctx: CommandContext,
    installation: PartInstallation,
    *,
    job_id: UUID,
    removed_at: datetime,
    odometer: Decimal | None,
    comment: str,
    replaced_by: PartInstallation | None = None,
) -> None:
    if installation.removed_at is not None:
        raise errors.business_rule("PART_ALREADY_REMOVED")
    _not_future(removed_at, "removed_at")
    if removed_at < installation.installed_at:
        raise errors.business_rule(
            "PART_REMOVAL_CHRONOLOGY",
            field="removed_at",
            installed_at=installation.installed_at.isoformat(),
        )
    start = _accumulated(
        installation.vehicle_id, installation.installed_odometer, installation.installed_at
    )
    end = _accumulated(installation.vehicle_id, odometer, removed_at)
    if start is not None and end is not None and end < start:
        raise errors.business_rule(
            "PART_REMOVAL_CHRONOLOGY",
            field="odometer",
            installed_odometer=str(installation.installed_odometer),
        )
    before = snapshot(installation)
    installation.removal_job_id = job_id
    installation.removed_at = removed_at
    installation.removed_odometer = odometer
    installation.removed_by_user_id = ctx.actor.user_id
    installation.replaced_by = replaced_by
    if comment:
        installation.comment = comment
    installation.version += 1
    installation.save()
    asset = Asset.objects.select_for_update().get(pk=installation.asset_id)
    asset.state = Asset.State.REMOVED
    asset.save(update_fields=["state"])
    after = snapshot(installation)
    ctx.audit(
        "warehouse.part_removed",
        object_type="warehouse_part_installation",
        object_id=installation.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
        reason=comment,
    )


def install(
    ctx: CommandContext,
    company_id: UUID,
    *,
    line_id: UUID,
    vehicle_id: UUID,
    job_id: UUID,
    installed_at: datetime,
    odometer: Decimal | None,
    replaces_id: UUID | None = None,
) -> PartInstallation:
    """Installs a unit issued to a work on the vehicle of the work: what is still issued is
    consumed (its cost goes to the vehicle); a replaced part is removed at the same moment."""
    _not_future(installed_at, "installed_at")
    line = (
        OperationLine.objects.select_for_update(of=("self",))
        .select_related("operation", "asset")
        .filter(company_id=company_id, pk=line_id)
        .first()
    )
    if line is None or line.asset_id is None:
        raise errors.business_rule("PART_INDIVIDUAL_ONLY")
    if line.operation.kind != StockOperation.Kind.ISSUE or line.operation.vehicle_id != vehicle_id:
        raise errors.business_rule("PART_NOT_ISSUED_TO_VEHICLE")
    asset = Asset.objects.select_for_update().get(pk=line.asset_id)
    if PartInstallation.objects.filter(asset=asset, removed_at__isnull=True).exists():
        # § 11.4.8: before a new installation the part is removed.
        raise errors.business_rule("PART_ALREADY_INSTALLED", asset=asset.public_id)
    if operations.outstanding(line) > 0:
        operations.consume_issued(
            ctx, company_id, line.operation_id, (operations.LineAmount(line.id, Decimal(1)),)
        )
    elif line.consumed_quantity != 1 or asset.state != Asset.State.CONSUMED:
        raise errors.business_rule("PART_NOT_ISSUED_TO_VEHICLE")
    asset.refresh_from_db()
    asset.state = Asset.State.INSTALLED
    asset.vehicle_id = vehicle_id
    asset.save(update_fields=["state", "vehicle"])
    installation = PartInstallation.objects.create(
        company_id=company_id,
        asset=asset,
        vehicle_id=vehicle_id,
        operation_line=line,
        install_job_id=job_id,
        installed_at=installed_at,
        installed_odometer=odometer,
        installed_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "warehouse.part_installed",
        object_type="warehouse_part_installation",
        object_id=installation.id,
        after={**snapshot(installation), "asset_public_id": asset.public_id},
    )
    if replaces_id is not None:
        old = _lock(company_id, replaces_id)
        if old.vehicle_id != vehicle_id or old.removed_at is not None:
            raise errors.validation_error("replaces_id", code="PART_NOT_INSTALLED")
        if installed_at < old.installed_at:
            raise errors.business_rule("PART_REMOVAL_CHRONOLOGY", field="installed_at")
        _close(
            ctx,
            old,
            job_id=job_id,
            removed_at=installed_at,
            odometer=odometer,
            comment="",
            replaced_by=installation,
        )
    return installation


def remove(
    ctx: CommandContext,
    company_id: UUID,
    installation_id: UUID,
    *,
    vehicle_id: UUID,
    job_id: UUID,
    removed_at: datetime,
    odometer: Decimal | None,
    comment: str,
) -> PartInstallation:
    """The removal from the vehicle of the work, with the actual moment and odometer."""
    installation = _lock(company_id, installation_id)
    if installation.vehicle_id != vehicle_id:
        raise errors.validation_error("installation_id", code="PART_NOT_INSTALLED")
    _close(
        ctx, installation, job_id=job_id, removed_at=removed_at, odometer=odometer, comment=comment
    )
    return installation


def _pending(company_id: UUID, installation_id: UUID) -> tuple[PartInstallation, Asset]:
    installation = _lock(company_id, installation_id)
    if installation.removed_at is None or installation.disposition != Disposition.NONE:
        raise errors.business_rule("PART_NOT_AWAITING_DECISION")
    asset = Asset.objects.select_for_update().get(pk=installation.asset_id)
    if asset.state != Asset.State.REMOVED:
        raise errors.business_rule("PART_NOT_AWAITING_DECISION")
    return installation, asset


def accept(
    ctx: CommandContext,
    company_id: UUID,
    installation_id: UUID,
    *,
    context_id: UUID,
    cost_mode: str,
    cost: Decimal | None,
    comment: str,
) -> PartInstallation:
    """A removed part comes into a warehouse (a source of receipt, § 11.2.2) with its new
    valuation: a cost, an explicit zero or unknown (§ 11.4.7). It creates no payment, debt or
    purchase; the history stays."""
    if cost_mode not in PartInstallation.CostMode.values:
        raise errors.validation_error("cost_mode")
    if cost_mode == PartInstallation.CostMode.VALUE and cost is None:
        raise errors.validation_error("cost", code="FIELD_REQUIRED")
    if cost_mode != PartInstallation.CostMode.VALUE and cost is not None:
        raise errors.validation_error("cost", code="FIELD_NOT_APPLICABLE")
    context = locations.storage_context(company_id, context_id)
    if context is None:
        raise errors.validation_error("context_id", code="WAREHOUSE_NOT_USABLE")
    installation, asset = _pending(company_id, installation_id)
    valuation = (
        cost
        if cost_mode == PartInstallation.CostMode.VALUE
        else (Decimal(0) if cost_mode == PartInstallation.CostMode.ZERO else None)
    )
    now = timezone.now()
    installation.cost_before = asset.purchase_cost
    installation.valuation = valuation
    installation.cost_mode = cost_mode
    installation.accepted_context = context
    installation.disposition = Disposition.WAREHOUSE
    installation.decided_at = now
    installation.decided_by_user_id = ctx.actor.user_id
    if comment:
        installation.comment = comment
    installation.version += 1
    installation.save()
    asset.state = Asset.State.IN_STOCK
    asset.context = context
    asset.vehicle = None
    asset.purchase_cost = valuation
    asset.save(update_fields=["state", "context", "vehicle", "purchase_cost"])
    StockMovement.objects.create(
        company_id=company_id,
        kind=StockMovement.Kind.REMOVAL,
        context=context,
        item_id=asset.item_id,
        asset=asset,
        quantity=Decimal(1),
        actor_user_id=ctx.actor.user_id,
        created_at=now,
    )
    ctx.audit(
        "warehouse.removed_part_accepted",
        object_type="warehouse_part_installation",
        object_id=installation.id,
        before={
            "cost": str(installation.cost_before) if installation.cost_before is not None else None
        },
        after={
            "context": str(context.id),
            "cost_mode": cost_mode,
            "valuation": str(valuation) if valuation is not None else None,
        },
        reason=comment,
    )
    return installation


def write_off(
    ctx: CommandContext,
    company: Company,
    installation_id: UUID,
    *,
    reason_id: UUID | None,
    comment: str,
) -> PartInstallation:
    """A removed part is written off with a reason (§ 11.3.4) instead of coming back to stock;
    the write-off is an operation of the warehouse the part was issued from."""
    reason = operations._reason(company.id, reason_id, comment)
    installation, asset = _pending(company.id, installation_id)
    context = locations.storage_context(company.id, asset.context_id)
    if context is None:
        raise errors.business_rule("WAREHOUSE_NOT_USABLE", field="context_id")
    operation = operations._new_operation(
        ctx,
        company,
        StockOperation.Kind.WRITE_OFF,
        context,
        reason=reason,
        comment=comment,
    )
    OperationLine.objects.create(
        company_id=company.id,
        operation=operation,
        item_id=asset.item_id,
        asset=asset,
        quantity=Decimal(1),
    )
    asset.state = Asset.State.WRITTEN_OFF
    asset.vehicle = None
    asset.save(update_fields=["state", "vehicle"])
    installation.disposition = Disposition.WRITTEN_OFF
    installation.write_off = operation
    installation.decided_at = timezone.now()
    installation.decided_by_user_id = ctx.actor.user_id
    installation.version += 1
    installation.save()
    ctx.audit(
        "warehouse.removed_part_written_off",
        object_type="warehouse_part_installation",
        object_id=installation.id,
        after={"write_off": operation.number, "reason": str(reason.id)},
        reason=comment,
    )
    return installation


def guarded_lines(line_ids: list[UUID]) -> set[UUID]:
    """A write-off of a removed part is not a stock movement and is not corrected as one."""
    return set(
        OperationLine.objects.filter(
            pk__in=line_ids,
            operation_id__in=PartInstallation.objects.filter(write_off__isnull=False).values(
                "write_off_id"
            ),
        ).values_list("id", flat=True)
    )


# --- Reading --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    days: int
    distance: Decimal | None


def run(installation: PartInstallation, now: datetime | None = None) -> Run:
    """Duration to now and mileage to the last known reading while installed; the end values
    after the removal. Without readings the mileage is unknown (§ 11.4.3)."""
    moment = now or timezone.now()
    end = installation.removed_at or moment
    days = max((end - installation.installed_at).days, 0)
    start_value = _accumulated(
        installation.vehicle_id, installation.installed_odometer, installation.installed_at
    )
    if installation.removed_at is not None:
        end_value = _accumulated(
            installation.vehicle_id, installation.removed_odometer, installation.removed_at
        )
    else:
        end_value = odometers.current_accumulated(installation.vehicle_id)
    distance = (
        end_value - start_value
        if start_value is not None and end_value is not None and end_value >= start_value
        else None
    )
    return Run(days=days, distance=distance)


def payload(installation: PartInstallation, names: dict[UUID, str]) -> dict[str, Any]:
    asset = installation.asset
    line = installation.operation_line
    cost = asset_cost_at(asset, line.operation.created_at) if line is not None else None
    measured = run(installation)
    return {
        "id": str(installation.id),
        "version": installation.version,
        "asset": {"id": str(asset.id), "public_id": asset.public_id},
        "item": {"id": str(asset.item_id), "name": asset.item.name},
        "vehicle": {
            "id": str(installation.vehicle_id),
            "name": names.get(installation.vehicle_id, ""),
        },
        "install_job_id": str(installation.install_job_id),
        "installed_at": installation.installed_at.isoformat(),
        "installed_odometer": str(installation.installed_odometer)
        if installation.installed_odometer is not None
        else None,
        "removal_job_id": str(installation.removal_job_id) if installation.removal_job_id else None,
        "removed_at": installation.removed_at.isoformat() if installation.removed_at else None,
        "removed_odometer": str(installation.removed_odometer)
        if installation.removed_odometer is not None
        else None,
        "replaced_by": str(installation.replaced_by_id) if installation.replaced_by_id else None,
        "disposition": installation.disposition,
        "accepted_context_id": str(installation.accepted_context_id)
        if installation.accepted_context_id
        else None,
        "cost_mode": installation.cost_mode,
        "valuation": str(installation.valuation) if installation.valuation is not None else None,
        "cost": str(cost) if cost is not None else None,
        "cost_known": cost is not None,
        "currency": asset.currency,
        "write_off_id": str(installation.write_off_id) if installation.write_off_id else None,
        "days": measured.days,
        "distance": str(measured.distance) if measured.distance is not None else None,
        "comment": installation.comment,
        "issued_from_context_id": str(asset.context_id),
    }


def installations(company_id: UUID) -> Any:
    return (
        PartInstallation.objects.filter(company_id=company_id)
        .select_related("asset__item", "asset__receipt_line", "operation_line__operation")
        .prefetch_related("asset__installations")
        .order_by("-installed_at", "-created_at")
    )


def of_vehicle(company_id: UUID, card_id: UUID) -> list[PartInstallation]:
    return list(installations(company_id).filter(vehicle_id=card_id))


def open_on_vehicle(company_id: UUID, card_id: UUID) -> list[PartInstallation]:
    return list(installations(company_id).filter(vehicle_id=card_id, removed_at__isnull=True))


def of_jobs(company_id: UUID, job_ids: list[UUID]) -> list[PartInstallation]:
    from django.db.models import Q

    return list(
        installations(company_id).filter(
            Q(install_job_id__in=job_ids) | Q(removal_job_id__in=job_ids)
        )
    )


def awaiting(company_id: UUID) -> list[PartInstallation]:
    """Removed parts waiting to be taken into a warehouse or written off."""
    return list(
        installations(company_id).filter(removed_at__isnull=False, disposition=Disposition.NONE)
    )


def of_asset(company_id: UUID, asset_id: UUID) -> list[PartInstallation]:
    return list(installations(company_id).filter(asset_id=asset_id))


def installed_line_ids(line_ids: list[UUID]) -> set[UUID]:
    return {
        line_id
        for line_id in PartInstallation.objects.filter(operation_line_id__in=line_ids).values_list(
            "operation_line_id", flat=True
        )
        if line_id is not None
    }


def unit_values(
    company_id: UUID, card_id: UUID, conv: Conversion, apply: bool
) -> list[dict[str, Any]]:
    """§ 8.2.10: the odometer values of installations and removals follow a change of the unit."""
    changes: list[dict[str, Any]] = []
    found = PartInstallation.objects.filter(company_id=company_id, vehicle_id=card_id)
    if apply:
        found = found.select_for_update()
    for installation in found:
        fields: dict[str, Decimal] = {}
        for name in ("installed_odometer", "removed_odometer"):
            value = getattr(installation, name)
            if value is not None:
                fields[name] = conv.distance(value)
        for name, after in fields.items():
            changes.append(
                {
                    "what": "part_installation",
                    "id": str(installation.id),
                    "field": name,
                    "before": f"{getattr(installation, name)} {conv.from_unit}",
                    "after": f"{after} {conv.to_unit}",
                }
            )
        if apply and fields:
            for name, after in fields.items():
                setattr(installation, name, after)
            installation.save(update_fields=list(fields))
    return changes
