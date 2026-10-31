"""Lifecycle of a company's vehicle card (ТЗ § 8.1.6, § 8.2.6, § 8.2.11, § 8.3.9–8.3.10,
§ 8.3.17, § 8.4.2, § 8.7.5–8.7.6, § 11.9.5).

* Periods of work (§ 8.7.6): the card's history is split into periods of one relation; a change
  of the relation, the end of the management, an archive and a restore end one and may open the
  next. The identity and the history of the vehicle stay (§ 8.1.6).
* The end of the management (§ 8.3.17): the vehicle returns to the owner in the accounting of the
  relations, without a warehouse; the management stops being confirmed and its history stays.
  Taking a client vehicle into management goes straight to the garage.
* An archive (§ 8.7.5) only after the confirmed ownership or the running management ended and
  without running or future operations; it keeps the initiator, the moment and the reason. A
  restore (§ 8.7.6): the company that again becomes the owner or the manager of the same vehicle
  gets its earlier card back, with a new period; no parallel card.
* A sale through a closed order (§ 8.3.9): the seller's ownership ends; a buyer — a person linked
  with a confirmed profile — gets the vehicle in his personal garage (§ 7.1.2). The seller does not
  undo the transfer alone: he asks the new owner to confirm the error.
* A write-off or a disposal of an own vehicle only from the warehouse, with a special right
  (§ 8.4.2).
* A change of the owner ends the confirmation of the management given by the earlier owner and
  sends a separate request to the new one (§ 8.3.10).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.fleet import disclosure, identity, owner_requests, owners
from c4rsys.fleet import services as fleet
from c4rsys.fleet.models import (
    Vehicle,
    VehicleCard,
    VehicleCardContext,
    VehicleCardPeriod,
    VehicleDisposal,
    VehicleOwnerClaim,
    VehicleOwnership,
    VehicleTransferReversal,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

Relation = VehicleCard.Relation
REASON_MAX = 1000


# --- Periods (§ 8.7.6) ---------------------------------------------------------------------------


def open_period(
    card: VehicleCard, basis: str, reason: str, user_id: UUID | None, now: datetime
) -> VehicleCardPeriod:
    return VehicleCardPeriod.objects.create(
        company_id=card.company_id,
        vehicle_card=card,
        relation=card.relation,
        started_at=now,
        start_basis=basis,
        start_reason=reason,
        started_by_user_id=user_id,
    )


def close_period(
    card: VehicleCard, basis: str, reason: str, user_id: UUID | None, now: datetime
) -> VehicleCardPeriod | None:
    period = VehicleCardPeriod.objects.filter(vehicle_card=card, ended_at__isnull=True).first()
    if period is None:
        return None
    period.ended_at = max(now, period.started_at)
    period.end_basis = basis
    period.end_reason = reason
    period.ended_by_user_id = user_id
    period.save(update_fields=["ended_at", "end_basis", "end_reason", "ended_by_user_id"])
    return period


def periods_payload(card: VehicleCard) -> list[dict[str, Any]]:
    return [
        {
            "relation": p.relation,
            "started_at": p.started_at.isoformat(),
            "ended_at": p.ended_at.isoformat() if p.ended_at else None,
            "start_basis": p.start_basis,
            "start_reason": p.start_reason,
            "end_basis": p.end_basis,
            "end_reason": p.end_reason,
        }
        for p in card.periods.order_by("-started_at")
    ]


# --- History of owners and plates (§ 8.2.6, § 8.2.11) --------------------------------------------


def owners_history(card: VehicleCard) -> list[dict[str, Any]]:
    """The confirmed periods of ownership of the global vehicle. The company sees itself by name;
    another owner only as a person or a company (PROPOSAL: the identity of other owners is not
    disclosed, § 2.2.3)."""
    entities = identity.family(identity.resolve(card.vehicle_id))
    result = []
    for o in VehicleOwnership.objects.filter(vehicle_id__in=entities).order_by("-started_at"):
        mine = o.owner_company_id == card.company_id
        result.append(
            {
                "owner": "this_company" if mine else "company" if o.owner_company_id else "person",
                "name": card.company.display_name if mine else "",
                "started_at": o.started_at.isoformat(),
                "ended_at": o.ended_at.isoformat() if o.ended_at else None,
                "started_approx": o.started_approx,
                "ended_approx": o.ended_approx,
                "basis": o.basis,
                "reversed": o.reversed_at is not None,
            }
        )
    return result


def plates_history(card: VehicleCard) -> list[dict[str, Any]]:
    return [
        {
            "number": p.number,
            "country": p.country,
            "region": p.region,
            "valid_from": p.valid_from.isoformat(),
            "valid_to": p.valid_to.isoformat() if p.valid_to else None,
        }
        for p in card.vehicle.plates.order_by("-valid_from")
    ]


# --- Blockers ------------------------------------------------------------------------------------


def _blockers(card: VehicleCard, now: datetime) -> list[dict[str, Any]]:
    """Running and future operations of the vehicle (§ 8.7.5) and what keeps it in place."""
    from c4rsys.fleet import moves

    found = [
        {k: v for k, v in op.items() if k in ("kind", "id", "label", "active")}
        for op in fleet.pending_operations(card.company_id, card.id, now)
    ]
    found += [{"kind": code} for code in moves.busy_reasons(card.company_id, card.id)]
    return found


def _require_free(card: VehicleCard, now: datetime) -> None:
    found = _blockers(card, now)
    if found:
        raise errors.business_rule("VEHICLE_HAS_OPERATIONS", blockers=found)


def _locked(company_id: UUID, card_id: UUID, expected_version: int) -> VehicleCard:
    card = fleet.lock_card(company_id, card_id)
    if card.version != expected_version:
        raise errors.version_conflict()
    if card.state == VehicleCard.State.MERGED:
        fleet.require_open_for_new(card)
    return card


def _require_reason(reason: str) -> str:
    reason = reason.strip()
    if not reason:
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    return reason


def _archive(
    ctx: CommandContext, card: VehicleCard, basis: str, reason: str, now: datetime
) -> None:
    card.state = VehicleCard.State.ARCHIVED
    card.archived_at = now
    card.archived_by_user_id = ctx.actor.user_id
    card.archive_reason = reason or basis
    card.version += 1
    card.save(
        update_fields=[
            "state",
            "archived_at",
            "archived_by_user_id",
            "archive_reason",
            "version",
            "updated_at",
        ]
    )
    close_period(card, basis, reason, ctx.actor.user_id, now)
    disclosure.end_confirmation(ctx, card, basis, now)


# --- Archive and restore (§ 8.7.5–8.7.6) ---------------------------------------------------------


def archive(
    ctx: CommandContext, company_id: UUID, card_id: UUID, reason: str, expected_version: int
) -> VehicleCard:
    card = _locked(company_id, card_id, expected_version)
    reason = _require_reason(reason)
    if card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_ARCHIVED")
    if card.relation == Relation.OWN:
        # An own vehicle leaves the company by a sale, a write-off or a disposal from the
        # warehouse (§ 8.4.2); they archive the card themselves.
        raise errors.business_rule("VEHICLE_OWNERSHIP_NOT_ENDED")
    if card.relation == Relation.MANAGED:
        raise errors.business_rule("VEHICLE_MANAGEMENT_ACTIVE")
    now = timezone.now()
    _require_free(card, now)
    _archive(ctx, card, "archive", reason, now)
    ctx.audit(
        "vehicle.archived",
        object_type="vehicle_card",
        object_id=card.id,
        after={"state": card.state, "relation": card.relation},
        reason=reason,
    )
    return card


def restore(
    ctx: CommandContext,
    company: Any,
    card_id: UUID,
    *,
    relation: str,
    location_id: UUID | None,
    owner: owners.Owner,
    reason: str,
    expected_version: int,
) -> VehicleCard:
    """The company is again the owner or the manager of the same vehicle (§ 8.7.6): its earlier
    card comes back with a new period instead of a parallel card."""
    card = _locked(company.id, card_id, expected_version)
    reason = _require_reason(reason)
    if card.state != VehicleCard.State.ARCHIVED:
        raise errors.business_rule("VEHICLE_NOT_ARCHIVED")
    if VehicleDisposal.objects.filter(vehicle_card=card).exists():
        raise errors.business_rule("VEHICLE_DISPOSED")
    if relation not in (Relation.OWN, Relation.MANAGED, Relation.CLIENT):
        raise errors.validation_error("relation", code="VALUE_UNKNOWN")
    if relation != Relation.CLIENT:
        if location_id is None:
            raise errors.validation_error("location_id", code="FIELD_REQUIRED")
        locations.require_usable(company.id, location_id, "location_id")
        # One operational accounting of a global vehicle (§ 8.3.2).
        if identity.operational_elsewhere(card.vehicle_id, company.id):
            raise errors.business_rule("VEHICLE_OPERATIONAL_ELSEWHERE")
    now = timezone.now()
    before = {"state": card.state, "relation": card.relation}
    card.state = VehicleCard.State.ACTIVE
    card.relation = relation
    card.accounting_location_id = location_id if relation != Relation.CLIENT else None
    # A sold or written-off vehicle left the warehouse; it comes back into the garage.
    card.storage_context_id = None
    card.stored_at = None
    card.archived_at = None
    card.archived_by_user_id = None
    card.archive_reason = ""
    if relation != Relation.OWN:
        card.acquisition_cost = None
        card.acquisition_currency_id = None
    card.version += 1
    card.save()
    _reset_contexts(card)
    open_period(card, "restored", reason, ctx.actor.user_id, now)
    ctx.audit(
        "vehicle.restored",
        object_type="vehicle_card",
        object_id=card.id,
        before=before,
        after={
            "state": card.state,
            "relation": relation,
            "accounting_location": str(location_id) if location_id else None,
        },
        reason=reason,
    )
    if relation == Relation.OWN or not owners.is_current(card, owner):
        owners.declare(ctx, company, card, owner)
    return card


def _reset_contexts(card: VehicleCard) -> None:
    """A client vehicle has no usage context; an operational one gets the applicable contexts of
    the company if it has none (PROPOSAL)."""
    if card.relation == Relation.CLIENT:
        VehicleCardContext.objects.filter(vehicle_card=card).delete()
        return
    if VehicleCardContext.objects.filter(vehicle_card=card).exists():
        return
    VehicleCardContext.objects.bulk_create(
        VehicleCardContext(company_id=card.company_id, vehicle_card=card, context_type=t)
        for t in sorted(fleet.applicable_contexts(card.company_id))
    )


# --- Relation (§ 8.1.6, § 8.3.17) ----------------------------------------------------------------


def end_management(
    ctx: CommandContext,
    company_id: UUID,
    card_id: UUID,
    *,
    reason: str,
    then: str,
    expected_version: int,
) -> VehicleCard:
    """The vehicle returns to the owner in the accounting of relations, without a warehouse
    (§ 8.3.17). The card is archived or stays as a client vehicle of the service (PROPOSAL: the
    employee chooses)."""
    card = _locked(company_id, card_id, expected_version)
    reason = _require_reason(reason)
    if card.relation != Relation.MANAGED or card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_NOT_MANAGED")
    if then not in ("archive", "client"):
        raise errors.validation_error("then", code="CHOICE_REQUIRED")
    now = timezone.now()
    _require_free(card, now)
    owner_requests.close_open(ctx, card, "management_ended")
    if then == "archive":
        _archive(ctx, card, "management_ended", reason, now)
    else:
        close_period(card, "management_ended", reason, ctx.actor.user_id, now)
        card.relation = Relation.CLIENT
        card.accounting_location_id = None
        card.version += 1
        card.save(update_fields=["relation", "accounting_location", "version", "updated_at"])
        _reset_contexts(card)
        open_period(card, "management_ended", reason, ctx.actor.user_id, now)
    ctx.audit(
        "vehicle.management_ended",
        object_type="vehicle_card",
        object_id=card.id,
        before={"relation": Relation.MANAGED},
        after={"relation": card.relation, "state": card.state, "then": then},
        reason=reason,
    )
    return card


def take_into_management(
    ctx: CommandContext,
    company: Any,
    card_id: UUID,
    *,
    location_id: UUID,
    owner: owners.Owner,
    reason: str,
    expected_version: int,
) -> VehicleCard:
    """A client vehicle given to the company's management without a change of the owner goes
    straight into the garage (§ 8.3.17), bound to a location (§ 8.1.3). One operational
    accounting at a time (§ 8.3.2)."""
    card = _locked(company.id, card_id, expected_version)
    reason = _require_reason(reason)
    if card.relation != Relation.CLIENT or card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_NOT_CLIENT")
    locations.require_usable(company.id, location_id, "location_id")
    if identity.operational_elsewhere(card.vehicle_id, company.id):
        raise errors.business_rule("VEHICLE_OPERATIONAL_ELSEWHERE")
    now = timezone.now()
    close_period(card, "management_started", reason, ctx.actor.user_id, now)
    card.relation = Relation.MANAGED
    card.accounting_location_id = location_id
    card.version += 1
    card.save(update_fields=["relation", "accounting_location", "version", "updated_at"])
    _reset_contexts(card)
    open_period(card, "management_started", reason, ctx.actor.user_id, now)
    ctx.audit(
        "vehicle.relation_changed",
        object_type="vehicle_card",
        object_id=card.id,
        before={"relation": Relation.CLIENT},
        after={"relation": Relation.MANAGED, "accounting_location": str(location_id)},
        reason=reason,
    )
    if not owners.is_current(card, owner):
        owners.declare(ctx, company, card, owner)
    return card


# --- Write-off and disposal (§ 8.4.2) ------------------------------------------------------------


def dispose(
    ctx: CommandContext,
    company_id: UUID,
    card_id: UUID,
    *,
    kind: str,
    reason: str,
    expected_version: int,
) -> VehicleDisposal:
    card = _locked(company_id, card_id, expected_version)
    reason = _require_reason(reason)
    if kind not in VehicleDisposal.Kind.values:
        raise errors.validation_error("kind", code="CHOICE_REQUIRED")
    if card.relation != Relation.OWN:
        raise errors.business_rule("VEHICLE_NOT_OWN")
    if card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule("VEHICLE_ARCHIVED")
    if card.storage_context_id is None:
        # Only through the warehouse (§ 8.4.2).
        raise errors.business_rule("VEHICLE_NOT_IN_WAREHOUSE")
    found = fleet.garage_blockers(company_id, card.id)
    if found:
        raise errors.business_rule("VEHICLE_HAS_OPERATIONS", blockers=found)
    now = timezone.now()
    disposal = VehicleDisposal.objects.create(
        company_id=company_id,
        vehicle_card=card,
        kind=kind,
        reason=reason,
        storage_context_id=card.storage_context_id,
        done_at=now,
        created_by_user_id=ctx.actor.user_id,
    )
    ended = _end_company_ownership(card, now)
    _archive(ctx, card, kind, reason, now)
    ctx.audit(
        "vehicle.disposed",
        object_type="vehicle_card",
        object_id=card.id,
        after={"kind": kind, "disposal": str(disposal.id), "ownership_ended": ended},
        reason=reason,
    )
    return disposal


def _end_company_ownership(card: VehicleCard, now: datetime) -> bool:
    """Ends the confirmed ownership of the company, if it is the current owner."""
    Vehicle.objects.select_for_update().filter(pk=card.vehicle_id).first()
    current = (
        VehicleOwnership.objects.select_for_update()
        .filter(vehicle_id=card.vehicle_id, ended_at__isnull=True)
        .first()
    )
    if current is None or current.owner_company_id != card.company_id:
        return False
    current.ended_at = max(now, current.started_at)
    current.save(update_fields=["ended_at"])
    return True


# --- Sale (§ 8.3.9, § 7.1.2, § 11.9.5) -----------------------------------------------------------


def record_sale(
    ctx: CommandContext,
    company_id: UUID,
    card_id: UUID,
    order_id: UUID,
    buyer_user_id: UUID | None,
) -> dict[str, Any]:
    """A vehicle sold from a warehouse through a closed order: the sale and the end of the
    seller's ownership are recorded (§ 8.3.9). The card leaves the active garage and the
    warehouse; its ID and history stay. A buyer linked with a confirmed personal profile gets
    the vehicle in the personal garage (§ 7.1.2). Another confirmed owner is not replaced:
    nothing changes automatically (§ 8.3.12)."""
    card = fleet.lock_card(company_id, card_id)
    if card.storage_context_id is None:
        raise errors.business_rule("VEHICLE_NOT_IN_WAREHOUSE")
    now = timezone.now()
    Vehicle.objects.select_for_update().filter(pk=card.vehicle_id).first()
    current = (
        VehicleOwnership.objects.select_for_update()
        .filter(vehicle_id=card.vehicle_id, ended_at__isnull=True)
        .first()
    )
    result: dict[str, Any] = {"seller_ownership_ended": False, "buyer_ownership": None}
    if current is not None and current.owner_company_id == company_id:
        current.ended_at = max(now, current.started_at)
        current.save(update_fields=["ended_at"])
        result["seller_ownership_ended"] = True
        current = None
    if current is not None:
        result["other_owner"] = True
    elif buyer_user_id is not None:
        new = VehicleOwnership.objects.create(
            vehicle_id=card.vehicle_id,
            owner_user_id=buyer_user_id,
            started_at=now,
            basis="sale",
            order_id=order_id,
            seller_company_id=company_id,
        )
        result["buyer_ownership"] = str(new.id)
        on_ownership_changed(ctx, card.vehicle_id, user_id=buyer_user_id, company_id=None)
    _archive(ctx, card, "sold", f"order:{order_id}", now)
    ctx.audit(
        "vehicle.sold",
        object_type="vehicle_card",
        object_id=card.id,
        after={"order": str(order_id), "state": card.state, **result},
    )
    return result


def _sale_period(card: VehicleCard) -> VehicleCardPeriod | None:
    return card.periods.filter(end_basis="sold").order_by("-ended_at").first()


def cancel_sale(
    ctx: CommandContext, company_id: UUID, card_id: UUID, order_id: UUID, reason: str
) -> VehicleCard:
    """§ 11.9.5: a wrongly closed sale of a vehicle is cancelled only if the ownership did not
    pass to the buyer, or after the new owner confirmed the error (or the support decided). The
    vehicle returns to the seller's warehouse, the seller's ownership comes back; the original
    history stays."""
    card = fleet.lock_card(company_id, card_id)
    if card.state != VehicleCard.State.ARCHIVED or card.storage_context_id is None:
        raise errors.business_rule("VEHICLE_NOT_SOLD")
    period = _sale_period(card)
    if (
        period is None
        or period.end_reason != f"order:{order_id}"
        or card.archive_reason != (f"order:{order_id}")
    ):
        raise errors.business_rule("VEHICLE_NOT_SOLD")
    Vehicle.objects.select_for_update().filter(pk=card.vehicle_id).first()
    transferred = VehicleOwnership.objects.filter(
        vehicle_id=card.vehicle_id, order_id=order_id, basis="sale", reversed_at__isnull=True
    ).first()
    if transferred is not None:
        raise errors.business_rule("VEHICLE_OWNERSHIP_TRANSFERRED")
    now = timezone.now()
    seller_was_owner = VehicleOwnership.objects.filter(
        vehicle_id=card.vehicle_id, owner_company_id=company_id, ended_at=period.ended_at
    ).exists()
    current = VehicleOwnership.objects.filter(
        vehicle_id=card.vehicle_id, ended_at__isnull=True
    ).first()
    if seller_was_owner and current is None:
        VehicleOwnership.objects.create(
            vehicle_id=card.vehicle_id,
            owner_company_id=company_id,
            started_at=now,
            basis="sale_cancelled",
            order_id=order_id,
        )
    card.state = VehicleCard.State.ACTIVE
    card.archived_at = None
    card.archived_by_user_id = None
    card.archive_reason = ""
    card.version += 1
    card.save()
    open_period(card, "sale_cancelled", reason, ctx.actor.user_id, now)
    ctx.audit(
        "vehicle.sale_cancelled",
        object_type="vehicle_card",
        object_id=card.id,
        after={"order": str(order_id), "state": card.state},
        reason=reason,
    )
    return card


def sold_ownership(card: VehicleCard) -> VehicleOwnership | None:
    """The buyer's current ownership from this company's sale of the card."""
    period = _sale_period(card)
    if period is None or not period.end_reason.startswith("order:"):
        return None
    return VehicleOwnership.objects.filter(
        vehicle_id=card.vehicle_id,
        seller_company_id=card.company_id,
        order_id=period.end_reason.removeprefix("order:"),
        basis="sale",
        ended_at__isnull=True,
    ).first()


def request_reversal(
    ctx: CommandContext, company_id: UUID, card_id: UUID, reason: str
) -> VehicleTransferReversal:
    """The seller asks the new owner to confirm that the transfer was wrong (§ 8.3.9)."""
    card = fleet.lock_card(company_id, card_id)
    reason = _require_reason(reason)
    ownership = sold_ownership(card)
    if ownership is None or ownership.order_id is None:
        raise errors.business_rule("VEHICLE_OWNERSHIP_NOT_TRANSFERRED")
    if VehicleTransferReversal.objects.filter(
        ownership=ownership, state=VehicleTransferReversal.State.OPEN
    ).exists():
        raise errors.business_rule("REVERSAL_PENDING")
    reversal = VehicleTransferReversal.objects.create(
        company_id=company_id,
        vehicle_card=card,
        vehicle_id=card.vehicle_id,
        ownership=ownership,
        order_id=ownership.order_id,
        reason=reason,
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "vehicle.transfer_reversal_requested",
        object_type="vehicle_card",
        object_id=card.id,
        after={"reversal": str(reversal.id), "order": str(ownership.order_id)},
        reason=reason,
    )
    return reversal


def reversals_for_user(user_id: UUID) -> list[VehicleTransferReversal]:
    return list(
        VehicleTransferReversal.objects.select_related("company", "vehicle")
        .filter(ownership__owner_user_id=user_id)
        .order_by("-created_at")
    )


def answer_reversal(
    ctx: CommandContext, user_id: UUID, reversal_id: UUID, accept: bool, comment: str
) -> VehicleTransferReversal:
    """The new owner confirms the error — his ownership ends and the vehicle leaves his personal
    garage — or refuses: nothing changes (§ 8.3.9). The answer is kept."""
    peek = VehicleTransferReversal.objects.filter(
        pk=reversal_id, ownership__owner_user_id=user_id
    ).first()
    if peek is None:
        raise errors.not_found()
    Vehicle.objects.select_for_update().filter(pk=peek.vehicle_id).first()
    reversal = VehicleTransferReversal.objects.select_for_update().get(pk=reversal_id)
    if reversal.state != VehicleTransferReversal.State.OPEN:
        raise errors.business_rule("REVERSAL_NOT_OPEN")
    ownership = VehicleOwnership.objects.select_for_update().get(pk=reversal.ownership_id)
    now = timezone.now()
    if accept:
        if ownership.ended_at is not None:
            raise errors.business_rule("REVERSAL_NOT_OPEN")
        ownership.ended_at = max(now, ownership.started_at)
        ownership.reversed_at = now
        ownership.save(update_fields=["ended_at", "reversed_at"])
    reversal.state = (
        VehicleTransferReversal.State.ACCEPTED if accept else VehicleTransferReversal.State.REJECTED
    )
    reversal.answered_by_user_id = user_id
    reversal.answered_at = now
    reversal.answer_comment = comment
    reversal.save(update_fields=["state", "answered_by_user_id", "answered_at", "answer_comment"])
    ctx.audit(
        "vehicle.transfer_reversal_answered",
        object_type="vehicle_card",
        object_id=reversal.vehicle_card_id,
        after={"reversal": str(reversal.id), "accepted": accept, "comment": comment},
        company_id=reversal.company_id,
    )
    return reversal


def reversal_payload(r: VehicleTransferReversal) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "state": r.state,
        "reason": r.reason,
        "created_at": r.created_at.isoformat(),
        "answered_at": r.answered_at.isoformat() if r.answered_at else None,
        "answer_comment": r.answer_comment,
        "company": {"public_id": r.company.public_id, "display_name": r.company.display_name},
        "vehicle_id": r.vehicle.public_id,
    }


def reversals_of_card(card: VehicleCard) -> list[dict[str, Any]]:
    return [
        reversal_payload(r)
        for r in VehicleTransferReversal.objects.select_related("company", "vehicle")
        .filter(vehicle_card=card)
        .order_by("-created_at")
    ]


# --- A change of the owner (§ 8.3.10) ------------------------------------------------------------


def on_ownership_changed(
    ctx: CommandContext, vehicle_id: UUID, *, user_id: UUID | None, company_id: UUID | None
) -> int:
    """At the moment the ownership passes, the confirmation of the management given by the
    earlier owner ends; the working card and the internal history stay, and the new owner gets a
    separate request to confirm the management. Until he accepts he gets no history of the
    company, and the company loses what only the earlier owner gave (the global documents follow
    the current confirmed relation)."""
    entities = identity.family(identity.resolve(vehicle_id))
    told = 0
    for card in VehicleCard.objects.select_for_update().filter(
        Q(vehicle_id__in=entities),
        state=VehicleCard.State.ACTIVE,
        relation=Relation.MANAGED,
    ):
        if company_id is not None and card.company_id == company_id:
            continue
        claim = card.owner_claims.order_by("-created_at").first()
        same = claim is not None and (
            (user_id is not None and claim.owner_user_id == user_id)
            or (company_id is not None and claim.lookup.upper() == _company_public_id(company_id))
        )
        if same:
            continue
        owner_requests.close_open(ctx, card, "ownership_changed")
        new_claim = VehicleOwnerClaim.objects.create(
            company_id=card.company_id,
            vehicle_card=card,
            kind=VehicleOwnerClaim.Kind.PROFILE,
            owner_user_id=user_id,
            source="ownership_change",
            created_by_user_id=ctx.actor.user_id,
        )
        ctx.audit(
            "vehicle.owner_declared",
            object_type="vehicle_card",
            object_id=card.id,
            after={"kind": "profile", "source": "ownership_change", "claim": str(new_claim.id)},
            company_id=card.company_id,
        )
        owner_requests.send_to_new_owner(
            ctx, card, new_claim, user_id=user_id, company_id=company_id
        )
        told += 1
    return told


def _company_public_id(company_id: UUID) -> str:
    from c4rsys.companies.models import Company

    return str(Company.objects.filter(pk=company_id).values_list("public_id", flat=True).first())
