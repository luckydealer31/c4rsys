"""Identity of vehicles: conflicts of identifiers and one global entity (ТЗ § 8.1.14, § 8.2.8,
§ 8.3.2, § 8.6.1–8.6.4, § 24.1.5).

* Normalization (§ 8.6.1): the VIN and the numbers of the body, chassis and frame are upper-cased
  and trimmed (similar symbols are not replaced); a plate is compared within its country and,
  when both are given, its region. PROPOSAL (kept from block 6): spaces and dashes of a plate are
  removed for the comparison. The engine number is not compared (decision of 30.10.2026).
* A match between different global vehicles is recorded as a conflict with the original and the
  normalized value of each card, the moment of detection and the state of the review. It is a
  warning: the card is created and kept (§ 8.6.3–8.6.4).
* A company sees only the fact of a match and the number of the conflict; the other parties, their
  companies, owners, documents, history and location are not shown (§ 8.6.1). Its own other cards
  in the conflict are its own data and are shown (PROPOSAL).
* The system verification decides (§ 8.6.2, § 24.1.5): one physical vehicle — the cards are
  linked to one global entity, the other entity stays as an archive reference; different vehicles
  — a confirmed exception, the same match between them is not opened again; wrong data — the
  conflict stays on the records until a company corrects them; not enough evidence — it stays
  open. When the values no longer match the system closes the conflict. A revision is a new
  decision; the earlier ones are kept. The parties are notified without others' data.
* One global vehicle is in the operational fleet of one company at a time (§ 8.3.2); service
  cards of other companies are allowed.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import connection
from django.db.models import Q

from c4rsys.fleet import characteristics
from c4rsys.fleet.models import (
    IdentifierConflict,
    IdentifierConflictDecision,
    IdentifierConflictParty,
    IdentifierException,
    Vehicle,
    VehicleCard,
    VehicleCardWorkingValue,
    VehicleCharacteristicProposal,
    VehicleOwnerRequest,
    VehicleOwnership,
    VehiclePlate,
)
from c4rsys.platform import errors

Kind = IdentifierConflict.Kind
Review = IdentifierConflict.Review
Outcome = IdentifierConflictDecision.Outcome
OPERATIONAL = (VehicleCard.Relation.OWN, VehicleCard.Relation.MANAGED)
DECIDED_EVENT = "vehicle.identifier_conflict_decided"


@dataclass(frozen=True)
class Identifier:
    kind: str
    value: str
    # The key of the conflict: the country of a plate; empty for the other kinds.
    country: str
    region: str
    original: str
    # For the numbers: the country of the vehicle (its current plate) for the comparison.
    vehicle_country: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.value, self.country)


def resolve(vehicle_id: UUID) -> UUID:
    """The main global entity of a vehicle linked as a system duplicate (§ 8.6.2)."""
    current = vehicle_id
    for _ in range(10):
        parent = Vehicle.objects.filter(pk=current).values_list("merged_into_id", flat=True).first()
        if parent is None:
            return current
        current = parent
    return current


def family(entity_id: UUID) -> list[UUID]:
    """The entity and its archive references."""
    return [
        entity_id,
        *Vehicle.objects.filter(merged_into_id=entity_id).values_list("id", flat=True),
    ]


def _plate(vehicle: Vehicle) -> VehiclePlate | None:
    return VehiclePlate.objects.filter(vehicle=vehicle, valid_to__isnull=True).first()


def identifiers(vehicle: Vehicle, originals: dict[str, str] | None = None) -> list[Identifier]:
    given = originals or {}
    plate = _plate(vehicle)
    country = plate.country if plate else ""
    found: list[Identifier] = []
    if vehicle.vin:
        found.append(Identifier(Kind.VIN, vehicle.vin, "", "", given.get("vin", vehicle.vin)))
    if plate is not None and plate.number_normalized:
        found.append(
            Identifier(
                Kind.PLATE, plate.number_normalized, plate.country, plate.region, plate.number
            )
        )
    for key in characteristics.NUMBERS:
        value = getattr(vehicle, key)
        if value:
            found.append(Identifier(key, value, "", "", given.get(key, value), country))
    return found


def _excepted(vehicle_id: UUID, ident: Identifier) -> set[UUID]:
    rows = IdentifierException.objects.filter(
        kind=ident.kind,
        normalized_value=ident.value,
        country=ident.country,
        revoked_at__isnull=True,
    ).filter(Q(vehicle_a_id__in=family(vehicle_id)) | Q(vehicle_b_id__in=family(vehicle_id)))
    mine = set(family(vehicle_id))
    others: set[UUID] = set()
    for row in rows:
        other = row.vehicle_b_id if row.vehicle_a_id in mine else row.vehicle_a_id
        others.add(resolve(other))
    return others


def candidates(vehicle: Vehicle, ident: Identifier) -> list[Vehicle]:
    """Other global entities with the same identifier and no confirmed exception (§ 8.6.1)."""
    base = Vehicle.objects.filter(merged_into__isnull=True).exclude(pk=vehicle.pk)
    if ident.kind == Kind.VIN:
        found = list(base.filter(vin=ident.value)[:50])
    elif ident.kind == Kind.PLATE:
        plates = VehiclePlate.objects.filter(
            number_normalized=ident.value, country=ident.country, valid_to__isnull=True
        )
        if ident.region:
            # Both regions given and different: not the same registration.
            plates = plates.filter(Q(region="") | Q(region__iexact=ident.region))
        found = list(base.filter(pk__in=plates.values("vehicle_id"))[:50])
    else:
        found = []
        for other in base.filter(**{ident.kind: ident.value})[:50]:
            plate = _plate(other)
            theirs = plate.country if plate else ""
            # Registered in different known countries: not a match (§ 8.6.1).
            if ident.vehicle_country and theirs and theirs != ident.vehicle_country:
                continue
            found.append(other)
    excepted = _excepted(vehicle.id, ident)
    return [v for v in found if v.id not in excepted]


def matches(vehicle: Vehicle) -> list[str]:
    """Kinds of identifiers that match another global entity: the fact only."""
    if vehicle.merged_into_id is not None:
        return []
    return [i.kind for i in identifiers(vehicle) if candidates(vehicle, i)]


def _next_number() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('vehicle_identifier_conflict_number_seq')")
        row = cursor.fetchone()
    return int(row[0])


def _original(vehicle: Vehicle, kind: str) -> str:
    if kind == Kind.PLATE:
        plate = _plate(vehicle)
        return plate.number if plate else ""
    return str(getattr(vehicle, kind) or "")


def _join(conflict: IdentifierConflict, vehicle: Vehicle, original: str, now: datetime) -> None:
    cards = list(
        VehicleCard.objects.filter(vehicle=vehicle).exclude(state=VehicleCard.State.MERGED)
    )
    active = IdentifierConflictParty.objects.filter(
        conflict=conflict, vehicle=vehicle, left_at__isnull=True
    )
    present = set(active.values_list("vehicle_card_id", flat=True))
    targets: list[VehicleCard | None] = list(cards) if cards else [None]
    for card in targets:
        if (card.id if card else None) in present:
            continue
        IdentifierConflictParty.objects.create(
            conflict=conflict,
            vehicle=vehicle,
            company_id=card.company_id if card else None,
            vehicle_card=card,
            original_value=original[:60],
            normalized_value=conflict.normalized_value,
            joined_at=now,
        )
    if cards:
        # A party of the vehicle alone is replaced by its cards.
        active.filter(vehicle_card__isnull=True).update(left_at=now)


def _entities(conflict: IdentifierConflict) -> set[UUID]:
    vehicles = IdentifierConflictParty.objects.filter(
        conflict=conflict, left_at__isnull=True
    ).values_list("vehicle_id", flat=True)
    return {resolve(v) for v in set(vehicles)}


def _close(conflict: IdentifierConflict, review: str, now: datetime) -> None:
    conflict.is_open = False
    conflict.review = review
    conflict.closed_at = now
    conflict.save(update_fields=["is_open", "review", "closed_at"])


def close_if_resolved(conflict: IdentifierConflict, now: datetime) -> bool:
    """The cause is removed — fewer than two different entities still match: the system closes
    the conflict with its own decision (§ 8.6.2)."""
    if not conflict.is_open or len(_entities(conflict)) >= 2:
        return False
    IdentifierConflictDecision.objects.create(
        conflict=conflict,
        outcome=Outcome.CAUSE_REMOVED,
        revises=_last(conflict),
        decided_at=now,
    )
    _close(conflict, Review.CAUSE_REMOVED, now)
    return True


def _last(conflict: IdentifierConflict) -> IdentifierConflictDecision | None:
    return (
        IdentifierConflictDecision.objects.filter(conflict=conflict).order_by("-decided_at").first()
    )


def refresh(
    vehicle: Vehicle, now: datetime, originals: dict[str, str] | None = None
) -> list[IdentifierConflict]:
    """Records the matches of the vehicle after it was created or changed: it leaves the
    conflicts its values no longer match and joins (or opens) one per matching identifier.
    Run inside the caller's command."""
    if vehicle.merged_into_id is not None:
        return []
    current = identifiers(vehicle, originals)
    keys = {i.key for i in current}
    touched: list[IdentifierConflict] = []
    for party in IdentifierConflictParty.objects.select_related("conflict").filter(
        vehicle=vehicle, left_at__isnull=True, conflict__is_open=True
    ):
        conflict = party.conflict
        if (conflict.kind, conflict.normalized_value, conflict.country) in keys:
            continue
        party.left_at = now
        party.save(update_fields=["left_at"])
        close_if_resolved(conflict, now)
        touched.append(conflict)
    for ident in current:
        others = candidates(vehicle, ident)
        if not others:
            continue
        found = (
            IdentifierConflict.objects.select_for_update()
            .filter(
                kind=ident.kind,
                normalized_value=ident.value,
                country=ident.country,
                region="",
                is_open=True,
            )
            .first()
        )
        conflict = found or IdentifierConflict.objects.create(
            number=_next_number(),
            kind=ident.kind,
            normalized_value=ident.value,
            country=ident.country,
            detected_at=now,
        )
        _join(conflict, vehicle, ident.original, now)
        for other in others:
            _join(conflict, other, _original(other, ident.kind), now)
        touched.append(conflict)
    return touched


def leave(card_ids: Iterable[UUID], now: datetime) -> None:
    """Cards removed by the cancel of an import (§ 22.2.5) leave their conflicts; a conflict with
    one entity left is closed by the system."""
    parties = IdentifierConflictParty.objects.select_related("conflict").filter(
        vehicle_card_id__in=list(card_ids)
    )
    conflicts = {p.conflict_id: p.conflict for p in parties}
    parties.delete()
    for conflict in conflicts.values():
        if not IdentifierConflictParty.objects.filter(conflict=conflict).exists():
            IdentifierConflictDecision.objects.filter(conflict=conflict).delete()
            conflict.delete()
        else:
            close_if_resolved(conflict, now)


# --- What a company sees (§ 8.6.1) ------------------------------------------------------------


def for_card(card: VehicleCard) -> list[dict[str, Any]]:
    """The conflicts of the card: the fact of the match and the number; of the other parties only
    the company's own cards."""
    result = []
    parties = (
        IdentifierConflictParty.objects.select_related("conflict")
        .filter(vehicle_card=card)
        .order_by("-conflict__is_open", "-joined_at")
    )
    seen: set[UUID] = set()
    for party in parties:
        conflict = party.conflict
        if conflict.id in seen:
            continue
        seen.add(conflict.id)
        own = (
            IdentifierConflictParty.objects.select_related("vehicle_card")
            .filter(conflict=conflict, company_id=card.company_id, left_at__isnull=True)
            .exclude(vehicle_card=card)
        )
        last = _last(conflict)
        result.append(
            {
                "id": str(conflict.id),
                "number": conflict.number,
                "kind": conflict.kind,
                "value": party.original_value,
                "open": conflict.is_open,
                "review": conflict.review,
                "detected_at": conflict.detected_at.isoformat(),
                "closed_at": conflict.closed_at.isoformat() if conflict.closed_at else None,
                "left_at": party.left_at.isoformat() if party.left_at else None,
                "decided_at": last.decided_at.isoformat() if last else None,
                "own_cards": [
                    {"id": str(p.vehicle_card_id), "name": p.vehicle_card.name}
                    for p in own
                    if p.vehicle_card is not None
                ],
            }
        )
    return result


def open_kinds(card: VehicleCard) -> list[str]:
    return sorted(
        set(
            IdentifierConflictParty.objects.filter(
                vehicle_card=card, left_at__isnull=True, conflict__is_open=True
            ).values_list("conflict__kind", flat=True)
        )
    )


def pending_local_merge(card: VehicleCard) -> dict[str, Any] | None:
    """A card left on an archive reference because the company already has a card of the main
    entity: the company merges them itself (§ 8.6.2)."""
    if card.state == VehicleCard.State.MERGED:
        return None
    entity = resolve(card.vehicle_id)
    if entity == card.vehicle_id:
        return None
    others = VehicleCard.objects.filter(company_id=card.company_id, vehicle_id=entity).exclude(
        state=VehicleCard.State.MERGED
    )
    return {
        "vehicle_id": Vehicle.objects.get(pk=entity).public_id,
        "cards": [{"id": str(c.id), "name": c.name} for c in others],
    }


# --- One operational record (§ 8.3.2) --------------------------------------------------------


def operational_companies(entity_id: UUID) -> set[UUID]:
    """Companies whose active own or managed card is of the entity."""
    return set(
        VehicleCard.objects.filter(
            vehicle_id__in=family(resolve(entity_id)),
            state=VehicleCard.State.ACTIVE,
            relation__in=OPERATIONAL,
        ).values_list("company_id", flat=True)
    )


def operational_elsewhere(entity_id: UUID, company_id: UUID) -> bool:
    """Whether another company keeps the vehicle in its operational fleet (§ 8.3.2)."""
    return bool(operational_companies(entity_id) - {company_id})


# --- What the system staff sees and decides (§ 24.1.5) -----------------------------------------


def _vehicle_summary(vehicle: Vehicle) -> dict[str, Any]:
    plate = _plate(vehicle)
    data = characteristics.describe(vehicle)
    return {
        "vehicle_id": vehicle.public_id,
        "entity": Vehicle.objects.get(pk=resolve(vehicle.id)).public_id,
        "make": data["make_name"],
        "model": data["model_name"],
        "year": vehicle.year,
        "vin": vehicle.vin,
        "plate": plate.number if plate else "",
        "plate_country": plate.country if plate else "",
        "plate_region": plate.region if plate else "",
        "body_number": vehicle.body_number,
        "chassis_number": vehicle.chassis_number,
        "frame_number": vehicle.frame_number,
        "confirmed_fields": list(vehicle.confirmed_fields),
        "owners": VehicleOwnership.objects.filter(vehicle_id__in=family(vehicle.id)).count(),
    }


def staff_payload(conflict: IdentifierConflict, *, detail: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(conflict.id),
        "number": conflict.number,
        "kind": conflict.kind,
        "value": conflict.normalized_value,
        "country": conflict.country,
        "open": conflict.is_open,
        "review": conflict.review,
        "detected_at": conflict.detected_at.isoformat(),
        "closed_at": conflict.closed_at.isoformat() if conflict.closed_at else None,
        "entities": len(_entities(conflict)),
    }
    if not detail:
        return body
    parties = (
        IdentifierConflictParty.objects.select_related("vehicle", "vehicle_card", "company")
        .filter(conflict=conflict)
        .order_by("joined_at")
    )
    body["parties"] = [
        {
            "id": str(p.id),
            "vehicle": _vehicle_summary(p.vehicle),
            "company": {"public_id": p.company.public_id, "name": p.company.display_name}
            if p.company
            else None,
            "card": {
                "id": str(p.vehicle_card.id),
                "name": p.vehicle_card.name,
                "relation": p.vehicle_card.relation,
                "state": p.vehicle_card.state,
                "created_at": p.vehicle_card.created_at.isoformat(),
            }
            if p.vehicle_card
            else None,
            "original_value": p.original_value,
            "joined_at": p.joined_at.isoformat(),
            "left_at": p.left_at.isoformat() if p.left_at else None,
        }
        for p in parties
    ]
    body["decisions"] = [
        {
            "id": str(d.id),
            "outcome": d.outcome,
            "reason": d.reason,
            "evidence": d.evidence,
            "primary": d.primary_vehicle.public_id if d.primary_vehicle else None,
            "details": d.details,
            "decided_at": d.decided_at.isoformat(),
            "by_system": d.decided_by_user_id is None,
        }
        for d in IdentifierConflictDecision.objects.select_related("primary_vehicle")
        .filter(conflict=conflict)
        .order_by("decided_at")
    ]
    body["operational_companies"] = len(
        {c for e in _entities(conflict) for c in operational_companies(e)}
    )
    return body


def conflicts(state: str = "open", limit: int = 200) -> list[IdentifierConflict]:
    found = IdentifierConflict.objects.all()
    if state == "open":
        found = found.filter(is_open=True).order_by("detected_at")
    else:
        found = found.filter(is_open=False).order_by("-closed_at")
    return list(found[:limit])


def get(conflict_id: UUID) -> IdentifierConflict:
    found = IdentifierConflict.objects.filter(pk=conflict_id).first()
    if found is None:
        raise errors.not_found()
    return found


def _pairs(entities: list[UUID]) -> list[tuple[UUID, UUID]]:
    ordered = sorted(entities)
    return [(a, b) for i, a in enumerate(ordered) for b in ordered[i + 1 :]]


def decide(
    conflict_id: UUID,
    *,
    outcome: str,
    reason: str,
    evidence: str,
    primary: str | None,
    by_user_id: UUID,
    ticket_id: UUID | None,
    now: datetime,
) -> tuple[IdentifierConflictDecision, dict[str, Any]]:
    """A decision of the system verification inside the staff command; returns the decision and
    the change for the system journal."""
    if outcome not in (
        Outcome.SAME_VEHICLE,
        Outcome.EXCEPTION,
        Outcome.ERROR,
        Outcome.INSUFFICIENT,
    ):
        raise errors.validation_error("outcome")
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    conflict = IdentifierConflict.objects.select_for_update().filter(pk=conflict_id).first()
    if conflict is None:
        raise errors.not_found()
    vehicle_ids = sorted(
        set(
            IdentifierConflictParty.objects.filter(conflict=conflict).values_list(
                "vehicle_id", flat=True
            )
        )
    )
    # A fixed order of the locks, so two decisions on shared vehicles do not wait for each other.
    list(Vehicle.objects.select_for_update().filter(pk__in=vehicle_ids).order_by("id"))
    before = {"open": conflict.is_open, "review": conflict.review}
    previous = _last(conflict)
    if not conflict.is_open:
        # Only a confirmed exception is revised; a link of one physical vehicle is not undone
        # (the global split is not a requirement, DOMAIN_VEHICLE_IDENTITY).
        if conflict.review != Review.EXCEPTION:
            raise errors.business_rule("CONFLICT_CLOSED")
        if (
            IdentifierConflict.objects.filter(
                kind=conflict.kind,
                normalized_value=conflict.normalized_value,
                country=conflict.country,
                region=conflict.region,
                is_open=True,
            )
            .exclude(pk=conflict.pk)
            .exists()
        ):
            raise errors.business_rule("CONFLICT_ANOTHER_OPEN")
    if outcome == Outcome.EXCEPTION and not conflict.is_open and previous is not None:
        raise errors.business_rule("CONFLICT_CLOSED")
    entities = sorted(_entities(conflict))
    if len(entities) < 2 and outcome in (Outcome.SAME_VEHICLE, Outcome.EXCEPTION):
        raise errors.business_rule("CONFLICT_ONE_ENTITY")
    decision = IdentifierConflictDecision(
        conflict=conflict,
        outcome=outcome,
        reason=reason,
        evidence=evidence,
        ticket_id=ticket_id,
        revises=previous,
        decided_by_user_id=by_user_id,
        decided_at=now,
    )
    if not conflict.is_open:
        # The revision revokes the exception and reopens the review.
        decision.save(force_insert=True)
        IdentifierException.objects.filter(
            decision__conflict=conflict, revoked_at__isnull=True
        ).update(revoked_at=now, revoked_by=decision)
        conflict.is_open = True
        conflict.closed_at = None
        conflict.save(update_fields=["is_open", "closed_at"])
    if outcome == Outcome.SAME_VEHICLE:
        if not primary:
            raise errors.validation_error("primary", code="FIELD_REQUIRED")
        main = Vehicle.objects.filter(public_id=primary.strip().upper()).first()
        if main is None or main.id not in entities:
            raise errors.validation_error("primary", code="CONFLICT_PRIMARY_NOT_PARTY")
        decision.primary_vehicle = main
        decision.details = link(main, [e for e in entities if e != main.id], now)
        decision.save()
        _close(conflict, Review.SAME_VEHICLE, now)
    elif outcome == Outcome.EXCEPTION:
        decision.save()
        for a, b in _pairs(entities):
            IdentifierException.objects.create(
                kind=conflict.kind,
                normalized_value=conflict.normalized_value,
                country=conflict.country,
                region=conflict.region,
                vehicle_a_id=a,
                vehicle_b_id=b,
                decision=decision,
                created_at=now,
            )
        decision.details = {"pairs": len(_pairs(entities))}
        decision.save(update_fields=["details"])
        _close(conflict, Review.EXCEPTION, now)
    else:
        decision.save()
        conflict.review = (
            Review.AWAITING_CORRECTION if outcome == Outcome.ERROR else Review.INSUFFICIENT
        )
        conflict.save(update_fields=["review"])
    if outcome == Outcome.SAME_VEHICLE:
        # Other conflicts of the linked entities may have lost their second party.
        for other in IdentifierConflict.objects.filter(
            is_open=True, parties__vehicle_id__in=vehicle_ids
        ).distinct():
            close_if_resolved(other, now)
    _notify(conflict, decision, now)
    after = {
        "outcome": outcome,
        "open": conflict.is_open,
        "review": conflict.review,
        "primary": decision.primary_vehicle.public_id if decision.primary_vehicle else None,
        "details": decision.details,
    }
    return decision, {"before": before, "after": after}


def link(main: Vehicle, others: list[UUID], now: datetime) -> dict[str, Any]:
    """Links the cards of the other entities to the main one (§ 8.6.2): each company keeps its
    relations, documents, operations, finances and access; the other entity stays as an archive
    reference to the main one and its history is not deleted.

    PROPOSAL: empty characteristics of the main entity are filled from the others; a different
    value stays the card's own working value with the mark of the discrepancy (§ 8.2.1); a card
    of a company that already has a card of the main entity stays on its reference until the
    company merges the two itself (§ 8.6.2, § 8.6.6)."""
    entities = [main.id, *others]
    # One global vehicle is operated by one company at a time (§ 8.3.2).
    operating = {c for e in entities for c in operational_companies(e)}
    if len(operating) > 1:
        raise errors.business_rule("VEHICLE_LINK_OPERATIONAL_TWICE", companies=len(operating))
    secondary = [v for e in others for v in family(e)]
    # TODO(spec): the ownership history of two entities is not combined automatically; the
    # entity with the confirmed owners is chosen as the main one (PROPOSAL).
    if VehicleOwnership.objects.filter(vehicle_id__in=secondary).exists():
        raise errors.business_rule("VEHICLE_LINK_SECONDARY_HAS_OWNERS")
    if VehicleOwnerRequest.objects.filter(
        vehicle_id__in=secondary, state=VehicleOwnerRequest.State.OPEN
    ).exists():
        raise errors.business_rule("VEHICLE_LINK_OPEN_OWNER_REQUEST")
    if VehicleCharacteristicProposal.objects.filter(
        vehicle_id__in=secondary, state=VehicleCharacteristicProposal.State.OPEN
    ).exists():
        raise errors.business_rule("VEHICLE_LINK_OPEN_PROPOSAL")
    moved: list[str] = []
    pending: list[str] = []
    filled: list[str] = []
    for other in Vehicle.objects.filter(pk__in=others).order_by("created_at"):
        for name in characteristics.FIELDS:
            theirs = characteristics.value_of(other, name)
            if characteristics.is_empty(theirs):
                continue
            if characteristics.is_empty(characteristics.value_of(main, name)):
                characteristics.set_value(main, name, theirs)
                filled.append(name)
                if name in other.confirmed_fields and name not in main.confirmed_fields:
                    main.confirmed_fields = [*main.confirmed_fields, name]
        if _plate(main) is None and (plate := _plate(other)) is not None:
            VehiclePlate.objects.create(
                vehicle=main,
                number=plate.number,
                number_normalized=plate.number_normalized,
                country=plate.country,
                region=plate.region,
                valid_from=plate.valid_from,
            )
            filled.append("plate")
        main.save()
        Vehicle.objects.filter(merged_into=other).update(merged_into=main)
        other.merged_into = main
        other.merged_at = now
        other.save(update_fields=["merged_into", "merged_at"])
        for card in VehicleCard.objects.select_for_update().filter(vehicle=other):
            if VehicleCard.objects.filter(company_id=card.company_id, vehicle=main).exists():
                pending.append(str(card.id))
                continue
            for name in characteristics.FIELDS:
                theirs = characteristics.value_of(other, name)
                if characteristics.is_empty(theirs) or theirs == characteristics.value_of(
                    main, name
                ):
                    continue
                if VehicleCardWorkingValue.objects.filter(
                    vehicle_card=card, field=name, ended_at__isnull=True
                ).exists():
                    continue
                VehicleCardWorkingValue.objects.create(
                    company_id=card.company_id,
                    vehicle_card=card,
                    field=name,
                    value=theirs,
                    origin=VehicleCardWorkingValue.Origin.LINK,
                    created_at=now,
                )
            card.vehicle = main
            card.version += 1
            card.save(update_fields=["vehicle", "version", "updated_at"])
            moved.append(str(card.id))
    refresh(main, now)
    return {
        "linked": [str(v) for v in others],
        "moved_cards": moved,
        "pending_local_merge": pending,
        "filled": sorted(set(filled)),
    }


def _notify(
    conflict: IdentifierConflict, decision: IdentifierConflictDecision, now: datetime
) -> None:
    """The parties learn the outcome by the number of the conflict only (§ 8.6.2)."""
    from c4rsys.notifications import rules

    parties = IdentifierConflictParty.objects.select_related("vehicle_card").filter(
        conflict=conflict, vehicle_card__isnull=False
    )
    done: set[UUID] = set()
    for party in parties:
        card = party.vehicle_card
        if card is None or card.id in done:
            continue
        done.add(card.id)
        rules.raise_event(
            card.company_id,
            DECIDED_EVENT,
            condition=f"{DECIDED_EVENT}:{decision.id}:{card.id}",
            location_id=card.accounting_location_id,
            object_type="vehicle",
            object_id=card.id,
            params={"number": conflict.number, "outcome": decision.outcome},
            now=now,
            done=True,
        )
