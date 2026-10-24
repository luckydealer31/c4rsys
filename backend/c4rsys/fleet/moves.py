"""Internal move of a vehicle to another location of the company, recorded after it happened
(ТЗ § 8.5.2, § 15.1.3–15.1.5, § 23.6.2; decision 23.10.2026). One action: the driver and the
actual start and end are entered, an internal order of the company gets the move, and the
vehicle's accounting location becomes the destination. Planned moves with «in transit»,
expenses and client delivery come later."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import Employment
from c4rsys.fleet.models import VehicleCard, VehicleMove
from c4rsys.fleet.services import lock_card, move_to_location
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

# (company_id, vehicle_card_id) -> codes of what keeps the vehicle where it is now.
BusyCheck = Callable[[UUID, UUID], list[str]]
_busy: list[BusyCheck] = []


def register_busy(check: BusyCheck) -> None:
    _busy.append(check)


@dataclass(frozen=True)
class MoveRequest:
    vehicle_card_id: UUID
    to_location_id: UUID
    driver_employment_id: UUID
    actual_start: datetime
    actual_end: datetime
    note: str = ""


def record_move(ctx: CommandContext, company_id: UUID, request: MoveRequest) -> VehicleMove:
    card = lock_card(company_id, request.vehicle_card_id)
    if card.storage_context_id is not None:
        # A vehicle on a warehouse is a stock item, not moved as a garage vehicle (§ 8.1.5).
        raise errors.business_rule("VEHICLE_IN_WAREHOUSE")
    if card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_ARCHIVED")
    source = card.accounting_location_id
    if source is None:
        raise errors.business_rule("VEHICLE_WITHOUT_LOCATION")
    if request.to_location_id == source:
        raise errors.validation_error("to_location_id", code="MOVE_SAME_LOCATION")
    locations.require_usable(company_id, request.to_location_id, "to_location_id")
    busy = [code for check in _busy for code in check(company_id, card.id)]
    if busy:
        raise errors.business_rule("VEHICLE_BUSY", reasons=busy)
    driver = Employment.objects.filter(
        company_id=company_id, pk=request.driver_employment_id, state=Employment.State.ACTIVE
    ).first()
    if driver is None:
        raise errors.validation_error("driver_employment_id", code="DRIVER_UNKNOWN")
    if request.actual_end <= request.actual_start:
        raise errors.validation_error("actual_end", code="PERIOD_INVALID")
    if request.actual_end > timezone.now():
        raise errors.business_rule("FUTURE_TIME_NOT_ALLOWED", field="actual_end")
    # TODO(spec): whether the start may precede the vehicle's arrival in the source location.

    order = orders.create_internal_order(
        ctx, company_id=company_id, responsible_employment_id=ctx.actor.employment_id
    )
    move = VehicleMove.objects.create(
        company_id=company_id,
        vehicle_card=card,
        order=order,
        from_location_id=source,
        to_location_id=request.to_location_id,
        driver_employment=driver,
        actual_start=request.actual_start,
        actual_end=request.actual_end,
        note=request.note,
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "vehicle.moved",
        object_type="vehicle_move",
        object_id=move.id,
        after={
            "vehicle_card": str(card.id),
            "order": str(order.id),
            "order_number": order.number,
            "from_location": str(source),
            "to_location": str(request.to_location_id),
            # The actual driver and the employee who entered it are kept apart (§ 23.6.2).
            "driver_employment": str(driver.id),
            "actual_start": request.actual_start.isoformat(),
            "actual_end": request.actual_end.isoformat(),
            "note": request.note,
        },
    )
    move_to_location(ctx, card, request.to_location_id, reason="move")
    return move


def moves_of(company_id: UUID, card_id: UUID) -> list[VehicleMove]:
    return list(
        VehicleMove.objects.filter(company_id=company_id, vehicle_card_id=card_id).order_by(
            "-actual_end"
        )
    )


# --- For orders ------------------------------------------------------------------------------


def operations_for_order(company_id: UUID, order_id: UUID) -> list[orders.OperationSummary]:
    """A recorded move is finished; its location is the source (§ 15.1.4)."""
    return [
        orders.OperationSummary(
            type="move",
            id=move.id,
            state="completed",
            location_id=move.from_location_id,
            finished=True,
        )
        for move in VehicleMove.objects.filter(company_id=company_id, order_id=order_id)
    ]


def history_for_order(company_id: UUID, order_id: UUID) -> list[tuple[str, str]]:
    return [
        ("vehicle_move", str(move_id))
        for move_id in VehicleMove.objects.filter(
            company_id=company_id, order_id=order_id
        ).values_list("id", flat=True)
    ]
