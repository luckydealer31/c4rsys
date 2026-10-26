"""Return of a vehicle from rental (ТЗ § 8.5.4, § 14.5.1, § 14.5.3, § 14.5.5–14.5.6).

The return, the formalization and the settlement are three separate states (§ 14.5.6): the return
is recorded even without the readings, the inspection then stays a draft. The base cost is
recalculated by the actual period from the stored snapshot, and the charge is brought to it by
an appended difference.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.finance import charges
from c4rsys.finance.models import ChargeEntry
from c4rsys.fleet import services as fleet
from c4rsys.inspections import services as inspections
from c4rsys.inspections.models import Inspection
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.rentals import issue_policies, rentals
from c4rsys.rentals.models import Rental, RentalFreeInterval


@dataclass(frozen=True)
class ReturnRequest:
    rental_id: UUID
    actual_end: datetime
    return_location_id: UUID
    place_kind: str
    place_note: str
    odometer: Decimal | None
    fuel: Decimal | None
    inspection_conducted: bool
    expected_version: int


def acceptance_location(rental: Rental, place_kind: str, location_id: UUID) -> UUID:
    """«Other» is an outside address of the issue location (§ 8.5.4)."""
    if place_kind == Rental.PlaceKind.OTHER and location_id != rental.issue_location_id:
        raise errors.validation_error("return_location_id", code="OTHER_PLACE_OF_ISSUE_LOCATION")
    return location_id


def recalculate(ctx: CommandContext, rental: Rental, reason: str) -> ChargeEntry | None:
    """Base cost by the actual period with the free time (§ 14.5.3, § 13.3) and the mileage
    overage (§ 13.4.4) as separate charges of the rental."""
    snapshot, start, end, client_id = (
        rental.tariff_snapshot,
        rental.actual_start,
        rental.actual_end,
        rental.client_id,
    )
    if end is None or start is None or snapshot is None or client_id is None:
        raise RuntimeError("Only a finished period of an issued rental is charged")
    result = rentals.calculate(rental, end)
    _charge_mileage(ctx, rental, client_id, reason)
    return charges.set_charge(
        ctx,
        company_id=rental.company_id,
        order_id=rental.order_id,
        client_id=client_id,
        source_type=ChargeEntry.SourceType.RENTAL_BASE,
        source_id=rental.id,
        amount=result.base_amount,
        currency=result.currency,
        reason=reason,
        calculation={
            "actual_start": start.isoformat(),
            "actual_end": end.isoformat(),
            "billable_minutes": int(result.billable.total_seconds() // 60),
            "free_minutes": int(result.free.total_seconds() // 60),
            "units": result.units,
            "rate": str(result.rate) if result.rate is not None else None,
            "base_amount": str(result.base_amount),
            "tariff_id": snapshot.get("tariff_id"),
            "tariff_version": snapshot.get("tariff_version"),
        },
    )


def _charge_mileage(ctx: CommandContext, rental: Rental, client_id: UUID, reason: str) -> None:
    """The overage of the included mileage as its own charge (§ 13.4.3–13.4.4; decision Т5);
    without both readings nothing is charged yet."""
    result = rentals.mileage(rental)
    if result is None or result.get("currency") is None:
        return
    charges.set_charge(
        ctx,
        company_id=rental.company_id,
        order_id=rental.order_id,
        client_id=client_id,
        source_type=ChargeEntry.SourceType.RENTAL_MILEAGE,
        source_id=rental.id,
        amount=result["amount"],
        currency=result["currency"],
        reason=reason,
        calculation={k: str(v) if v is not None else None for k, v in result.items()},
    )


def accept_return(ctx: CommandContext, request: ReturnRequest) -> tuple[Rental, Inspection]:
    company_id = ctx.actor.company_id
    if company_id is None:
        raise RuntimeError("Return runs in a company")
    now = timezone.now()
    found = Rental.objects.filter(company_id=company_id, pk=request.rental_id).first()
    if found is None:
        raise errors.not_found()
    # Lock order: vehicle → rental (STAGE1_COMMANDS_API).
    card = fleet.lock_card(company_id, found.vehicle_card_id)
    rental = Rental.objects.select_for_update().get(pk=found.pk)
    if rental.version != request.expected_version:
        raise errors.version_conflict()
    orders.ensure_open(rental.company_id, rental.order_id)
    if rental.state != Rental.State.ACTIVE:
        raise errors.business_rule("RENTAL_NOT_ACTIVE", state=rental.state)
    if request.actual_end > now:
        raise errors.business_rule("FUTURE_TIME_NOT_ALLOWED", field="actual_end")
    if rental.actual_start is None or request.actual_end <= rental.actual_start:
        raise errors.validation_error("actual_end", code="PERIOD_INVALID")
    # § 13.3.2: free time past the new end is a conflict; the manager moves or shortens it by
    # hand, nothing is cut automatically.
    outside = RentalFreeInterval.objects.filter(rental_id=rental.id, end__gt=request.actual_end)
    if outside.exists():
        raise errors.business_rule(
            "FREE_INTERVAL_OUTSIDE_RENTAL",
            field="actual_end",
            intervals=[str(i.id) for i in outside],
        )
    location_id = acceptance_location(rental, request.place_kind, request.return_location_id)
    locations.require_usable(company_id, location_id, "return_location_id")

    before = {"state": rental.state, "actual_end": None}
    rental.actual_end = request.actual_end
    rental.actual_period = (rental.actual_start, request.actual_end)
    rental.return_location_id = location_id
    rental.return_place_kind = request.place_kind
    rental.return_place_note = request.place_note
    rental.state = Rental.State.RETURNED
    rental.version += 1
    rental.save()

    complete = request.odometer is not None and request.fuel is not None
    readings = inspections.Readings(
        performed_at=request.actual_end,
        place_kind=request.place_kind,
        place_location_id=location_id,
        place_note=request.place_note,
        check_status=_check_status(rental.company_id, location_id, request.inspection_conducted),
        odometer=request.odometer,
        fuel=request.fuel,
    )
    create = inspections.create_saved if complete else inspections.create_draft
    inspection = create(
        ctx, card, kind=Inspection.Kind.RETURN, rental_id=rental.id, readings=readings
    )
    if complete and request.odometer is not None:
        fleet.record_reading(
            ctx,
            card,
            measured_at=request.actual_end,
            value=request.odometer,
            source_type="inspection",
            source_id=inspection.id,
        )
    # Acceptance in another location makes it the vehicle's location; «other» keeps it (§ 8.5.4).
    if request.place_kind == Rental.PlaceKind.LOCATION:
        fleet.move_to_location(ctx, card, location_id, reason="return_from_rental")

    recalculate(ctx, rental, reason="return")
    ctx.audit(
        "rental.returned",
        object_type="rental",
        object_id=rental.id,
        before=before,
        after={
            "state": rental.state,
            "actual_end": request.actual_end,
            "return_location": str(location_id),
            "place_kind": request.place_kind,
            "place_note": request.place_note,
            "inspection": str(inspection.id),
            "inspection_state": inspection.state,
        },
    )
    return rental, inspection


def _check_status(company_id: UUID, location_id: UUID | None, conducted: bool) -> str:
    """A condition check required by the policy of the acceptance location and not done is
    recorded as such: a conflict, never a refusal (§ 10.3.3, § 14.3.1)."""
    if conducted:
        return Inspection.CheckStatus.CONDUCTED
    policy = issue_policies.policy_for(company_id, location_id)
    if policy["inspection"] == "required":
        return Inspection.CheckStatus.REQUIRED_NOT_DONE
    return Inspection.CheckStatus.NOT_REQUIRED


def save_return_readings(
    ctx: CommandContext,
    rental: Rental,
    inspection: Inspection,
    *,
    odometer: Decimal,
    fuel: Decimal,
    conducted: bool,
) -> Inspection:
    """Readings entered later into the draft of the return inspection (§ 14.5.5)."""
    card = fleet.lock_card(rental.company_id, rental.vehicle_card_id)
    orders.ensure_open(rental.company_id, rental.order_id)
    locked = Inspection.objects.select_for_update().get(pk=inspection.pk)
    saved = inspections.save_draft(
        ctx,
        locked,
        odometer=odometer,
        fuel=fuel,
        check_status=_check_status(rental.company_id, rental.return_location_id, conducted),
    )
    fleet.record_reading(
        ctx,
        card,
        measured_at=saved.performed_at,
        value=odometer,
        source_type="inspection",
        source_id=saved.id,
    )
    # With both readings the mileage overage can be charged now (customer's decision Т5).
    if rental.client_id is not None:
        _charge_mileage(ctx, rental, rental.client_id, "readings")
    return saved
