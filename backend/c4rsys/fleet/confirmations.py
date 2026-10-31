"""Confirmed characteristics of a global vehicle and the tools of the c4rsys staff (ТЗ § 8.2.1,
§ 8.1.11, § 8.3.12, § 8.3.16, § 24.1.4).

* An authorized staff member confirms basic factory characteristics, setting the value when
  needed; a confirmed value is not changed by a direct edit of a company (§ 8.2.1).
* The owner or the managing company proposes a correction with a basis; until the decision the
  company may use its own working value in its card, explicitly marked as a discrepancy. The
  staff member accepts — the global value changes — or rejects; the earlier and the new value,
  the initiator, the basis, the decision and the moment are kept (`VehicleCharacteristicEvent`).
  PROPOSAL: «the managing company» is a company with an active own or managed card; a proposal
  by a confirmed owner from the personal cabinet and an attached document come later.
* The catalog of makes and models is extended by the authorized support after a check, and the
  cards with «Other» are linked to the accepted value by hand; that does not merge vehicles
  (§ 8.1.11).
* A confirmed period of ownership is corrected by the verification with a basis and evidence; the
  decision is a new version of the history, the earlier one is kept, and confirmed periods do not
  overlap (§ 8.3.16).
"""

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.fleet import characteristics, identity
from c4rsys.fleet.models import (
    Vehicle,
    VehicleCard,
    VehicleCardWorkingValue,
    VehicleCharacteristicEvent,
    VehicleCharacteristicProposal,
    VehicleMake,
    VehicleModel,
    VehicleOwnership,
    VehicleOwnershipRevision,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

Proposal = VehicleCharacteristicProposal
Action = VehicleCharacteristicEvent.Action
DECIDED_EVENT = "vehicle.characteristic_decided"
PROPOSERS = (VehicleCard.Relation.OWN, VehicleCard.Relation.MANAGED)


def _value(name: str, value: Any) -> dict[str, Any]:
    return {"value": value, "display": characteristics.display(name, value)}


def working_values(card: VehicleCard) -> dict[str, dict[str, Any]]:
    """The card's own values that differ from the global ones (§ 8.2.1)."""
    result = {}
    for w in VehicleCardWorkingValue.objects.filter(vehicle_card=card, ended_at__isnull=True):
        global_value = characteristics.value_of(card.vehicle, w.field)
        result[w.field] = {
            **_value(w.field, w.value),
            "origin": w.origin,
            "proposal_id": str(w.proposal_id) if w.proposal_id else None,
            "global": _value(w.field, global_value),
            "since": w.created_at.isoformat(),
            # В-12.2: the owner changed the value after this one.
            "owner_newer": w.owner_newer_at is not None,
        }
    return result


def proposal_payload(p: Proposal, *, staff: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(p.id),
        "field": p.field,
        "current": _value(p.field, p.current_value),
        "proposed": _value(p.field, p.proposed_value),
        "reason": p.reason,
        "state": p.state,
        "created_at": p.created_at.isoformat(),
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "decision_reason": p.decision_reason,
    }
    if staff:
        body["vehicle_id"] = p.vehicle.public_id
        body["company"] = {"public_id": p.company.public_id, "name": p.company.display_name}
        body["card"] = {"name": p.vehicle_card.name, "relation": p.vehicle_card.relation}
    return body


def proposals_of(card: VehicleCard) -> list[dict[str, Any]]:
    return [
        proposal_payload(p)
        for p in Proposal.objects.filter(vehicle_card=card).order_by("-created_at")[:50]
    ]


def propose(
    ctx: CommandContext,
    company_id: UUID,
    card_id: UUID,
    *,
    name: str,
    data: dict[str, Any],
    reason: str,
    use_working: bool,
) -> Proposal:
    """A correction of a confirmed value proposed by the company (§ 8.2.1)."""
    card = (
        VehicleCard.objects.select_for_update()
        .select_related("vehicle")
        .filter(company_id=company_id, pk=card_id)
        .first()
    )
    if card is None:
        raise errors.not_found()
    if card.state != VehicleCard.State.ACTIVE or card.relation not in PROPOSERS:
        raise errors.business_rule("VEHICLE_PROPOSAL_NOT_ALLOWED")
    vehicle = Vehicle.objects.select_for_update().get(pk=card.vehicle_id)
    if identity.resolve(vehicle.id) != vehicle.id:
        raise errors.business_rule("VEHICLE_PENDING_LOCAL_MERGE")
    if name not in characteristics.FACTORY_FIELDS:
        raise errors.validation_error("field")
    if name not in vehicle.confirmed_fields:
        # An unconfirmed value is changed directly in the card.
        raise errors.business_rule("VEHICLE_CHARACTERISTIC_NOT_CONFIRMED", field="field")
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    value = characteristics.parse_one(name, data, vehicle, timezone.localdate().year)
    current = characteristics.value_of(vehicle, name)
    if value == current:
        raise errors.validation_error("value", code="VEHICLE_PROPOSAL_SAME_VALUE")
    if Proposal.objects.filter(vehicle_card=card, field=name, state=Proposal.State.OPEN).exists():
        raise errors.business_rule("VEHICLE_PROPOSAL_OPEN")
    now = timezone.now()
    proposal = Proposal.objects.create(
        vehicle=vehicle,
        company_id=company_id,
        vehicle_card=card,
        field=name,
        current_value=current,
        proposed_value=value,
        reason=reason,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )
    if use_working:
        VehicleCardWorkingValue.objects.filter(
            vehicle_card=card, field=name, ended_at__isnull=True
        ).update(ended_at=now)
        VehicleCardWorkingValue.objects.create(
            company_id=company_id,
            vehicle_card=card,
            field=name,
            value=value,
            origin=VehicleCardWorkingValue.Origin.PROPOSAL,
            proposal=proposal,
            created_by_user_id=ctx.actor.user_id,
            created_at=now,
        )
    ctx.audit(
        "vehicle.characteristic_proposed",
        object_type="vehicle_card",
        object_id=card.id,
        before={"field": name, "value": current},
        after={
            "field": name,
            "value": value,
            "proposal": str(proposal.id),
            "working_value": use_working,
        },
        reason=reason,
    )
    return proposal


# --- The staff (§ 8.2.1) ---------------------------------------------------------------------


def find_vehicle(public_id: str) -> Vehicle:
    found = Vehicle.objects.filter(public_id=public_id.strip().upper()).first()
    if found is None:
        raise errors.not_found()
    return found


def search(query: str, limit: int = 20) -> list[Vehicle]:
    """By the VHL ID, the VIN or a current plate: the global entities only."""
    text = query.strip().upper()
    if not text:
        return []
    by_plate = Vehicle.objects.filter(
        plates__number_normalized=characteristics.normalize_plate(text),
        plates__valid_to__isnull=True,
    )
    found = (
        Vehicle.objects.filter(public_id=text) | Vehicle.objects.filter(vin=text) | by_plate
    ).distinct()
    return list(found.order_by("created_at")[:limit])


def _party(user_id: UUID | None, company_id: UUID | None) -> dict[str, Any] | None:
    from c4rsys.fleet import disputes

    return disputes._party(user_id, company_id)


def ownership_payload(o: VehicleOwnership) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "owner": _party(o.owner_user_id, o.owner_company_id),
        "started_at": o.started_at.isoformat(),
        "ended_at": o.ended_at.isoformat() if o.ended_at else None,
        "started_approx": o.started_approx,
        "ended_approx": o.ended_approx,
    }


def vehicle_payload(vehicle: Vehicle) -> dict[str, Any]:
    """The global entity for the staff: characteristics, confirmations and their history, the
    owners and the plates. Internal data of the companies are not shown."""
    from c4rsys.companies.models import Company

    events = VehicleCharacteristicEvent.objects.filter(vehicle=vehicle).order_by("-decided_at")
    cards = VehicleCard.objects.filter(vehicle_id__in=identity.family(vehicle.id))
    companies = dict(
        Company.objects.filter(pk__in={c.company_id for c in cards}).values_list(
            "id", "display_name"
        )
    )
    return {
        "vehicle_id": vehicle.public_id,
        "entity": Vehicle.objects.get(pk=identity.resolve(vehicle.id)).public_id,
        "merged_at": vehicle.merged_at.isoformat() if vehicle.merged_at else None,
        "characteristics": characteristics.describe(vehicle),
        "values": {
            name: _value(name, characteristics.value_of(vehicle, name))
            for name in characteristics.FIELDS
        },
        "factory_fields": list(characteristics.FACTORY_FIELDS),
        "confirmed_fields": list(vehicle.confirmed_fields),
        "events": [
            {
                "field": e.field,
                "action": e.action,
                "before": _value(e.field, e.before),
                "after": _value(e.field, e.after),
                "reason": e.reason,
                "decided_at": e.decided_at.isoformat(),
                "proposal_id": str(e.proposal_id) if e.proposal_id else None,
            }
            for e in events[:100]
        ],
        "ownerships": [
            ownership_payload(o)
            for o in VehicleOwnership.objects.filter(vehicle=vehicle).order_by("started_at")
        ],
        "plates": [
            {
                "number": p.number,
                "country": p.country,
                "region": p.region,
                "valid_from": p.valid_from.isoformat(),
                "valid_to": p.valid_to.isoformat() if p.valid_to else None,
            }
            for p in vehicle.plates.order_by("valid_from")
        ],
        # Which companies keep a card and the relation: what the verification needs.
        "cards": [
            {
                "company": companies.get(c.company_id, ""),
                "relation": c.relation,
                "state": c.state,
            }
            for c in cards.order_by("created_at")
        ],
    }


def confirm(
    vehicle_public_id: str,
    *,
    items: list[dict[str, Any]],
    reason: str,
    by_user_id: UUID,
    ticket_id: UUID | None,
    now: datetime,
) -> tuple[Vehicle, list[dict[str, Any]]]:
    """Confirms factory characteristics, each with its value — the current one when not given
    (§ 8.2.1). Runs inside the staff command; returns the changes for the system journal."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    if not items:
        raise errors.validation_error("items", code="FIELD_REQUIRED")
    vehicle = (
        Vehicle.objects.select_for_update().filter(public_id=vehicle_public_id.upper()).first()
    )
    if vehicle is None:
        raise errors.not_found()
    if vehicle.merged_into_id is not None:
        raise errors.business_rule("VEHICLE_IS_ARCHIVE_REFERENCE")
    changes = []
    for item in items:
        name = item.get("field")
        if not isinstance(name, str) or name not in characteristics.FACTORY_FIELDS:
            raise errors.validation_error("field")
        before = characteristics.value_of(vehicle, name)
        after = (
            characteristics.parse_one(name, item, vehicle, now.year)
            if name in item or f"{name}_other" in item
            else before
        )
        if characteristics.is_empty(after):
            raise errors.validation_error(name, code="VEHICLE_CONFIRM_EMPTY")
        characteristics.set_value(vehicle, name, after)
        if name not in vehicle.confirmed_fields:
            vehicle.confirmed_fields = [*vehicle.confirmed_fields, name]
        VehicleCharacteristicEvent.objects.create(
            vehicle=vehicle,
            field=name,
            action=Action.CONFIRMED,
            before=before,
            after=after,
            reason=reason,
            ticket_id=ticket_id,
            decided_by_user_id=by_user_id,
            decided_at=now,
        )
        changes.append({"field": name, "before": before, "after": after})
    vehicle.save()
    _end_equal_working_values(vehicle, now)
    identity.refresh(vehicle, now)
    return vehicle, changes


def _end_equal_working_values(vehicle: Vehicle, now: datetime) -> None:
    """A working value equal to the global one is no longer a discrepancy."""
    for w in VehicleCardWorkingValue.objects.filter(
        vehicle_card__vehicle=vehicle, ended_at__isnull=True
    ):
        if w.value == characteristics.value_of(vehicle, w.field):
            w.ended_at = now
            w.save(update_fields=["ended_at"])


def open_proposals(limit: int = 200) -> list[Proposal]:
    return list(
        Proposal.objects.select_related("vehicle", "company", "vehicle_card")
        .filter(state=Proposal.State.OPEN)
        .order_by("created_at")[:limit]
    )


def decide_proposal(
    proposal_id: UUID,
    *,
    accept: bool,
    reason: str,
    by_user_id: UUID,
    ticket_id: UUID | None,
    now: datetime,
) -> tuple[Proposal, dict[str, Any]]:
    """The staff member accepts or rejects; the working value of the proposal ends with the
    decision (PROPOSAL); the company is notified."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    proposal = (
        Proposal.objects.select_for_update()
        .select_related("vehicle_card", "company")
        .filter(pk=proposal_id)
        .first()
    )
    if proposal is None:
        raise errors.not_found()
    if proposal.state != Proposal.State.OPEN:
        raise errors.business_rule("VEHICLE_PROPOSAL_DECIDED")
    vehicle = Vehicle.objects.select_for_update().get(pk=proposal.vehicle_id)
    before = characteristics.value_of(vehicle, proposal.field)
    if accept:
        characteristics.set_value(vehicle, proposal.field, proposal.proposed_value)
        if proposal.field not in vehicle.confirmed_fields:
            vehicle.confirmed_fields = [*vehicle.confirmed_fields, proposal.field]
        vehicle.save()
    proposal.state = Proposal.State.ACCEPTED if accept else Proposal.State.REJECTED
    proposal.decided_by_user_id = by_user_id
    proposal.decided_at = now
    proposal.decision_reason = reason
    proposal.save(update_fields=["state", "decided_by_user_id", "decided_at", "decision_reason"])
    VehicleCharacteristicEvent.objects.create(
        vehicle=vehicle,
        field=proposal.field,
        action=Action.CORRECTED if accept else Action.REJECTED,
        before=before,
        after=proposal.proposed_value if accept else before,
        proposal=proposal,
        initiator_company_id=proposal.company_id,
        initiator_user_id=proposal.created_by_user_id,
        reason=reason,
        ticket_id=ticket_id,
        decided_by_user_id=by_user_id,
        decided_at=now,
    )
    VehicleCardWorkingValue.objects.filter(proposal=proposal, ended_at__isnull=True).update(
        ended_at=now
    )
    _end_equal_working_values(vehicle, now)
    if accept:
        identity.refresh(vehicle, now)
    _notify(proposal, now)
    after = {"state": proposal.state, "value": characteristics.value_of(vehicle, proposal.field)}
    return proposal, {"before": {"value": before}, "after": after, "vehicle": vehicle.public_id}


def _notify(proposal: Proposal, now: datetime) -> None:
    from c4rsys.notifications import rules

    card = proposal.vehicle_card
    rules.raise_event(
        proposal.company_id,
        DECIDED_EVENT,
        condition=f"{DECIDED_EVENT}:{proposal.id}",
        location_id=card.accounting_location_id,
        object_type="vehicle",
        object_id=card.id,
        params={"field": proposal.field, "state": proposal.state, "vehicle": card.name},
        now=now,
        done=True,
    )


# --- The catalog of makes and models (§ 8.1.11) -----------------------------------------------


def other_values(limit: int = 200) -> list[dict[str, Any]]:
    """Texts entered as «Other», with the vehicles that hold them (VHL IDs only)."""
    groups: dict[tuple[str, str, str, str], list[str]] = {}
    found = (
        Vehicle.objects.filter(merged_into__isnull=True)
        .exclude(make_other="", model_other="")
        .order_by("created_at")
    )
    for v in found[:2000]:
        key = (v.vehicle_type, v.make_id or "", v.make_other, v.model_other)
        groups.setdefault(key, []).append(v.public_id)
    return [
        {
            "vehicle_type": k[0],
            "make": k[1],
            "make_other": k[2],
            "model_other": k[3],
            "vehicles": ids,
        }
        for k, ids in sorted(groups.items(), key=lambda item: -len(item[1]))[:limit]
    ]


def _code(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")[:60]


def add_make(*, name: str, vehicle_type: str, country: str) -> VehicleMake:
    name = name.strip()
    if not name:
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    if vehicle_type not in characteristics.VEHICLE_TYPES[Vehicle.TransportClass.GROUND]:
        raise errors.validation_error("vehicle_type")
    code = f"{vehicle_type}_{_code(name)}"[:60]
    if (
        VehicleMake.objects.filter(code=code).exists()
        or VehicleMake.objects.filter(vehicle_type=vehicle_type, name__iexact=name).exists()
    ):
        raise errors.business_rule("CATALOG_MAKE_EXISTS")
    return VehicleMake.objects.create(
        code=code, vehicle_type=vehicle_type, name=name, country=country.strip()[:100]
    )


def add_model(*, make_code: str, name: str) -> VehicleModel:
    make = VehicleMake.objects.filter(code=make_code).first()
    if make is None:
        raise errors.validation_error("make", code="MAKE_UNKNOWN")
    name = name.strip()
    if not name:
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    code = f"{make.code}_{_code(name)}"[:120]
    if (
        VehicleModel.objects.filter(code=code).exists()
        or VehicleModel.objects.filter(make=make, name__iexact=name).exists()
    ):
        raise errors.business_rule("CATALOG_MODEL_EXISTS")
    return VehicleModel.objects.create(code=code, make=make, name=name)


def link_to_catalog(
    *, vehicles: list[str], make_code: str, model_code: str, now: datetime
) -> list[dict[str, Any]]:
    """Links the chosen vehicles with «Other» to the accepted values by hand; the vehicles
    themselves are not merged (§ 8.1.11)."""
    make = VehicleMake.objects.filter(code=make_code).first()
    if make is None:
        raise errors.validation_error("make", code="MAKE_UNKNOWN")
    model = None
    if model_code:
        model = VehicleModel.objects.filter(code=model_code, make=make).first()
        if model is None:
            raise errors.validation_error("model", code="MODEL_UNKNOWN")
    if not vehicles:
        raise errors.validation_error("vehicles", code="FIELD_REQUIRED")
    changes = []
    for public_id in sorted(set(vehicles)):
        vehicle = Vehicle.objects.select_for_update().filter(public_id=public_id.upper()).first()
        if vehicle is None:
            raise errors.validation_error("vehicles", code="VEHICLE_ID_UNKNOWN", id=public_id)
        if vehicle.make_id not in (None, make.code):
            raise errors.business_rule("CATALOG_OTHER_MAKE", vehicle=vehicle.public_id)
        before = {
            "make": characteristics.value_of(vehicle, "make"),
            "model": characteristics.value_of(vehicle, "model"),
        }
        vehicle.make = make
        vehicle.make_other = ""
        if model is not None:
            vehicle.model = model
            vehicle.model_other = ""
        if not vehicle.vehicle_type:
            vehicle.vehicle_type = make.vehicle_type
        vehicle.save()
        _end_equal_working_values(vehicle, now)
        changes.append(
            {
                "vehicle": vehicle.public_id,
                "before": before,
                "after": {
                    "make": characteristics.value_of(vehicle, "make"),
                    "model": characteristics.value_of(vehicle, "model"),
                },
            }
        )
    return changes


# --- Correction of a confirmed period of ownership (§ 8.3.16) ----------------------------------


def correct_ownership(
    ownership_id: UUID,
    *,
    started_at: datetime,
    ended_at: datetime | None,
    started_approx: bool,
    ended_approx: bool,
    initiator: str,
    reason: str,
    evidence: str,
    by_user_id: UUID,
    ticket_id: UUID | None,
    now: datetime,
) -> tuple[VehicleOwnershipRevision, dict[str, Any]]:
    """A new version of a confirmed period; the earlier version is kept. Confirmed periods do not
    overlap and there is one current owner (§ 8.3.8, § 8.3.16). Other periods are not moved: a
    correction that would overlap one is refused (another period is corrected first)."""
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    if not initiator.strip():
        raise errors.validation_error("initiator", code="FIELD_REQUIRED")
    ownership = VehicleOwnership.objects.select_for_update().filter(pk=ownership_id).first()
    if ownership is None:
        raise errors.not_found()
    Vehicle.objects.select_for_update().filter(pk=ownership.vehicle_id).first()
    if started_at > now or (ended_at is not None and ended_at > now):
        raise errors.validation_error("started_at", code="DATE_IN_FUTURE")
    if ended_at is not None and ended_at <= started_at:
        raise errors.validation_error("ended_at", code="PERIOD_INVALID")
    others = VehicleOwnership.objects.filter(vehicle_id=ownership.vehicle_id).exclude(
        pk=ownership.pk
    )
    for other in others:
        other_end = other.ended_at
        overlaps = (other_end is None or started_at < other_end) and (
            ended_at is None or other.started_at < ended_at
        )
        if overlaps:
            raise errors.business_rule("OWNERSHIP_PERIODS_OVERLAP", period=str(other.id))
    if ended_at is None and others.filter(ended_at__isnull=True).exists():
        raise errors.business_rule("OWNERSHIP_ONE_CURRENT")
    before = ownership_payload(ownership)
    ownership.started_at = started_at
    ownership.ended_at = ended_at
    ownership.started_approx = started_approx
    ownership.ended_approx = ended_approx
    ownership.save(update_fields=["started_at", "ended_at", "started_approx", "ended_approx"])
    after = ownership_payload(ownership)
    revision = VehicleOwnershipRevision.objects.create(
        ownership=ownership,
        vehicle_id=ownership.vehicle_id,
        before=before,
        after=after,
        initiator=initiator.strip()[:300],
        reason=reason,
        evidence=evidence,
        ticket_id=ticket_id,
        decided_by_user_id=by_user_id,
        decided_at=now,
    )
    return revision, {"before": before, "after": after}
