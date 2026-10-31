"""Merge of duplicate client cards or counterparties inside one company and its split (ТЗ § 6.2.3,
§ 6.1.13, § 6.5.1–6.5.5, § 23.8.6).

* A match never merges by itself (§ 6.5.1): an employee with the right compares the two cards and
  decides. Equal data go by themselves; a differing value is chosen explicitly — the main card's,
  the duplicate's, or for contacts and groups both.
* The merge is reversible (§ 6.5.2): the duplicate keeps its data and gets the state «merged»
  with a reference to the main card; the choice and the values of both cards are kept with the
  merge. Nothing of other modules is rewritten: the records of the duplicate keep their card and
  are shown from the main card; new operations go to the main card.
* Cards linked with different c4rsys profiles need a separate check (§ 6.5.4); the merge of
  cards never merges profiles.
* The split (an elevated right of «Company administrator», § 6.5.3) is one action: the records
  made before the merge stay with their cards; the records made after it are assigned by hand to
  one of the two cards; nothing is assigned automatically. A payment or a document follows its
  order. The history of the merge and of the split stays. Accounts, sign-in and the data of other
  companies do not change. The profile linked to one card loses access to the history of the
  other card (§ 6.5.5); what it downloaded is not returned.

PROPOSAL (TODO(spec)) — what blocks a merge: the main card is not active or the duplicate is
already merged; the kinds differ; the cards are linked with different profiles (the «separate
check» of § 6.5.4 is not defined: one link has to be broken first); a request to link is pending;
the duplicate has open orders, a non-zero settlement balance or a held deposit (so that the
settlements of the two cards are not mixed). When only the duplicate is linked, the main card
takes the link and the profile sees the merged history (§ 6.3.6).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.parties import counterparties, extended, services
from c4rsys.parties.models import (
    CardMerge,
    ClientCard,
    ClientGroupMember,
    ClientRestriction,
    Counterparty,
    ProfileLink,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

Card = ClientCard | Counterparty
CARD_TYPES = ("client", "counterparty")
LISTS = ("contacts", "groups")
CLIENT_TEXTS = (
    "first_name",
    "last_name",
    "middle_name",
    "birth_date",
    "company_name",
    "address",
    "comment",
    "external_id",
)
COUNTERPARTY_TEXTS = (
    "first_name",
    "last_name",
    "middle_name",
    "company_name",
    "comment",
    "registration",
    "address",
    "category",
    "external_id",
)

# --- Providers of other modules ------------------------------------------------------------------

# (company_id, duplicate_id) -> obstacles of the duplicate (open orders, balance, deposit).
Check = Callable[[UUID, UUID], list[str]]
# (company_id, card_id, moment) -> records of the card created at or after the moment as
# {"kind", "id", "label", "at"}.
Since = Callable[[UUID, UUID, datetime], list[dict[str, Any]]]
# (ctx, company_id, ids, from_card, to_card): the records go to the other card.
Reassign = Callable[[CommandContext, UUID, list[UUID], UUID, UUID], None]


@dataclass(frozen=True)
class CardRecords:
    card_type: str
    kind: str
    since: Since
    reassign: Reassign


_checks: dict[str, list[Check]] = {"client": [], "counterparty": []}
_records: dict[str, dict[str, CardRecords]] = {"client": {}, "counterparty": {}}


def register_check(card_type: str, check: Check) -> None:
    _checks[card_type].append(check)


def register_card_records(provider: CardRecords) -> None:
    _records[provider.card_type][provider.kind] = provider


def records_since(card_type: str, company_id: UUID, card_id: UUID, at: datetime) -> list[Any]:
    return [
        {**item, "key": f"{item['kind']}:{item['id']}"}
        for p in _records[card_type].values()
        for item in p.since(company_id, card_id, at)
    ]


# --- Cards ---------------------------------------------------------------------------------------


def _model(card_type: str) -> type[ClientCard] | type[Counterparty]:
    if card_type not in CARD_TYPES:
        raise errors.validation_error("card_type")
    return ClientCard if card_type == "client" else Counterparty


def _pair(
    company_id: UUID, card_type: str, primary_id: UUID, duplicate_id: UUID
) -> tuple[Card, Card]:
    if primary_id == duplicate_id:
        raise errors.validation_error("duplicate_id", code="CARD_MERGE_SAME_CARD")
    locked = {
        c.id: c
        for c in _model(card_type)
        .objects.select_for_update()
        .filter(company_id=company_id, pk__in=[primary_id, duplicate_id])
        .order_by("id")
    }
    if len(locked) != 2:
        raise errors.not_found()
    return locked[primary_id], locked[duplicate_id]


def _texts(card_type: str, kind: str) -> tuple[str, ...]:
    base = CLIENT_TEXTS if card_type == "client" else COUNTERPARTY_TEXTS
    own = extended.PERSON_FIELDS if kind == "person" else extended.COMPANY_FIELDS
    names = [n for n in base if n not in _not_of_kind(kind)]
    return (*names, *own)


def _not_of_kind(kind: str) -> tuple[str, ...]:
    if kind == "person":
        return ("company_name",)
    return ("first_name", "last_name", "middle_name", "birth_date")


def _snapshot(card_type: str, card: Card) -> dict[str, Any]:
    full = services.snapshot(card) if card_type == "client" else counterparties.snapshot(card)  # type: ignore[arg-type]
    result = {name: full[name] for name in _texts(card_type, card.kind)}
    result["contacts"] = sorted(
        (
            {"kind": c["kind"], "value": c["value"], "messenger": c["messenger"]}
            for c in full["contacts"]
        ),
        key=lambda c: (c["kind"], c["value"]),
    )
    if card_type == "client":
        result["groups"] = sorted(
            str(g)
            for g in ClientGroupMember.objects.filter(client_id=card.id).values_list(
                "group_id", flat=True
            )
        )
    return result


def _profile(card: Card) -> dict[str, str] | None:
    if card.linked_user_id is not None:
        return {"user": str(card.linked_user_id)}
    if card.linked_company_id is not None:
        return {"company": str(card.linked_company_id)}
    return None


def _profile_label(profile: dict[str, str] | None) -> dict[str, str] | None:
    if profile is None:
        return None
    from c4rsys.accounts import services as accounts
    from c4rsys.companies import services as companies

    if "user" in profile:
        user = accounts.get_user(UUID(profile["user"]))
        return (
            {"public_id": user.public_id, "name": f"{user.first_name} {user.last_name}".strip()}
            if user
            else None
        )
    company = companies.get_company(UUID(profile["company"]))
    return {"public_id": company.public_id, "name": company.display_name} if company else None


def _pending(card_type: str, card: Card) -> bool:
    field = "client_id" if card_type == "client" else "counterparty_id"
    now = timezone.now()
    return ProfileLink.objects.filter(
        **{field: card.id}, state=ProfileLink.State.PENDING, expires_at__gt=now
    ).exists()


def _obstacles(card_type: str, primary: Card, duplicate: Card) -> list[str]:
    found = []
    if primary.state != ClientCard.State.ACTIVE:
        found.append("primary_not_active")
    if duplicate.state == ClientCard.State.MERGED:
        found.append("duplicate_merged")
    if primary.kind != duplicate.kind:
        found.append("kind_differs")
    main, other = _profile(primary), _profile(duplicate)
    if main is not None and other is not None and main != other:
        # § 6.5.4: TODO(spec) — the separate check is not defined; one link is broken first.
        found.append("different_profiles")
    if _pending(card_type, duplicate):
        found.append("duplicate_link_pending")
    if other is not None and main is None and _pending(card_type, primary):
        found.append("primary_link_pending")
    for check in _checks[card_type]:
        found += check(primary.company_id, duplicate.id)
    return found


def _choices_for(key: str) -> tuple[str, ...]:
    return ("primary", "duplicate", "both") if key in LISTS else ("primary", "duplicate")


def comparison(card_type: str, primary: Card, duplicate: Card) -> dict[str, Any]:
    main, other = _snapshot(card_type, primary), _snapshot(card_type, duplicate)
    keys = list(dict.fromkeys([*main, *other]))
    fields = [
        {
            "key": key,
            "primary": main.get(key),
            "duplicate": other.get(key),
            "same": main.get(key) == other.get(key),
            "choices": list(_choices_for(key)),
        }
        for key in keys
    ]
    restrictions: dict[str, list[dict[str, Any]]] = {"primary": [], "duplicate": []}
    if card_type == "client":
        for role, card in (("primary", primary), ("duplicate", duplicate)):
            restrictions[role] = [
                {
                    "id": str(r.id),
                    "kind": r.kind,
                    "reason": r.reason,
                    "added_at": r.added_at.isoformat(),
                }
                for r in ClientRestriction.objects.filter(client_id=card.id, lifted_at__isnull=True)
            ]
    return {
        "card_type": card_type,
        "primary": _brief(card_type, primary),
        "duplicate": _brief(card_type, duplicate),
        "fields": fields,
        "profiles": {
            "primary": _profile_label(_profile(primary)),
            "duplicate": _profile_label(_profile(duplicate)),
        },
        "restrictions": restrictions,
        "obstacles": _obstacles(card_type, primary, duplicate),
    }


def _brief(card_type: str, card: Card) -> dict[str, Any]:
    name = (
        services.display_name(card)  # type: ignore[arg-type]
        if card_type == "client"
        else counterparties.display_name(card)  # type: ignore[arg-type]
    )
    return {"id": str(card.id), "name": name, "kind": card.kind, "state": card.state}


def preview(
    company_id: UUID, card_type: str, primary_id: UUID, duplicate_id: UUID
) -> dict[str, Any]:
    cards = {
        c.id: c
        for c in _model(card_type).objects.filter(
            company_id=company_id, pk__in=[primary_id, duplicate_id]
        )
    }
    if primary_id == duplicate_id or len(cards) != 2:
        raise errors.not_found()
    return comparison(card_type, cards[primary_id], cards[duplicate_id])


# --- Merge (§ 6.2.3, § 6.5.2) --------------------------------------------------------------------


def merge(
    ctx: CommandContext,
    company: Company,
    card_type: str,
    primary_id: UUID,
    duplicate_id: UUID,
    *,
    choices: dict[str, str],
    reason: str,
) -> CardMerge:
    primary, duplicate = _pair(company.id, card_type, primary_id, duplicate_id)
    obstacles = _obstacles(card_type, primary, duplicate)
    if obstacles:
        raise errors.business_rule("CARD_MERGE_NOT_ALLOWED", obstacles=obstacles)
    main, other = _snapshot(card_type, primary), _snapshot(card_type, duplicate)
    differing = sorted(k for k in main if main[k] != other.get(k))
    unknown = sorted(set(choices) - set(differing))
    if unknown:
        # The cards changed since the comparison was shown.
        raise errors.business_rule("CARD_MERGE_CHANGED", fields=unknown)
    missing = [k for k in differing if choices.get(k) not in _choices_for(k)]
    if missing:
        raise errors.validation_error("choices", code="CARD_MERGE_CHOICE_REQUIRED", fields=missing)
    now = timezone.now()
    taken = {k: choices[k] for k in differing if choices[k] != "primary"}
    moved = _profile(duplicate) if _profile(primary) is None else None
    before_state = duplicate.state
    if moved is not None:
        # One card per profile in the list (§ 6.2.4): the duplicate lets the link go first.
        primary.linked_user_id, primary.linked_company_id = (
            duplicate.linked_user_id,
            duplicate.linked_company_id,
        )
        duplicate.linked_user_id = duplicate.linked_company_id = None
    duplicate.state = ClientCard.State.MERGED
    duplicate.merged_into_id = primary.id
    duplicate.merged_at = now
    duplicate.version += 1
    duplicate.save()
    _apply(card_type, primary, duplicate, taken, other)
    if primary.provisional and services.has_minimum(primary, primary.contacts.count()):
        primary.provisional = False
    primary.version += 1
    primary.save()
    record = CardMerge.objects.create(
        company=company,
        card_type=card_type,
        primary_client_id=primary.id if card_type == "client" else None,
        duplicate_client_id=duplicate.id if card_type == "client" else None,
        primary_counterparty_id=primary.id if card_type == "counterparty" else None,
        duplicate_counterparty_id=duplicate.id if card_type == "counterparty" else None,
        reason=reason,
        chosen={
            k: {"choice": v, "value": _value_after(k, v, main, other)} for k, v in taken.items()
        },
        before={"primary": main, "duplicate": other, "duplicate_state": before_state},
        moved_link=moved or {},
        merged_by_user_id=ctx.actor.user_id,
        merged_at=now,
    )
    object_type = "client_card" if card_type == "client" else "counterparty"
    for card, role in ((primary, "primary"), (duplicate, "duplicate")):
        ctx.audit(
            f"{card_type}.merged",
            object_type=object_type,
            object_id=card.id,
            before={"state": before_state if role == "duplicate" else ClientCard.State.ACTIVE},
            after={
                "role": role,
                "merge": str(record.id),
                "primary": str(primary.id),
                "duplicate": str(duplicate.id),
                "taken": taken,
                "link_moved": bool(moved),
            },
            reason=reason,
        )
    return record


def _value_after(key: str, choice: str, main: dict[str, Any], other: dict[str, Any]) -> Any:
    if choice == "duplicate":
        return other.get(key)
    # «both»: the union of the lists, in the order of the snapshot.
    union = list(main.get(key) or [])
    for item in other.get(key) or []:
        if item not in union:
            union.append(item)
    if key == "contacts":
        return sorted(union, key=lambda c: (c["kind"], c["value"]))
    return sorted(union)


def _apply(
    card_type: str, primary: Card, duplicate: Card, taken: dict[str, str], other: dict[str, Any]
) -> None:
    main = _snapshot(card_type, primary)
    for key, choice in taken.items():
        value = _value_after(key, choice, main, other)
        _set(card_type, primary, key, value)


def _set(card_type: str, card: Card, key: str, value: Any) -> None:
    """A value of the comparison put into the card (not saved)."""
    if key == "contacts":
        contacts = [
            services.normalize_contact(c["kind"], c["value"], "contacts", c.get("messenger", ""))
            for c in value or []
        ]
        if card_type == "client":
            services.write_contacts(card, contacts)  # type: ignore[arg-type]
        else:
            counterparties._write_contacts(card, contacts)  # type: ignore[arg-type]
    elif key == "groups":
        wanted = {UUID(g) for g in value or []}
        ClientGroupMember.objects.filter(client_id=card.id).exclude(group_id__in=wanted).delete()
        present = set(
            ClientGroupMember.objects.filter(client_id=card.id).values_list("group_id", flat=True)
        )
        for group_id in sorted(wanted - present, key=str):
            ClientGroupMember.objects.create(
                company_id=card.company_id, group_id=group_id, client_id=card.id
            )
    elif key in ("birth_date", "driving_since"):
        setattr(card, key, date.fromisoformat(value) if value else None)
    else:
        setattr(card, key, value)


# --- Split (§ 6.5.3, § 6.5.5) --------------------------------------------------------------------


def _get_merge(company_id: UUID, merge_id: UUID, *, lock: bool = False) -> CardMerge:
    found = CardMerge.objects.filter(company_id=company_id, pk=merge_id)
    if lock:
        found = found.select_for_update()
    row = found.first()
    if row is None:
        raise errors.not_found()
    return row


def ids_of(m: CardMerge) -> tuple[UUID, UUID]:
    primary = m.primary_client_id or m.primary_counterparty_id
    duplicate = m.duplicate_client_id or m.duplicate_counterparty_id
    if primary is None or duplicate is None:  # excluded by card_merge_one_list
        raise RuntimeError(f"Merge {m.id} has no cards")
    return primary, duplicate


def merges_of(company_id: UUID, card_type: str, card_id: UUID) -> list[CardMerge]:
    field = "primary_client_id" if card_type == "client" else "primary_counterparty_id"
    other = "duplicate_client_id" if card_type == "client" else "duplicate_counterparty_id"
    from django.db.models import Q

    return list(
        CardMerge.objects.filter(
            Q(**{field: card_id}) | Q(**{other: card_id}), company_id=company_id
        ).order_by("-merged_at")
    )


def merge_payload(m: CardMerge) -> dict[str, Any]:
    primary_id, duplicate_id = ids_of(m)
    model = _model(m.card_type)
    cards = {c.id: c for c in model.objects.filter(pk__in=[primary_id, duplicate_id])}
    return {
        "id": str(m.id),
        "card_type": m.card_type,
        "state": m.state,
        "primary": _brief(m.card_type, cards[primary_id]),
        "duplicate": _brief(m.card_type, cards[duplicate_id]),
        "reason": m.reason,
        "chosen": {k: v["choice"] for k, v in m.chosen.items()},
        "link_moved": bool(m.moved_link),
        "merged_at": m.merged_at.isoformat(),
        "split_at": m.split_at.isoformat() if m.split_at else None,
        "split_reason": m.split_reason,
        "assignments": m.assignments,
        "revoked": m.revoked,
    }


def split_preview(company_id: UUID, merge_id: UUID) -> dict[str, Any]:
    m = _get_merge(company_id, merge_id)
    if m.state != CardMerge.State.MERGED:
        raise errors.business_rule("CARD_MERGE_ALREADY_SPLIT")
    primary_id, _ = ids_of(m)
    return {
        "merge": merge_payload(m),
        "records": records_since(m.card_type, company_id, primary_id, m.merged_at),
    }


def split(
    ctx: CommandContext,
    company_id: UUID,
    merge_id: UUID,
    *,
    assignments: dict[str, str],
    reason: str,
) -> CardMerge:
    peek = _get_merge(company_id, merge_id)
    primary_id, duplicate_id = ids_of(peek)
    primary, duplicate = _pair(company_id, peek.card_type, primary_id, duplicate_id)
    m = _get_merge(company_id, merge_id, lock=True)
    if m.state != CardMerge.State.MERGED:
        raise errors.business_rule("CARD_MERGE_ALREADY_SPLIT")
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    if primary.state == ClientCard.State.MERGED:
        # A later merge of the main card is split first.
        raise errors.business_rule("CARD_MERGE_PRIMARY_MERGED", main=str(primary.merged_into_id))
    card_type = m.card_type
    records = records_since(card_type, company_id, primary.id, m.merged_at)
    keys = {r["key"] for r in records}
    if set(assignments) - keys:
        raise errors.business_rule("CARD_SPLIT_CHANGED", records=sorted(set(assignments) - keys))
    unassigned = [k for k in sorted(keys) if assignments.get(k) not in ("primary", "duplicate")]
    if unassigned:
        # Confirmed only after every new record is assigned; nothing is assigned automatically.
        raise errors.business_rule("CARD_SPLIT_UNASSIGNED", records=unassigned)
    moving: dict[str, list[UUID]] = {}
    for r in records:
        if assignments[r["key"]] == "duplicate":
            moving.setdefault(r["kind"], []).append(UUID(r["id"]))
    for kind, ids in moving.items():
        _records[card_type][kind].reassign(ctx, company_id, ids, primary.id, duplicate.id)
    now = timezone.now()
    revoked = _revoked(card_type, company_id, primary, duplicate)
    _restore(card_type, m, primary)
    if m.moved_link and _profile(primary) == m.moved_link:
        duplicate.linked_user_id, duplicate.linked_company_id = (
            primary.linked_user_id,
            primary.linked_company_id,
        )
        primary.linked_user_id = primary.linked_company_id = None
        primary.save(update_fields=["linked_user_id", "linked_company_id", "updated_at"])
    duplicate.state = m.before.get("duplicate_state") or ClientCard.State.ACTIVE
    duplicate.merged_into_id = None
    duplicate.merged_at = None
    duplicate.version += 1
    duplicate.save()
    m.state = CardMerge.State.SPLIT
    m.split_at = now
    m.split_by_user_id = ctx.actor.user_id
    m.split_reason = reason
    m.assignments = [{"record": r["key"], "card": assignments[r["key"]]} for r in records]
    m.revoked = revoked
    m.save(
        update_fields=[
            "state",
            "split_at",
            "split_by_user_id",
            "split_reason",
            "assignments",
            "revoked",
        ]
    )
    object_type = "client_card" if card_type == "client" else "counterparty"
    for card, role in ((primary, "primary"), (duplicate, "duplicate")):
        ctx.audit(
            f"{card_type}.split",
            object_type=object_type,
            object_id=card.id,
            before={
                "merge": str(m.id),
                "state": ClientCard.State.MERGED if role == "duplicate" else None,
            },
            after={
                "role": role,
                "assigned": [a for a in m.assignments if a["card"] == role],
                "revoked": revoked,
            },
            reason=reason,
        )
    return m


def _revoked(
    card_type: str, company_id: UUID, primary: Card, duplicate: Card
) -> list[dict[str, str]]:
    """§ 6.5.5: the confirmed links of the two cards. After the split each sees only the history
    of its own card; the history of the other card shown during the merge is no longer open."""
    field = "client_id" if card_type == "client" else "counterparty_id"
    found = ProfileLink.objects.filter(
        **{f"{field}__in": [primary.id, duplicate.id]},
        company_id=company_id,
        state=ProfileLink.State.ACCEPTED,
    )
    return [{"link": str(link.id), "target": link.target_label} for link in found]


def _restore(card_type: str, m: CardMerge, primary: Card) -> None:
    """The main card gets back its own values where they were taken from the duplicate and have
    not been changed since (PROPOSAL); a value changed after the merge stays."""
    before = m.before.get("primary", {})
    current = _snapshot(card_type, primary)
    for key, item in m.chosen.items():
        if current.get(key) != item["value"]:
            continue
        _set(card_type, primary, key, before.get(key))
    primary.version += 1
    primary.save()
