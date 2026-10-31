"""Characteristics of a shared global vehicle (decision of the customer В-12.2, 31.10.2026;
ТЗ § 8.2.1, § 8.3.2).

* The unconfirmed characteristics of a shared vehicle are changed only by its owner — the company
  or the person of the current confirmed ownership. Confirmed ones are changed only by the c4rsys
  staff (§ 8.2.1, `c4rsys.fleet.confirmations`).
* A company that is not the owner (it manages the vehicle or keeps a client card) changes only
  the working value of its own card (`VehicleCardWorkingValue`, origin «card»).
* When the owner changes a value, the cards of the other companies do not change: each keeps the
  value it showed (origin «previous») with the mark that the owner's data are newer, until the
  company applies them by its explicit request («update the data»), per card, in the journal.
  Past operations keep what they recorded.
* Before any owner is confirmed, the company with an active own card of the vehicle counts as the
  owner when it is the only one. If no company or several companies have such a card, nobody
  changes the shared value: every company keeps its value in its card (OPEN, BLOCK12_PLAN).
* A vehicle that only one company works with and that has no other confirmed owner is not shared:
  that company changes the global values as before.
* TODO(spec): a person-owner changes the characteristics in the personal cabinet; the cabinet
  garage (`c4rsys.cabinet.garage_views`) does not edit characteristics yet.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from c4rsys.fleet import characteristics, identity
from c4rsys.fleet.models import Vehicle, VehicleCard, VehicleCardWorkingValue, VehicleOwnership
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

Origin = VehicleCardWorkingValue.Origin
APPLIED = "applied"


def _cards(entity_id: UUID) -> list[VehicleCard]:
    return list(
        VehicleCard.objects.filter(vehicle_id__in=identity.family(entity_id)).exclude(
            state=VehicleCard.State.MERGED
        )
    )


def owner_of(entity_id: UUID) -> dict[str, Any] | None:
    """Who changes the shared values: {"company": id} or {"user": id}; None — nobody."""
    current = VehicleOwnership.objects.filter(vehicle_id=entity_id, ended_at__isnull=True).first()
    if current is not None:
        if current.owner_company_id is not None:
            return {"company": current.owner_company_id}
        return {"user": current.owner_user_id}
    owning = {
        c.company_id
        for c in _cards(entity_id)
        if c.state == VehicleCard.State.ACTIVE and c.relation == VehicleCard.Relation.OWN
    }
    if len(owning) == 1:
        return {"company": owning.pop()}
    return None


def is_shared(entity_id: UUID, company_id: UUID) -> bool:
    """Whether someone else works with the vehicle: another company's active card or another
    confirmed owner."""
    if any(
        c.company_id != company_id and c.state == VehicleCard.State.ACTIVE
        for c in _cards(entity_id)
    ):
        return True
    current = VehicleOwnership.objects.filter(vehicle_id=entity_id, ended_at__isnull=True).first()
    return current is not None and current.owner_company_id != company_id


def changes_global(card: VehicleCard) -> bool:
    """Whether the company of the card changes the global unconfirmed values (В-12.2)."""
    entity = identity.resolve(card.vehicle_id)
    if not is_shared(entity, card.company_id):
        return True
    owner = owner_of(entity)
    return owner is not None and owner.get("company") == card.company_id


def mark_owner_change(
    vehicle: Vehicle, name: str, earlier: Any, company_id: UUID | None, now: datetime
) -> int:
    """The owner changed `name` of the global vehicle from `earlier`: the cards of the other
    companies keep what they showed, with the mark that the owner's data are newer."""
    new = characteristics.value_of(vehicle, name)
    marked = 0
    for card in _cards(identity.resolve(vehicle.id)):
        if card.company_id == company_id:
            continue
        kept = (
            VehicleCardWorkingValue.objects.select_for_update()
            .filter(vehicle_card=card, field=name, ended_at__isnull=True)
            .first()
        )
        if kept is not None:
            if kept.origin == Origin.PROPOSAL:
                continue
            if kept.value == new:
                kept.ended_at = now
                kept.save(update_fields=["ended_at"])
                continue
            kept.owner_newer_at = now
            kept.save(update_fields=["owner_newer_at"])
        else:
            VehicleCardWorkingValue.objects.create(
                company_id=card.company_id,
                vehicle_card=card,
                field=name,
                value=earlier,
                origin=Origin.PREVIOUS,
                owner_newer_at=now,
                created_at=now,
            )
        marked += 1
    return marked


def owner_newer(card: VehicleCard) -> list[str]:
    """The characteristics where the owner's data are newer than the card's."""
    return sorted(
        VehicleCardWorkingValue.objects.filter(
            vehicle_card=card, ended_at__isnull=True, owner_newer_at__isnull=False
        ).values_list("field", flat=True)
    )


def apply_owner_data(
    ctx: CommandContext, company_id: UUID, card_id: UUID, expected_version: int, now: datetime
) -> VehicleCard:
    """«Update the data»: the card takes the owner's current values where they are newer; the
    earlier values of the card stay in its history and in the journal (В-12.2)."""
    card = (
        VehicleCard.objects.select_for_update()
        .select_related("vehicle")
        .filter(company_id=company_id, pk=card_id)
        .first()
    )
    if card is None:
        raise errors.not_found()
    if card.version != expected_version:
        raise errors.version_conflict()
    if card.state != VehicleCard.State.ACTIVE:
        raise errors.business_rule(
            "VEHICLE_ARCHIVED"
            if card.state == VehicleCard.State.ARCHIVED
            else "VEHICLE_CARD_MERGED"
        )
    vehicle = Vehicle.objects.select_for_update().get(pk=card.vehicle_id)
    rows = list(
        VehicleCardWorkingValue.objects.select_for_update().filter(
            vehicle_card=card, ended_at__isnull=True, owner_newer_at__isnull=False
        )
    )
    if not rows:
        raise errors.business_rule("VEHICLE_OWNER_DATA_NOT_NEWER")
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for row in rows:
        before[row.field] = row.value
        after[row.field] = characteristics.value_of(vehicle, row.field)
        row.ended_at = now
        row.ended_reason = APPLIED
        row.save(update_fields=["ended_at", "ended_reason"])
    card.version += 1
    card.save(update_fields=["version", "updated_at"])
    ctx.audit(
        "vehicle.owner_data_applied",
        object_type="vehicle_card",
        object_id=card.id,
        before={"working_values": before},
        after={"values": after},
    )
    return card


def card_values(card: VehicleCard) -> dict[str, Any]:
    """The characteristics as the card shows them: its working values over the global ones; for
    the records a company makes from its card (documents)."""
    described = characteristics.describe(card.vehicle)
    for row in VehicleCardWorkingValue.objects.filter(vehicle_card=card, ended_at__isnull=True):
        if row.field in ("make", "model"):
            described[row.field] = (row.value or {}).get("code") or None
            described[f"{row.field}_name"] = characteristics.display(row.field, row.value)
        else:
            described[row.field] = row.value
    return described
