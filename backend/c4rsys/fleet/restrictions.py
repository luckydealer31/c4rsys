"""Manual restriction of a vehicle (ТЗ § 8.8.1–8.8.7, § 8.7.2, § 23.5.5–23.5.6).

* A ban of chosen actions set by hand with a reason from the list and a comment; it is not a
  rental or a service and lives apart from the occupancy by operations (§ 8.8.1, § 8.7.2).
* The system reasons and the company's own ones (§ 8.8.2, § 8.8.7).
* The start and an end or none (§ 8.8.4). With the end reached the restriction ends by itself,
  without an employee (§ 8.8.3); to keep it longer the end is extended before it comes.
* One restriction at a time in the MVP (§ 8.8.5–8.8.6). The reason, the comment and the term are
  changed; it is lifted by hand, also early or without an end.
* Forbidden actions (§ 8.8.7): the issue into a new rental, the start of a delivery or of a usual
  move, the start of use in the chosen contexts. Service, inspection, repair, expenses and the
  history are not blocked. A move to a service is a separate exception with a reason and its own
  right (§ 23.5.6).
* A restriction that begins while an operation runs does not stop it: the responsible gets a
  notification and a task (§ 8.8.7). A future booking is allowed; if it overlaps the restriction
  the conflict is shown, and the issue is blocked while the restriction lasts.
* Setting, changing and lifting are three separate rights (§ 23.5.5); every action is journaled.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import VehicleCard, VehicleRestriction, VehicleRestrictionReason
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

Reason = VehicleRestriction.Reason
SYSTEM_REASONS = [r for r in Reason.values if r != Reason.CUSTOM]
EVENT = "vehicle.restriction_during_operation"
SYSTEM_PROCESS = "vehicle_restrictions"
# A start given a little earlier than the moment of saving is taken as «now».
START_TOLERANCE = timedelta(minutes=5)


@dataclass(frozen=True)
class RestrictionData:
    reason: str
    custom_reason_id: UUID | None
    comment: str
    starts_at: datetime | None
    ends_at: datetime | None
    forbid_rental_issue: bool = False
    forbid_moves: bool = False
    forbidden_contexts: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "custom_reason_id": str(self.custom_reason_id) if self.custom_reason_id else None,
            "comment": self.comment,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "forbid_rental_issue": self.forbid_rental_issue,
            "forbid_moves": self.forbid_moves,
            "forbidden_contexts": sorted(self.forbidden_contexts),
        }


def end_of(r: VehicleRestriction) -> datetime | None:
    """The real end: the lift, else the given end, else none."""
    if r.lifted_at is not None and (r.ends_at is None or r.lifted_at < r.ends_at):
        return r.lifted_at
    return r.ends_at


def state_of(r: VehicleRestriction, now: datetime) -> str:
    end = end_of(r)
    if r.lifted_at is not None and (end is None or end <= now):
        return "lifted"
    if end is not None and end <= now:
        return "ended"
    if r.starts_at > now:
        return "planned"
    return "active"


def _period(r: VehicleRestriction) -> tuple[datetime, datetime | None]:
    end = end_of(r)
    return (r.starts_at, max(end, r.starts_at) if end is not None else None)


def active_at(card_id: UUID, at: datetime) -> VehicleRestriction | None:
    return (
        VehicleRestriction.objects.select_related("custom_reason")
        .filter(vehicle_card_id=card_id, period__contains=at)
        .first()
    )


def overlapping(card_id: UUID, start: datetime, end: datetime | None) -> list[VehicleRestriction]:
    query = VehicleRestriction.objects.select_related("custom_reason").filter(
        vehicle_card_id=card_id, period__overlap=(start, end)
    )
    return list(query.order_by("starts_at"))


def reasons_of(company_id: UUID) -> list[VehicleRestrictionReason]:
    return list(
        VehicleRestrictionReason.objects.filter(
            company_id=company_id, state=VehicleRestrictionReason.State.ACTIVE
        ).order_by("name")
    )


def _forbids(r: VehicleRestriction, action: str, context: str | None) -> bool:
    if action == "rental_issue":
        return r.forbid_rental_issue or "rental" in r.forbidden_contexts
    if action == "move":
        return r.forbid_moves
    if action == "context":
        return context in r.forbidden_contexts
    return False


def payload(r: VehicleRestriction, now: datetime | None = None) -> dict[str, Any]:
    moment = now or timezone.now()
    return {
        "id": str(r.id),
        "reason": r.reason,
        "custom_reason": {"id": str(r.custom_reason_id), "name": r.custom_reason.name}
        if r.custom_reason is not None
        else None,
        "comment": r.comment,
        "starts_at": r.starts_at.isoformat(),
        "ends_at": r.ends_at.isoformat() if r.ends_at else None,
        "forbid_rental_issue": r.forbid_rental_issue,
        "forbid_moves": r.forbid_moves,
        "forbidden_contexts": list(r.forbidden_contexts),
        "lifted_at": r.lifted_at.isoformat() if r.lifted_at else None,
        "lift_comment": r.lift_comment,
        "state": state_of(r, moment),
        "version": r.version,
    }


def card_payload(card: VehicleCard) -> dict[str, Any]:
    """The current or the next restriction and the earlier ones of the card."""
    now = timezone.now()
    items = list(
        VehicleRestriction.objects.select_related("custom_reason")
        .filter(vehicle_card=card)
        .order_by("-starts_at")
    )
    current = next((r for r in items if state_of(r, now) in ("active", "planned")), None)
    return {
        "current": payload(current, now) if current else None,
        "history": [payload(r, now) for r in items if r is not current],
    }


def require_allowed(
    card: VehicleCard, action: str, at: datetime, context: str | None = None
) -> None:
    """Refuses an action the restriction acting at the moment forbids."""
    r = active_at(card.id, at)
    if r is not None and _forbids(r, action, context):
        end = end_of(r)
        raise errors.business_rule(
            "VEHICLE_RESTRICTED",
            field="vehicle_card_id",
            restriction=str(r.id),
            reason=r.reason if r.custom_reason is None else r.custom_reason.name,
            until=end.isoformat() if end else None,
        )


def booking_conflict(
    card_id: UUID, start: datetime | None, end: datetime | None
) -> dict[str, Any] | None:
    """A booking overlapping a restriction that forbids the issue: shown as a conflict; the
    booking itself is allowed (§ 8.8.7)."""
    if start is None:
        return None
    for r in overlapping(card_id, start, end):
        if _forbids(r, "rental_issue", None):
            r_end = end_of(r)
            return {
                "restriction": str(r.id),
                "reason": r.reason if r.custom_reason is None else r.custom_reason.name,
                "starts_at": r.starts_at.isoformat(),
                "ends_at": r_end.isoformat() if r_end else None,
            }
    return None


# --- Commands ------------------------------------------------------------------------------------


def _reason(company_id: UUID, data: RestrictionData) -> VehicleRestrictionReason | None:
    if data.reason == Reason.CUSTOM:
        if data.custom_reason_id is None:
            raise errors.validation_error("custom_reason_id", code="FIELD_REQUIRED")
        found = VehicleRestrictionReason.objects.filter(
            company_id=company_id,
            pk=data.custom_reason_id,
            state=VehicleRestrictionReason.State.ACTIVE,
        ).first()
        if found is None:
            raise errors.validation_error("custom_reason_id", code="RESTRICTION_REASON_UNKNOWN")
        return found
    if data.reason not in SYSTEM_REASONS:
        raise errors.validation_error("reason", code="CHOICE_REQUIRED")
    return None


def _check_actions(data: RestrictionData) -> None:
    if set(data.forbidden_contexts) - fleet.USAGE_CONTEXT_TYPES:
        raise errors.validation_error("forbidden_contexts", code="CONTEXT_TYPE_INVALID")
    if not (data.forbid_rental_issue or data.forbid_moves or data.forbidden_contexts):
        raise errors.validation_error("forbidden_actions", code="RESTRICTION_NOTHING_FORBIDDEN")


def _save(r: VehicleRestriction) -> None:
    r.period = _period(r)
    try:
        with transaction.atomic():
            r.save()
    except IntegrityError as exc:
        raise errors.business_rule("VEHICLE_RESTRICTION_EXISTS") from exc


def _snapshot(r: VehicleRestriction) -> dict[str, Any]:
    return {
        "reason": r.reason,
        "custom_reason": str(r.custom_reason_id) if r.custom_reason_id else None,
        "comment": r.comment,
        "starts_at": r.starts_at,
        "ends_at": r.ends_at,
        "forbid_rental_issue": r.forbid_rental_issue,
        "forbid_moves": r.forbid_moves,
        "forbidden_contexts": list(r.forbidden_contexts),
        "lifted_at": r.lifted_at,
    }


def set_restriction(
    ctx: CommandContext, company_id: UUID, card_id: UUID, data: RestrictionData
) -> VehicleRestriction:
    card = fleet.lock_card(company_id, card_id)
    fleet.require_open_for_new(card)
    if card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_ARCHIVED")
    custom = _reason(company_id, data)
    _check_actions(data)
    now = timezone.now()
    starts_at = data.starts_at or now
    if starts_at < now - START_TOLERANCE:
        # PROPOSAL: a restriction is not set backwards; history is not rewritten.
        raise errors.validation_error("starts_at", code="RESTRICTION_START_IN_PAST")
    starts_at = max(starts_at, now)
    if data.ends_at is not None and data.ends_at <= starts_at:
        raise errors.validation_error("ends_at", code="PERIOD_INVALID")
    # One at a time (§ 8.8.5): a restriction not ended yet is changed, not doubled.
    if any(state_of(r, now) in ("active", "planned") for r in card.restrictions.all()):
        raise errors.business_rule("VEHICLE_RESTRICTION_EXISTS")
    r = VehicleRestriction(
        company_id=company_id,
        vehicle_card=card,
        reason=data.reason,
        custom_reason=custom,
        comment=data.comment,
        starts_at=starts_at,
        ends_at=data.ends_at,
        forbid_rental_issue=data.forbid_rental_issue,
        forbid_moves=data.forbid_moves,
        forbidden_contexts=sorted(set(data.forbidden_contexts)),
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )
    _save(r)
    ctx.audit(
        "vehicle.restriction_set",
        object_type="vehicle_card",
        object_id=card.id,
        after={"restriction": str(r.id), **_snapshot(r)},
    )
    if r.starts_at <= now:
        _tell_running(card, r, now)
    return r


def _locked(
    company_id: UUID, card_id: UUID, restriction_id: UUID, version: int
) -> VehicleRestriction:
    card = fleet.lock_card(company_id, card_id)
    r = (
        VehicleRestriction.objects.select_for_update()
        .filter(company_id=company_id, vehicle_card=card, pk=restriction_id)
        .first()
    )
    if r is None:
        raise errors.not_found()
    if r.version != version:
        raise errors.version_conflict()
    return r


def change_restriction(
    ctx: CommandContext,
    company_id: UUID,
    card_id: UUID,
    restriction_id: UUID,
    data: RestrictionData,
    expected_version: int,
) -> VehicleRestriction:
    """The reason, the comment and the term change (§ 8.8.5); an ended one is not prolonged —
    the end must be extended before it comes (§ 8.8.3). The forbidden actions change too
    (PROPOSAL)."""
    r = _locked(company_id, card_id, restriction_id, expected_version)
    now = timezone.now()
    current = state_of(r, now)
    if current in ("ended", "lifted"):
        raise errors.business_rule("VEHICLE_RESTRICTION_ENDED")
    custom = _reason(company_id, data)
    _check_actions(data)
    before = _snapshot(r)
    starts_at = data.starts_at or r.starts_at
    if current == "active" and starts_at != r.starts_at:
        # The start of a running restriction is a fact.
        raise errors.validation_error("starts_at", code="RESTRICTION_ALREADY_STARTED")
    if current == "planned" and starts_at < now - START_TOLERANCE:
        raise errors.validation_error("starts_at", code="RESTRICTION_START_IN_PAST")
    if data.ends_at is not None and (data.ends_at <= starts_at or data.ends_at <= now):
        raise errors.validation_error("ends_at", code="PERIOD_INVALID")
    r.reason = data.reason
    r.custom_reason = custom
    r.comment = data.comment
    r.starts_at = starts_at
    r.ends_at = data.ends_at
    r.forbid_rental_issue = data.forbid_rental_issue
    r.forbid_moves = data.forbid_moves
    r.forbidden_contexts = sorted(set(data.forbidden_contexts))
    r.version += 1
    _save(r)
    after = _snapshot(r)
    ctx.audit(
        "vehicle.restriction_changed",
        object_type="vehicle_card",
        object_id=card_id,
        before={k: v for k, v in before.items() if after[k] != v},
        after={"restriction": str(r.id), **{k: v for k, v in after.items() if before[k] != v}},
    )
    return r


def lift_restriction(
    ctx: CommandContext,
    company_id: UUID,
    card_id: UUID,
    restriction_id: UUID,
    comment: str,
    expected_version: int,
) -> VehicleRestriction:
    """Lifted by hand, also early or without an end (§ 8.8.5)."""
    r = _locked(company_id, card_id, restriction_id, expected_version)
    now = timezone.now()
    if state_of(r, now) in ("ended", "lifted"):
        raise errors.business_rule("VEHICLE_RESTRICTION_ENDED")
    # A planned one lifted before its start never acts.
    r.lifted_at = r.starts_at if r.starts_at > now else now
    r.lifted_by_user_id = ctx.actor.user_id
    r.lift_comment = comment
    r.version += 1
    _save(r)
    ctx.audit(
        "vehicle.restriction_lifted",
        object_type="vehicle_card",
        object_id=card_id,
        after={"restriction": str(r.id), "lifted_at": now, "comment": comment},
    )
    return r


def add_reason(ctx: CommandContext, company_id: UUID, name: str) -> VehicleRestrictionReason:
    """A reason of the company added to the system list (§ 8.8.7)."""
    name = name.strip()
    if not name:
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    try:
        with transaction.atomic():
            reason = VehicleRestrictionReason.objects.create(
                company_id=company_id, name=name, created_by_user_id=ctx.actor.user_id
            )
    except IntegrityError as exc:
        raise errors.validation_error("name", code="RESTRICTION_REASON_EXISTS") from exc
    ctx.audit(
        "vehicle.restriction_reason_added",
        object_type="vehicle_restriction_reason",
        object_id=reason.id,
        after={"name": name},
    )
    return reason


# --- Running operations and the automatic end (§ 8.8.3, § 8.8.7) ------------------------------


def _tell_running(card: VehicleCard, r: VehicleRestriction, now: datetime) -> int:
    """A restriction that begins while an operation runs does not stop it: the responsible of the
    operation's order gets a notification and a task is put in the queue (§ 8.8.7)."""
    from c4rsys.orders import services as orders
    from c4rsys.tasks import services as tasks

    told = 0
    for op in fleet.pending_operations(card.company_id, card.id, now):
        if not op.get("active"):
            continue
        order_id = op.get("order_id")
        order = orders.get_order(card.company_id, UUID(order_id)) if order_id else None
        condition = f"{EVENT}:{r.id}:{op['kind']}:{op['id']}"
        params: dict[str, Any] = {
            "vehicle": card.name,
            "operation": op["kind"],
            "label": op.get("label", ""),
            "reason": r.reason if r.custom_reason is None else r.custom_reason.name,
        }
        _notify(card, condition, params, order)
        tasks.ensure_system(
            card.company_id,
            event=EVENT,
            condition=condition,
            location_id=card.accounting_location_id,
            object_type="vehicle",
            object_id=card.id,
            params=params,
        )
        told += 1
    r.start_checked_at = now
    VehicleRestriction.objects.filter(pk=r.pk).update(start_checked_at=now)
    return told


def _notify(card: VehicleCard, condition: str, params: dict[str, Any], order: Any) -> None:
    from c4rsys.notifications import rules

    rules.raise_event(
        card.company_id,
        EVENT,
        condition=condition,
        location_id=card.accounting_location_id,
        object_type="vehicle",
        object_id=card.id,
        params=params,
        responsible=[order.responsible_employment_id] if order is not None else [],
        done=True,
    )


def run(now: datetime) -> int:
    """The periodic check (every 5 minutes): a restriction that began meanwhile tells the running
    operations; a restriction whose end came ends by itself and the end is journaled."""
    from c4rsys.platform.models import AuditEvent

    changed = 0
    for r in VehicleRestriction.objects.select_related("vehicle_card", "custom_reason").filter(
        start_checked_at__isnull=True, lifted_at__isnull=True, starts_at__lte=now
    ):
        with transaction.atomic():
            locked = VehicleRestriction.objects.select_for_update().get(pk=r.pk)
            if locked.start_checked_at is None and state_of(locked, now) == "active":
                changed += _tell_running(r.vehicle_card, r, now)
            VehicleRestriction.objects.filter(pk=r.pk).update(start_checked_at=now)
    for r in VehicleRestriction.objects.filter(
        end_journaled_at__isnull=True, lifted_at__isnull=True, ends_at__lte=now
    ):
        with transaction.atomic():
            updated = VehicleRestriction.objects.filter(
                pk=r.pk, end_journaled_at__isnull=True
            ).update(end_journaled_at=now)
            if updated:
                AuditEvent.objects.create(
                    company_id=r.company_id,
                    system_process=SYSTEM_PROCESS,
                    action="vehicle.restriction_ended",
                    object_type="vehicle_card",
                    object_id=str(r.vehicle_card_id),
                    after={"restriction": str(r.id), "ended_at": r.ends_at},
                )
                changed += 1
    return changed
