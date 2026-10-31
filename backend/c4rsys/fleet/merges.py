"""Merge of duplicate vehicle cards inside one company and its split (ТЗ § 8.6.5–8.6.6, § 8.6.2).

* The cards must be of one system entity — after the verification found one physical vehicle
  (§ 8.6.2) — and the active operations must be compatible. The employee chooses the main and the
  duplicate card; the comparison shows the fields, locations, relations, restrictions and active
  operations. Equal data move by themselves; a different value is chosen explicitly, or the main
  card's value is kept and the other stays in the history of the duplicate.
* Nothing of other modules is rewritten: the duplicate gets the state «merged», refers to the main
  card and is closed for new operations; its records keep their origin and are shown from the main
  card. The merge needs its own right and a repeated one-time code.
* The split (an elevated right of «Company administrator») is one action: the records made before
  the merge stay with their cards; the records made after it are assigned by hand to one of the
  two cards; the split is confirmed only when all of them are assigned. Nothing is assigned
  automatically. The global identity and the cards of other companies do not change.

PROPOSAL (TODO(spec)) — what is incompatible: the duplicate has active or future operations; the
relations or the odometer units of the cards differ; one of them is in a warehouse. The new
records counted at the split are those of the modules that register them (rentals, service,
deliveries, inspections with their damages, moves and readings); documents, tasks and finance
records made after the merge stay with the main card.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.accounts import otp as account_codes
from c4rsys.accounts import security
from c4rsys.accounts import services as accounts
from c4rsys.companies.models import Company
from c4rsys.fleet import characteristics, identity, owners, services
from c4rsys.fleet.models import (
    IdentifierConflictParty,
    OdometerReading,
    VehicleActionCode,
    VehicleCard,
    VehicleCardContext,
    VehicleCardMerge,
    VehicleCardWorkingValue,
    VehicleMove,
    VehicleOwnerClaim,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

State = VehicleCard.State
MERGE_PURPOSE = "vehicle_merge"

# --- Records of other modules made after a merge -------------------------------------------------

# (company_id, card_id, moment) -> records of the card created at or after the moment as
# {"kind", "id", "label", "at", "parent"?}; a record with a parent follows its parent.
Since = Callable[[UUID, UUID, datetime], list[dict[str, Any]]]
# (ctx, company_id, ids, from_card, to_card) -> ids of the sources of odometer readings that
# move with them.
Reassign = Callable[[CommandContext, UUID, list[UUID], VehicleCard, VehicleCard], list[UUID]]


@dataclass(frozen=True)
class CardRecords:
    kind: str
    since: Since
    reassign: Reassign


_records: dict[str, CardRecords] = {}


def register_card_records(provider: CardRecords) -> None:
    _records[provider.kind] = provider


def _moves_since(company_id: UUID, card_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    return [
        {"kind": "move", "id": str(m.id), "label": "", "at": m.actual_start.isoformat()}
        for m in VehicleMove.objects.filter(
            company_id=company_id, vehicle_card_id=card_id, created_at__gte=moment
        ).order_by("created_at")
    ]


def _moves_reassign(
    ctx: CommandContext, company_id: UUID, ids: list[UUID], source: VehicleCard, target: VehicleCard
) -> list[UUID]:
    VehicleMove.objects.filter(company_id=company_id, vehicle_card=source, pk__in=ids).update(
        vehicle_card=target
    )
    return []


def _readings_since(company_id: UUID, card_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    """Readings entered by themselves (an import); those of an inspection or a service job move
    with their source."""
    return [
        {
            "kind": "reading",
            "id": str(r.id),
            "label": f"{r.value} {r.unit}",
            "at": r.measured_at.isoformat(),
        }
        for r in OdometerReading.objects.filter(
            company_id=company_id, vehicle_card_id=card_id, entered_at__gte=moment
        )
        .exclude(source_type__in=["inspection", "service_job"])
        .order_by("measured_at")
    ]


def _readings_reassign(
    ctx: CommandContext, company_id: UUID, ids: list[UUID], source: VehicleCard, target: VehicleCard
) -> list[UUID]:
    OdometerReading.objects.filter(company_id=company_id, vehicle_card=source, pk__in=ids).update(
        vehicle_card=target
    )
    return []


register_card_records(CardRecords("move", _moves_since, _moves_reassign))
register_card_records(CardRecords("reading", _readings_since, _readings_reassign))


def records_since(company_id: UUID, card_id: UUID, moment: datetime) -> list[dict[str, Any]]:
    return [item for p in _records.values() for item in p.since(company_id, card_id, moment)]


# --- The repeated one-time code (§ 8.6.6) --------------------------------------------------------


def _email(user_id: UUID) -> str:
    emails = accounts.confirmed_emails_of(user_id)
    if not emails:
        raise errors.business_rule("CODE_NO_EMAIL")
    return str(emails[0])


def request_code(ctx: CommandContext, company_id: UUID, subject: str) -> None:
    now = timezone.now()
    holder, _ = VehicleActionCode.objects.select_for_update().get_or_create(
        company_id=company_id, user_id=ctx.actor.user_id, purpose=MERGE_PURPOSE, subject=subject
    )
    code = account_codes.send_contact_code(_email(ctx.actor.user_id), MERGE_PURPOSE, now, None)
    holder.code_hash = security.code_hash(code)
    holder.code_expires_at = now + account_codes.CODE_TTL
    holder.code_attempts = 0
    holder.save(update_fields=["code_hash", "code_expires_at", "code_attempts"])
    ctx.audit(
        "vehicle.merge_code_sent", object_type="vehicle_card", object_id=subject.split(":")[0]
    )


def check_code(
    ctx: CommandContext, company_id: UUID, subject: str, code: str
) -> errors.ApiError | None:
    """A wrong code is counted and returned, not raised, so the count is committed."""
    holder = (
        VehicleActionCode.objects.select_for_update()
        .filter(
            company_id=company_id,
            user_id=ctx.actor.user_id,
            purpose=MERGE_PURPOSE,
            subject=subject,
        )
        .first()
    )
    if holder is None or not holder.code_hash or holder.code_expires_at is None:
        return errors.ApiError("CODE_EXPIRED", status=422, field="code")
    if holder.code_attempts >= account_codes.MAX_ATTEMPTS:
        return errors.ApiError("CODE_ATTEMPTS_EXHAUSTED", status=422, field="code")
    if timezone.now() > holder.code_expires_at:
        return errors.ApiError("CODE_EXPIRED", status=422, field="code")
    if not security.code_matches(code.strip(), holder.code_hash):
        holder.code_attempts += 1
        holder.save(update_fields=["code_attempts"])
        left = account_codes.MAX_ATTEMPTS - holder.code_attempts
        if left <= 0:
            return errors.ApiError("CODE_ATTEMPTS_EXHAUSTED", status=422, field="code")
        return errors.ApiError(
            "CODE_INVALID", status=422, field="code", params={"attempts_left": left}
        )
    holder.code_hash = ""
    holder.code_expires_at = None
    holder.save(update_fields=["code_hash", "code_expires_at"])
    return None


# --- Comparison and merge (§ 8.6.6) --------------------------------------------------------------


def _effective(card: VehicleCard) -> dict[str, Any]:
    working = {
        w.field: w.value
        for w in VehicleCardWorkingValue.objects.filter(vehicle_card=card, ended_at__isnull=True)
    }
    return {
        name: working.get(name, characteristics.value_of(card.vehicle, name))
        for name in characteristics.FIELDS
    }


def _owner(card: VehicleCard) -> dict[str, Any] | None:
    claim = card.owner_claims.order_by("-created_at").first()
    if claim is None:
        return None
    return {
        "kind": claim.kind,
        "lookup": claim.lookup,
        "name": claim.manual_name,
        "contact": claim.manual_contact,
        "client_id": str(claim.client_id) if claim.client_id else "",
    }


def _snapshot(card: VehicleCard) -> dict[str, Any]:
    return {
        "name": card.name,
        "location": str(card.accounting_location_id) if card.accounting_location_id else None,
        "contexts": sorted(services.allowed_contexts(card)),
        "acquisition": None
        if card.acquisition_cost is None
        else {"cost": str(card.acquisition_cost), "currency": card.acquisition_currency_id},
        "owner": _owner(card),
        **{f"characteristic.{k}": v for k, v in _effective(card).items()},
    }


def _active_operations(card: VehicleCard) -> list[dict[str, Any]]:
    now = timezone.now()
    kinds = {"rental", "service", "delivery"}
    found = services.schedule(card.company_id, [card.id], now, now + timedelta(days=3650), kinds)
    return [
        {
            "kind": item["kind"],
            "id": str(item["id"]),
            "start": item["start"].isoformat() if item.get("start") else None,
            "end": item["end"].isoformat() if item.get("end") else None,
            "state": item.get("state"),
        }
        for item in found
        if item.get("end") is None or item["end"] > now
    ]


def _pair(
    company_id: UUID, primary_id: UUID, duplicate_id: UUID
) -> tuple[VehicleCard, VehicleCard]:
    if primary_id == duplicate_id:
        raise errors.validation_error("duplicate_id", code="VEHICLE_MERGE_SAME_CARD")
    locked = {
        c.id: c
        for c in VehicleCard.objects.select_for_update()
        .select_related("vehicle")
        .filter(company_id=company_id, pk__in=[primary_id, duplicate_id])
        .order_by("id")
    }
    if len(locked) != 2:
        raise errors.not_found()
    return locked[primary_id], locked[duplicate_id]


def _obstacles(primary: VehicleCard, duplicate: VehicleCard) -> list[str]:
    found = []
    if primary.state != State.ACTIVE or duplicate.state != State.ACTIVE:
        found.append("card_not_active")
    if identity.resolve(primary.vehicle_id) != identity.resolve(duplicate.vehicle_id):
        # Different system entities are never merged (§ 8.6.6).
        found.append("different_entities")
    if primary.relation != duplicate.relation:
        found.append("relation_differs")
    if primary.odometer_unit != duplicate.odometer_unit:
        found.append("odometer_unit_differs")
    if primary.storage_context_id is not None or duplicate.storage_context_id is not None:
        found.append("in_warehouse")
    if _active_operations(duplicate):
        found.append("duplicate_has_active_operations")
    return found


def comparison(company_id: UUID, primary: VehicleCard, duplicate: VehicleCard) -> dict[str, Any]:
    main, other = _snapshot(primary), _snapshot(duplicate)
    fields = []
    for key in main:
        name = key.split(".", 1)[1] if key.startswith("characteristic.") else key
        if key.startswith("characteristic."):
            shown = (
                characteristics.display(name, main[key]),
                characteristics.display(name, other[key]),
            )
        else:
            shown = (main[key], other[key])
        fields.append(
            {
                "key": key,
                "primary": shown[0],
                "duplicate": shown[1],
                "same": main[key] == other[key],
            }
        )
    return {
        "primary": {"id": str(primary.id), "name": primary.name, "relation": primary.relation},
        "duplicate": {
            "id": str(duplicate.id),
            "name": duplicate.name,
            "relation": duplicate.relation,
        },
        "fields": fields,
        # Manual restrictions come with part Б of block 12 (§ 8.8).
        "restrictions": {"primary": [], "duplicate": []},
        "operations": {
            "primary": _active_operations(primary),
            "duplicate": _active_operations(duplicate),
        },
        "obstacles": _obstacles(primary, duplicate),
    }


def preview(company_id: UUID, primary_id: UUID, duplicate_id: UUID) -> dict[str, Any]:
    cards = {
        c.id: c
        for c in VehicleCard.objects.select_related("vehicle").filter(
            company_id=company_id, pk__in=[primary_id, duplicate_id]
        )
    }
    if primary_id == duplicate_id or len(cards) != 2:
        raise errors.not_found()
    return comparison(company_id, cards[primary_id], cards[duplicate_id])


def candidates(card: VehicleCard) -> list[dict[str, Any]]:
    """Other cards of the company of the same system entity (§ 8.6.2)."""
    entity = identity.resolve(card.vehicle_id)
    found = (
        VehicleCard.objects.filter(
            company_id=card.company_id, vehicle_id__in=identity.family(entity), state=State.ACTIVE
        )
        .exclude(pk=card.id)
        .order_by("created_at")
    )
    return [{"id": str(c.id), "name": c.name, "relation": c.relation} for c in found]


def merge(
    ctx: CommandContext,
    company: Company,
    primary_id: UUID,
    duplicate_id: UUID,
    *,
    choices: dict[str, str],
    reason: str,
) -> VehicleCardMerge:
    primary, duplicate = _pair(company.id, primary_id, duplicate_id)
    obstacles = _obstacles(primary, duplicate)
    if obstacles:
        raise errors.business_rule("VEHICLE_MERGE_NOT_ALLOWED", obstacles=obstacles)
    main, other = _snapshot(primary), _snapshot(duplicate)
    differing = sorted(k for k in main if main[k] != other[k])
    unknown = sorted(set(choices) - set(differing))
    if unknown:
        # The cards changed since the comparison was shown.
        raise errors.business_rule("VEHICLE_MERGE_CHANGED", fields=unknown)
    missing = [k for k in differing if choices.get(k) not in ("primary", "duplicate")]
    if missing:
        raise errors.validation_error(
            "choices", code="VEHICLE_MERGE_CHOICE_REQUIRED", fields=missing
        )
    now = timezone.now()
    taken = {k: other[k] for k in differing if choices[k] == "duplicate"}
    _apply(ctx, company, primary, duplicate, taken, now)
    duplicate.state = State.MERGED
    duplicate.merged_into = primary
    duplicate.merged_at = now
    duplicate.version += 1
    duplicate.save(update_fields=["state", "merged_into", "merged_at", "version", "updated_at"])
    primary.version += 1
    primary.save(update_fields=["version", "updated_at"])
    record = VehicleCardMerge.objects.create(
        company=company,
        primary=primary,
        duplicate=duplicate,
        reason=reason,
        chosen=taken,
        before={"primary": main, "duplicate": other},
        merged_by_user_id=ctx.actor.user_id,
        merged_at=now,
    )
    # The duplicate is no longer a party of the conflicts: the main card stays.
    parties = IdentifierConflictParty.objects.select_related("conflict").filter(
        vehicle_card=duplicate, left_at__isnull=True
    )
    for party in parties:
        party.left_at = now
        party.save(update_fields=["left_at"])
        identity.close_if_resolved(party.conflict, now)
    for card, role in ((primary, "primary"), (duplicate, "duplicate")):
        ctx.audit(
            "vehicle.merged",
            object_type="vehicle_card",
            object_id=card.id,
            before={"state": State.ACTIVE},
            after={
                "role": role,
                "merge": str(record.id),
                "primary": str(primary.id),
                "duplicate": str(duplicate.id),
                "taken": sorted(taken),
            },
            reason=reason,
        )
    return record


def _apply(
    ctx: CommandContext,
    company: Company,
    primary: VehicleCard,
    duplicate: VehicleCard,
    taken: dict[str, Any],
    now: datetime,
) -> None:
    """The values chosen from the duplicate go to the main card."""
    if "name" in taken:
        primary.name = taken["name"]
    if "location" in taken:
        primary.accounting_location_id = duplicate.accounting_location_id
    if "acquisition" in taken:
        primary.acquisition_cost = duplicate.acquisition_cost
        primary.acquisition_currency_id = duplicate.acquisition_currency_id
    primary.save(
        update_fields=[
            "name",
            "accounting_location",
            "acquisition_cost",
            "acquisition_currency",
            "updated_at",
        ]
    )
    if "contexts" in taken:
        _set_contexts(primary, set(taken["contexts"]))
    if "owner" in taken:
        claim = duplicate.owner_claims.order_by("-created_at").first()
        if claim is not None:
            owners.declare(ctx, company, primary, _owner_of(claim))
    for key, value in taken.items():
        if key.startswith("characteristic."):
            _set_working(ctx, primary, key.split(".", 1)[1], value, now)


def _owner_of(claim: VehicleOwnerClaim) -> owners.Owner:
    return owners.Owner(
        kind=claim.kind,
        lookup=claim.lookup,
        name=claim.manual_name,
        contact=claim.manual_contact,
        client_id=claim.client_id,
    )


def _set_contexts(card: VehicleCard, contexts: set[str]) -> None:
    VehicleCardContext.objects.filter(vehicle_card=card).exclude(context_type__in=contexts).delete()
    present = set(
        VehicleCardContext.objects.filter(vehicle_card=card).values_list("context_type", flat=True)
    )
    VehicleCardContext.objects.bulk_create(
        VehicleCardContext(company_id=card.company_id, vehicle_card=card, context_type=t)
        for t in sorted(contexts - present)
    )


def _set_working(
    ctx: CommandContext, card: VehicleCard, name: str, value: Any, now: datetime
) -> None:
    VehicleCardWorkingValue.objects.filter(
        vehicle_card=card, field=name, ended_at__isnull=True
    ).update(ended_at=now)
    if value != characteristics.value_of(card.vehicle, name):
        VehicleCardWorkingValue.objects.create(
            company_id=card.company_id,
            vehicle_card=card,
            field=name,
            value=value,
            origin=VehicleCardWorkingValue.Origin.CARD,
            created_by_user_id=ctx.actor.user_id,
            created_at=now,
        )


# --- Split (§ 8.6.5) -----------------------------------------------------------------------------


def merges_of(company_id: UUID, card_id: UUID) -> list[VehicleCardMerge]:
    return list(
        VehicleCardMerge.objects.select_related("primary", "duplicate")
        .filter(company_id=company_id, primary_id=card_id)
        .order_by("-merged_at")
    )


def merge_payload(m: VehicleCardMerge) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "state": m.state,
        "primary": {"id": str(m.primary_id), "name": m.primary.name},
        "duplicate": {"id": str(m.duplicate_id), "name": m.duplicate.name},
        "reason": m.reason,
        "chosen": sorted(m.chosen),
        "merged_at": m.merged_at.isoformat(),
        "split_at": m.split_at.isoformat() if m.split_at else None,
        "split_reason": m.split_reason,
    }


def _get_merge(company_id: UUID, merge_id: UUID, *, lock: bool = False) -> VehicleCardMerge:
    found = VehicleCardMerge.objects.select_related("primary", "duplicate").filter(
        company_id=company_id, pk=merge_id
    )
    if lock:
        found = found.select_for_update(of=("self",))
    merge_row = found.first()
    if merge_row is None:
        raise errors.not_found()
    return merge_row


def _new_records(m: VehicleCardMerge) -> list[dict[str, Any]]:
    """Records of the main card made since the merge; children follow their parents."""
    found = records_since(m.company_id, m.primary_id, m.merged_at)
    keys = {f"{r['kind']}:{r['id']}" for r in found}
    for r in found:
        r["key"] = f"{r['kind']}:{r['id']}"
        r["follows"] = r.get("parent") if r.get("parent") in keys else None
    return found


def split_preview(company_id: UUID, merge_id: UUID) -> dict[str, Any]:
    m = _get_merge(company_id, merge_id)
    if m.state != VehicleCardMerge.State.MERGED:
        raise errors.business_rule("VEHICLE_MERGE_ALREADY_SPLIT")
    return {"merge": merge_payload(m), "records": _new_records(m)}


def split(
    ctx: CommandContext,
    company_id: UUID,
    merge_id: UUID,
    *,
    assignments: dict[str, str],
    reason: str,
) -> VehicleCardMerge:
    m = _get_merge(company_id, merge_id)
    primary, duplicate = _pair(company_id, m.primary_id, m.duplicate_id)
    m = _get_merge(company_id, merge_id, lock=True)
    if m.state != VehicleCardMerge.State.MERGED:
        raise errors.business_rule("VEHICLE_MERGE_ALREADY_SPLIT")
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    records = _new_records(m)
    top = [r for r in records if r["follows"] is None]
    keys = {r["key"] for r in top}
    if set(assignments) - keys:
        raise errors.business_rule("VEHICLE_SPLIT_CHANGED", records=sorted(set(assignments) - keys))
    unassigned = [k for k in sorted(keys) if assignments.get(k) not in ("primary", "duplicate")]
    if unassigned:
        # Confirmed only after every new record is assigned; nothing is assigned automatically.
        raise errors.business_rule("VEHICLE_SPLIT_UNASSIGNED", records=unassigned)
    target = {r["key"]: assignments.get(r["follows"] or r["key"]) for r in records}
    moving: dict[str, list[UUID]] = {}
    for r in records:
        if target[r["key"]] == "duplicate":
            moving.setdefault(r["kind"], []).append(UUID(r["id"]))
    sources: list[UUID] = []
    for kind, ids in moving.items():
        sources += _records[kind].reassign(ctx, company_id, ids, primary, duplicate)
        sources += ids
    OdometerReading.objects.filter(
        company_id=company_id, vehicle_card=primary, source_id__in=sources
    ).update(vehicle_card=duplicate)
    now = timezone.now()
    _restore(m, primary, now)
    duplicate.state = State.ACTIVE
    duplicate.merged_into = None
    duplicate.merged_at = None
    duplicate.version += 1
    duplicate.save(update_fields=["state", "merged_into", "merged_at", "version", "updated_at"])
    identity.refresh(duplicate.vehicle, now)
    m.state = VehicleCardMerge.State.SPLIT
    m.split_at = now
    m.split_by_user_id = ctx.actor.user_id
    m.split_reason = reason
    m.assignments = [{"record": r["key"], "card": target[r["key"]]} for r in records]
    m.save(update_fields=["state", "split_at", "split_by_user_id", "split_reason", "assignments"])
    for card, role in ((primary, "primary"), (duplicate, "duplicate")):
        ctx.audit(
            "vehicle.split",
            object_type="vehicle_card",
            object_id=card.id,
            before={"merge": str(m.id), "state": State.MERGED if role == "duplicate" else None},
            after={
                "role": role,
                "assigned": [a for a in m.assignments if a["card"] == role],
            },
            reason=reason,
        )
    return m


def _restore(m: VehicleCardMerge, primary: VehicleCard, now: datetime) -> None:
    """The main card gets back its own values where they were taken from the duplicate and have
    not been changed since (PROPOSAL)."""
    before = m.before.get("primary", {})
    current = _snapshot(primary)
    for key, taken in m.chosen.items():
        if current.get(key) != taken:
            continue
        old = before.get(key)
        if key == "name":
            primary.name = old
        elif key == "location":
            primary.accounting_location_id = UUID(old) if old else None
        elif key == "acquisition":
            primary.acquisition_cost = old["cost"] if old else None
            primary.acquisition_currency_id = old["currency"] if old else None
        elif key == "contexts":
            _set_contexts(primary, set(old or []))
        elif key.startswith("characteristic."):
            name = key.split(".", 1)[1]
            VehicleCardWorkingValue.objects.filter(
                vehicle_card=primary, field=name, ended_at__isnull=True
            ).update(ended_at=now)
            if old != characteristics.value_of(primary.vehicle, name):
                VehicleCardWorkingValue.objects.create(
                    company_id=primary.company_id,
                    vehicle_card=primary,
                    field=name,
                    value=old,
                    origin=VehicleCardWorkingValue.Origin.CARD,
                    created_at=now,
                )
    primary.version += 1
    primary.save(
        update_fields=[
            "name",
            "accounting_location",
            "acquisition_cost",
            "acquisition_currency",
            "version",
            "updated_at",
        ]
    )


def merged_cards(card: VehicleCard) -> list[dict[str, Any]]:
    """Duplicates merged into the card: their records are shown from it (§ 8.6.6)."""
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "vehicle_id": c.vehicle.public_id,
            "merged_at": c.merged_at.isoformat() if c.merged_at else None,
        }
        for c in VehicleCard.objects.select_related("vehicle")
        .filter(merged_into=card, state=State.MERGED)
        .order_by("merged_at")
    ]
