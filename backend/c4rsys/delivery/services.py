"""Delivery and internal move: commands and what other modules see (ТЗ § 15.1–15.5; decisions
Д-1…Д-11 of 29.10.2026).

* A planned move has the vehicle, the start and end points and the planned start and end; the
  driver may come later (§ 15.1.3). Entered afterwards, the plan may be empty (decision Д-1).
* The start needs the driver and the actual start; the end — the actual end and the result
  (§ 15.1.3). The driver is one employee of the company (§ 15.2.4–15.2.5); the author of an entry
  is kept apart.
* A free move is taken by one employee only (§ 15.5.1).
* Completed with «accepted in the location» at another location of the company, the vehicle's
  location of accounting becomes that location; «A / Other» does not change it (decision Д-2,
  § 8.5.2).
* A planned move holds nothing; a move in transit holds the vehicle: no issue, no other move, no
  acceptance into a service (decision Д-3).
* An internal move belongs to an internal order of the company; a delivery made without a client
  is internal and may be attached to a client order before it starts (§ 15.1.6; decision Д-9).
* Overlapping assignments of one driver are warned of, not refused (§ 15.2.7).
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import Company, Employment
from c4rsys.delivery import money
from c4rsys.delivery.models import Delivery
from c4rsys.fleet import moves
from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import VehicleCard
from c4rsys.orders import services as orders
from c4rsys.orders.models import Order
from c4rsys.orders.services import OperationSummary
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number

State = Delivery.State
Result = Delivery.Result
Kind = Delivery.PointKind
FINAL = (State.COMPLETED, State.CANCELLED, State.TERMINATED)


def _not_future(moment: datetime, field_name: str) -> None:
    if moment > timezone.now():
        raise errors.business_rule("FUTURE_TIME_NOT_ALLOWED", field=field_name)


# --- Reading -------------------------------------------------------------------------------------


def deliveries_of(company_id: UUID) -> QuerySet[Delivery]:
    return Delivery.objects.filter(company_id=company_id)


def get(company_id: UUID, delivery_id: UUID) -> Delivery:
    found = deliveries_of(company_id).filter(pk=delivery_id).first()
    if found is None:
        raise errors.not_found()
    return found


def snapshot(d: Delivery) -> dict[str, Any]:
    return {
        "number": d.number,
        "order": str(d.order_id),
        "vehicle": str(d.vehicle_card_id),
        "from": _point(d, "from"),
        "to": _point(d, "to"),
        "planned_start": d.planned_start,
        "planned_end": d.planned_end,
        "driver": str(d.driver_id) if d.driver_id else None,
        "state": d.state,
        "actual_start": d.actual_start,
        "actual_end": d.actual_end,
        "result": d.result or None,
        "service": str(d.service_id) if d.service_id else None,
        "price": money.amount_text(d.price),
    }


def _point(d: Delivery, side: str) -> dict[str, Any]:
    lat = getattr(d, f"{side}_lat")
    lng = getattr(d, f"{side}_lng")
    return {
        "kind": getattr(d, f"{side}_kind"),
        "location": str(getattr(d, f"{side}_location_id")),
        "name": getattr(d, f"{side}_name"),
        "address": getattr(d, f"{side}_address"),
        "comment": getattr(d, f"{side}_comment"),
        "lat": str(lat) if lat is not None else None,
        "lng": str(lng) if lng is not None else None,
    }


def driver_overlaps(d: Delivery) -> list[dict[str, Any]]:
    """Other planned or running moves of the same driver over the planned time (§ 15.2.7)."""
    if d.driver_id is None or d.planned_start is None or d.planned_end is None:
        return []
    found = (
        deliveries_of(d.company_id)
        .filter(driver_id=d.driver_id, state__in=(State.PLANNED, State.IN_TRANSIT))
        .exclude(pk=d.id)
        .filter(planned_start__lt=d.planned_end, planned_end__gt=d.planned_start)
    )
    return [
        {
            "id": str(o.id),
            "number": o.number,
            "planned_start": o.planned_start.isoformat() if o.planned_start else None,
            "planned_end": o.planned_end.isoformat() if o.planned_end else None,
        }
        for o in found
    ]


# --- What other modules see ----------------------------------------------------------------------


def operations_for_order(company_id: UUID, order_id: UUID) -> list[OperationSummary]:
    return [
        OperationSummary(
            type="delivery",
            id=d.id,
            state=d.state,
            location_id=d.from_location_id,
            finished=d.state in FINAL,
        )
        for d in deliveries_of(company_id).filter(order_id=order_id).order_by("number")
    ]


def history_for_order(company_id: UUID, order_id: UUID) -> list[tuple[str, str]]:
    objects: list[tuple[str, str]] = []
    for i in deliveries_of(company_id).filter(order_id=order_id).values_list("id", flat=True):
        objects.append(("delivery", str(i)))
        objects += money.history(i)
    return objects


def on_order_cancel(ctx: CommandContext, company_id: UUID, order_id: UUID, reason: str) -> None:
    """Planned moves go with the cancelled order; a started one keeps it (§ 12.2.1)."""
    for d in Delivery.objects.select_for_update().filter(company_id=company_id, order_id=order_id):
        if d.state == State.PLANNED:
            _cancel(ctx, d, reason)
        elif d.state in (State.IN_TRANSIT, State.COMPLETED, State.TERMINATED):
            raise errors.business_rule("ORDER_HAS_STARTED_OPERATIONS")


def vehicle_busy(company_id: UUID, card_id: UUID) -> list[str]:
    """A vehicle in transit is not moved again or accepted elsewhere (decision Д-3)."""
    in_transit = deliveries_of(company_id).filter(vehicle_card_id=card_id, state=State.IN_TRANSIT)
    return ["in_transit"] if in_transit.exists() else []


def occupancy(
    company_id: UUID, card_id: UUID, start: datetime, end: datetime | None
) -> list[dict[str, Any]]:
    """Moves of the vehicle in [start, end): in transit from the actual start, finished ones over
    their actual period, planned ones over their plan (only shown, decision Д-3)."""
    query = deliveries_of(company_id).filter(vehicle_card_id=card_id)
    running = query.filter(state=State.IN_TRANSIT)
    done = query.filter(state__in=(State.COMPLETED, State.TERMINATED), actual_end__gt=start)
    if end is not None:
        running = running.filter(actual_start__lt=end)
        done = done.filter(actual_start__lt=end)
    planned = query.filter(state=State.PLANNED, planned_end__gt=start)
    if end is not None:
        planned = planned.filter(planned_start__lt=end)
    # § 14.6.3: a booking over a delivery is only warned of; an issue is refused over a running
    # or finished one, a planned one is only shown (decision Д-3).
    return [
        {"kind": "delivery", "id": str(d.id), "number": d.number, "warning": True}
        for d in (*running, *done)
    ] + [
        {"kind": "delivery", "id": str(d.id), "number": d.number, "warning": True, "planned": True}
        for d in planned
    ]


def location_blockers(company_id: UUID, location_id: UUID, context_type: str | None) -> list[str]:
    """Unfinished moves from or to a location keep it from being deactivated (§ 4.4.1)."""
    if context_type is not None:
        return []
    unfinished = (
        deliveries_of(company_id)
        .filter(Q(from_location_id=location_id) | Q(to_location_id=location_id))
        .exclude(state__in=FINAL)
    )
    return ["deliveries_not_finished"] if unfinished.exists() else []


# --- Creation (§ 15.1) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Point:
    kind: str
    location_id: UUID | None
    name: str = ""
    address: str = ""
    comment: str = ""
    lat: Decimal | None = None
    lng: Decimal | None = None


@dataclass(frozen=True)
class NewDelivery:
    vehicle_card_id: UUID
    to: Point
    from_point: Point | None  # empty — the vehicle's location (§ 15.1.5)
    planned_start: datetime | None
    planned_end: datetime | None
    driver_id: UUID | None = None
    order_id: UUID | None = None  # a client order; empty — a new internal order
    extra: dict[str, Any] = field(default_factory=dict)


def _check_vehicle(card: VehicleCard) -> None:
    if card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_ARCHIVED", field="vehicle_card_id")
    if card.storage_context_id is not None:
        raise errors.business_rule("VEHICLE_IN_WAREHOUSE", field="vehicle_card_id")


def _employee(company_id: UUID, employment_id: UUID | None) -> UUID | None:
    """The driver is an employee of this company (§ 15.2.4)."""
    if employment_id is None:
        return None
    if not Employment.objects.filter(
        company_id=company_id, pk=employment_id, state=Employment.State.ACTIVE
    ).exists():
        raise errors.validation_error("driver_id", code="EMPLOYEE_NOT_ACTIVE")
    return employment_id


def source_location(card: VehicleCard, point: Point | None) -> UUID:
    """The start is where the vehicle is bound now (§ 15.1.5): its location or «A / Other» of
    it. A vehicle without a location (a client's) starts from the location given."""
    if card.accounting_location_id is not None:
        if point is not None and point.location_id not in (None, card.accounting_location_id):
            raise errors.business_rule(
                "VEHICLE_LOCATION_MISMATCH",
                field="from.location_id",
                vehicle_location=str(card.accounting_location_id),
            )
        return card.accounting_location_id
    if point is None or point.location_id is None:
        raise errors.validation_error("from.location_id", code="FIELD_REQUIRED")
    return point.location_id


def _apply_point(d: Delivery, side: str, point: Point, location_id: UUID) -> None:
    if point.kind not in Kind.values:
        raise errors.validation_error(f"{side}.kind", code="CHOICE_REQUIRED")
    locations.require_usable(d.company_id, location_id, f"{side}.location_id")
    described = point.name or point.address or point.comment or point.lat is not None
    if point.kind == Kind.OTHER and not described:
        raise errors.validation_error(f"{side}.address", code="FIELD_REQUIRED")
    setattr(d, f"{side}_kind", point.kind)
    setattr(d, f"{side}_location_id", location_id)
    other = point.kind == Kind.OTHER
    setattr(d, f"{side}_name", point.name if other else "")
    setattr(d, f"{side}_address", point.address if other else "")
    setattr(d, f"{side}_comment", point.comment if other else "")
    setattr(d, f"{side}_lat", point.lat if other else None)
    setattr(d, f"{side}_lng", point.lng if other else None)


def _apply_route(d: Delivery, card: VehicleCard, data: NewDelivery) -> None:
    source = source_location(card, data.from_point)
    from_point = data.from_point or Point(kind=Kind.LOCATION, location_id=source)
    _apply_point(d, "from", from_point, source)
    to = data.to
    if to.kind == Kind.OTHER:
        # § 15.1.7, § 15.1.8: «Other» belongs to the location the vehicle is bound to; a delivery
        # from A cannot bind the vehicle to B by itself.
        if to.location_id not in (None, source):
            raise errors.validation_error("to.location_id", code="DELIVERY_OTHER_OF_SOURCE")
        _apply_point(d, "to", to, source)
    else:
        if to.location_id is None:
            raise errors.validation_error("to.location_id", code="FIELD_REQUIRED")
        if from_point.kind == Kind.LOCATION and to.location_id == source:
            raise errors.validation_error("to.location_id", code="MOVE_SAME_LOCATION")
        _apply_point(d, "to", to, to.location_id)


def _plan(start: datetime | None, end: datetime | None, *, required: bool) -> None:
    if (start is None) != (end is None) or (required and start is None):
        raise errors.validation_error(
            "planned_start" if start is None else "planned_end", code="FIELD_REQUIRED"
        )
    if start is not None and end is not None and end <= start:
        raise errors.validation_error("planned_end", code="PERIOD_INVALID")


def _order(ctx: CommandContext, company_id: UUID, order_id: UUID | None) -> Order:
    if order_id is None:
        return orders.create_internal_order(
            ctx, company_id=company_id, responsible_employment_id=ctx.actor.employment_id
        )
    return orders.lock_open_order(company_id, order_id)


def create(ctx: CommandContext, company: Company, data: NewDelivery) -> Delivery:
    """A planned move or delivery (§ 15.1.3–15.1.6)."""
    _plan(data.planned_start, data.planned_end, required=True)
    card = fleet.lock_card(company.id, data.vehicle_card_id)  # vehicle → delivery → order
    _check_vehicle(card)
    d = Delivery(company=company, vehicle_card=card, created_by_user_id=ctx.actor.user_id)
    _apply_route(d, card, data)
    d.order = _order(ctx, company.id, data.order_id)
    d.number = next_number(company.id, "delivery")
    d.planned_start = data.planned_start
    d.planned_end = data.planned_end
    d.driver_id = _employee(company.id, data.driver_id)
    d.created_at = timezone.now()
    d.save()
    ctx.audit("delivery.created", object_type="delivery", object_id=d.id, after=snapshot(d))
    return d


def locked(company_id: UUID, delivery_id: UUID, expected_version: int | None) -> Delivery:
    card_id = (
        deliveries_of(company_id)
        .filter(pk=delivery_id)
        .values_list("vehicle_card_id", flat=True)
        .first()
    )
    if card_id is None:
        raise errors.not_found()
    fleet.lock_card(company_id, card_id)
    d = Delivery.objects.select_for_update().get(pk=delivery_id)
    if expected_version is not None and d.version != expected_version:
        raise errors.version_conflict()
    return d


def _save(ctx: CommandContext, d: Delivery, action: str, before: dict[str, Any]) -> None:
    d.version += 1
    try:
        with transaction.atomic():
            d.save()
    except IntegrityError as exc:
        raise errors.business_rule("VEHICLE_IN_TRANSIT", field="vehicle_card_id") from exc
    after = snapshot(d)
    ctx.audit(
        action,
        object_type="delivery",
        object_id=d.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )


def change(ctx: CommandContext, d: Delivery, data: NewDelivery) -> Delivery:
    """A planned move changes its route and plan until it starts (§ 23.6.6)."""
    if d.state != State.PLANNED:
        raise errors.business_rule("DELIVERY_ALREADY_STARTED", state=d.state)
    orders.ensure_editable(d.company_id, d.order_id)
    _plan(data.planned_start, data.planned_end, required=True)
    before = snapshot(d)
    card = VehicleCard.objects.get(pk=d.vehicle_card_id)
    _apply_route(d, card, data)
    d.planned_start = data.planned_start
    d.planned_end = data.planned_end
    if before == snapshot(d):
        raise errors.business_rule("NOTHING_CHANGED")
    _save(ctx, d, "delivery.changed", before)
    return d


def assign(ctx: CommandContext, d: Delivery, driver_id: UUID | None) -> Delivery:
    """The driver is appointed or changed before the start (§ 15.2.4, § 15.2.5)."""
    if d.state != State.PLANNED:
        raise errors.business_rule("DELIVERY_ALREADY_STARTED", state=d.state)
    if d.driver_id == driver_id:
        raise errors.business_rule("NOTHING_CHANGED")
    before = snapshot(d)
    d.driver_id = _employee(d.company_id, driver_id)
    _save(ctx, d, "delivery.assigned", before)
    return d


def take(ctx: CommandContext, d: Delivery) -> Delivery:
    """A free move is taken by one employee who becomes its driver (§ 15.2.4, § 15.5.1)."""
    if d.state != State.PLANNED:
        raise errors.business_rule("DELIVERY_ALREADY_STARTED", state=d.state)
    if d.driver_id is not None:
        raise errors.business_rule("DELIVERY_TAKEN")
    if ctx.actor.employment_id is None:
        raise errors.permission_denied("delivery.take")
    before = snapshot(d)
    d.driver_id = ctx.actor.employment_id
    _save(ctx, d, "delivery.taken", before)
    return d


def start(ctx: CommandContext, d: Delivery, at: datetime) -> Delivery:
    """The movement starts with the driver and the actual start; it is not the start of a rental
    (§ 15.1.3, § 15.3.1)."""
    if d.state != State.PLANNED:
        raise errors.business_rule("DELIVERY_ALREADY_STARTED", state=d.state)
    if d.driver_id is None:
        raise errors.business_rule("DELIVERY_DRIVER_REQUIRED", field="driver_id")
    orders.ensure_open(d.company_id, d.order_id)
    _not_future(at, "actual_start")
    card = VehicleCard.objects.get(pk=d.vehicle_card_id)
    _check_vehicle(card)
    if card.accounting_location_id not in (None, d.from_location_id):
        raise errors.business_rule(
            "VEHICLE_LOCATION_MISMATCH",
            field="vehicle_card_id",
            vehicle_location=str(card.accounting_location_id),
        )
    busy = [c for c in moves.busy_reasons(d.company_id, card.id) if c != "in_transit"]
    if busy:
        raise errors.business_rule("VEHICLE_BUSY", reasons=busy)
    before = snapshot(d)
    d.state = State.IN_TRANSIT
    d.actual_start = at
    d.started_by_user_id = ctx.actor.user_id
    _save(ctx, d, "delivery.started", before)
    return d


def finish(ctx: CommandContext, d: Delivery, at: datetime, result: str, comment: str) -> Delivery:
    """«Completed» with the actual end and the result; «accepted in the location» at another
    location binds the vehicle there (decision Д-2)."""
    if d.state != State.IN_TRANSIT:
        raise errors.business_rule("DELIVERY_NOT_IN_TRANSIT", state=d.state)
    if result not in Result.values:
        raise errors.validation_error("result", code="CHOICE_REQUIRED")
    if result == Result.OTHER and not comment.strip():
        raise errors.validation_error("comment", code="FIELD_REQUIRED")
    if result == Result.ACCEPTED_IN_LOCATION and d.to_kind != Kind.LOCATION:
        raise errors.validation_error("result", code="DELIVERY_RESULT_NEEDS_LOCATION")
    orders.ensure_open(d.company_id, d.order_id)
    _not_future(at, "actual_end")
    if d.actual_start is not None and at < d.actual_start:
        raise errors.validation_error("actual_end", code="PERIOD_INVALID")
    before = snapshot(d)
    d.state = State.COMPLETED
    d.actual_end = at
    d.result = result
    d.result_comment = comment
    d.finished_by_user_id = ctx.actor.user_id
    d.finished_entered_at = timezone.now()
    _save(ctx, d, "delivery.completed", before)
    if result == Result.ACCEPTED_IN_LOCATION:
        card = fleet.lock_card(d.company_id, d.vehicle_card_id)
        fleet.move_to_location(ctx, card, d.to_location_id, f"delivery {d.number}")
    return d


def terminate(ctx: CommandContext, d: Delivery, at: datetime, reason: str) -> Delivery:
    """«Terminated» — the movement started but the route was not finished (§ 15.3.9)."""
    if d.state != State.IN_TRANSIT:
        raise errors.business_rule("DELIVERY_NOT_IN_TRANSIT", state=d.state)
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    orders.ensure_open(d.company_id, d.order_id)
    _not_future(at, "actual_end")
    if d.actual_start is not None and at < d.actual_start:
        raise errors.validation_error("actual_end", code="PERIOD_INVALID")
    before = snapshot(d)
    d.state = State.TERMINATED
    d.actual_end = at
    d.end_reason = reason
    d.finished_by_user_id = ctx.actor.user_id
    d.finished_entered_at = timezone.now()
    _save(ctx, d, "delivery.terminated", before)
    return d


def _cancel(ctx: CommandContext, d: Delivery, reason: str) -> None:
    before = snapshot(d)
    d.state = State.CANCELLED
    d.end_reason = reason
    _save(ctx, d, "delivery.cancelled", before)
    money.recharge(ctx, d, "delivery_cancelled")


def cancel(ctx: CommandContext, d: Delivery, reason: str) -> Delivery:
    """«Cancelled» before the movement started (§ 15.3.9)."""
    if d.state != State.PLANNED:
        raise errors.business_rule("DELIVERY_ALREADY_STARTED", state=d.state)
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    orders.ensure_editable(d.company_id, d.order_id)
    _cancel(ctx, d, reason)
    return d


@dataclass(frozen=True)
class Record:
    """A move that already happened, entered in one action (decision Д-1, § 15.1.3)."""

    vehicle_card_id: UUID
    to: Point
    driver_id: UUID
    actual_start: datetime
    actual_end: datetime
    result: str
    comment: str = ""
    order_id: UUID | None = None


def record(ctx: CommandContext, company: Company, data: Record) -> Delivery:
    if data.actual_end <= data.actual_start:
        raise errors.validation_error("actual_end", code="PERIOD_INVALID")
    _not_future(data.actual_end, "actual_end")
    card = fleet.lock_card(company.id, data.vehicle_card_id)
    _check_vehicle(card)
    d = Delivery(company=company, vehicle_card=card, created_by_user_id=ctx.actor.user_id)
    _apply_route(
        d,
        card,
        NewDelivery(
            vehicle_card_id=card.id,
            to=data.to,
            from_point=None,
            planned_start=None,
            planned_end=None,
        ),
    )
    d.order = _order(ctx, company.id, data.order_id)
    d.number = next_number(company.id, "delivery")
    d.driver_id = _employee(company.id, data.driver_id)
    d.created_at = timezone.now()
    # Saved in transit first so that the same checks as for a live start apply.
    d.state = State.IN_TRANSIT
    busy = [c for c in moves.busy_reasons(company.id, card.id) if c != "in_transit"]
    if busy:
        raise errors.business_rule("VEHICLE_BUSY", reasons=busy)
    d.actual_start = data.actual_start
    d.started_by_user_id = ctx.actor.user_id
    try:
        with transaction.atomic():
            d.save()
    except IntegrityError as exc:
        raise errors.business_rule("VEHICLE_IN_TRANSIT", field="vehicle_card_id") from exc
    ctx.audit("delivery.created", object_type="delivery", object_id=d.id, after=snapshot(d))
    return finish(ctx, d, data.actual_end, data.result, data.comment)


def attach(ctx: CommandContext, d: Delivery, order_id: UUID) -> Delivery:
    """A delivery made without a client goes to the chosen client order before it starts; the
    link and its change stay in the history (§ 15.1.6; decision Д-9)."""
    if d.state != State.PLANNED:
        raise errors.business_rule("DELIVERY_ALREADY_STARTED", state=d.state)
    current = orders.get_order(d.company_id, d.order_id)
    if not current.is_internal:
        raise errors.business_rule("DELIVERY_IN_CLIENT_ORDER")
    target = orders.open_order_of_client(d.company_id, order_id)
    before = snapshot(d)
    d.order = target
    _save(ctx, d, "delivery.attached", before)
    ctx.audit(
        "order.delivery_moved_in",
        object_type="order",
        object_id=target.id,
        after={"delivery": str(d.id), "from_order": str(current.id)},
    )
    return d


# --- Transitions with rentals and the service (§ 14.6.3, § 15.3.1–15.3.3, § 16.2.8) -------------
#
# Inside one order the operations are matched by the same vehicle, the direction of the transfer
# and compatible states; with exactly one match the transition is automatic, otherwise nothing is
# changed and the employee chooses by hand (decision Д-7).


def _one(query: QuerySet[Delivery]) -> Delivery | None:
    found = list(query.select_for_update()[:2])
    return found[0] if len(found) == 1 else None


def hand_over_on_issue(
    ctx: CommandContext, company_id: UUID, order_id: UUID, card_id: UUID, at: datetime
) -> Delivery | None:
    """The issue of a rental of the order completes the delivery of the vehicle to the client
    that is on the way: «handed to the client» at the moment of the issue (§ 14.6.3)."""
    d = _one(
        deliveries_of(company_id).filter(
            order_id=order_id,
            vehicle_card_id=card_id,
            state=State.IN_TRANSIT,
            to_kind=Kind.OTHER,
        )
    )
    if d is None or (d.actual_start is not None and at < d.actual_start):
        return None
    return finish(ctx, d, at, Result.HANDED_TO_CLIENT, "")


def start_pickup_on_return(
    ctx: CommandContext, company_id: UUID, order_id: UUID, card_id: UUID, at: datetime
) -> Delivery | None:
    """The vehicle handed back by the client at an external address starts the planned pickup of
    the same order, when its driver is known (§ 15.3.3)."""
    d = _one(
        deliveries_of(company_id).filter(
            order_id=order_id,
            vehicle_card_id=card_id,
            state=State.PLANNED,
            from_kind=Kind.OTHER,
            driver__isnull=False,
        )
    )
    if d is None:
        return None
    return start(ctx, d, at)


def arrive_on_acceptance(
    ctx: CommandContext,
    company_id: UUID,
    order_id: UUID,
    card_id: UUID,
    location_id: UUID,
    at: datetime,
) -> Delivery | None:
    """Accepting the vehicle into a service completes the delivery of the same order that brings
    it there: «accepted in the location» (§ 16.2.8)."""
    d = _one(
        deliveries_of(company_id).filter(
            order_id=order_id,
            vehicle_card_id=card_id,
            state=State.IN_TRANSIT,
            to_kind=Kind.LOCATION,
            to_location_id=location_id,
        )
    )
    if d is None or (d.actual_start is not None and at < d.actual_start):
        return None
    return finish(ctx, d, at, Result.ACCEPTED_IN_LOCATION, "")
