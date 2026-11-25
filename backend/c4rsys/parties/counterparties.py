"""Counterparties: the minimal list of the warehouse stage (ТЗ § 6.1.4–6.1.9, § 6.2.7–6.2.8,
§ 6.6.4–6.6.5, § 22.3.3; customer's decision of 25.10.2026).

Person: first and last name and at least one contact; company: name and at least one contact.
A counterparty is archived and restored, not deleted. Linking to a c4rsys profile is in links.py,
merging duplicates in merges.py (block 13).
"""

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.parties import extended
from c4rsys.parties.models import Counterparty, CounterpartyContact, ProfileLink
from c4rsys.parties.services import (
    Contact,
    MatchQuery,
    duplicate_ids,
    has_minimum,
    loose_matches,
    refresh_duplicate_marks,
)
from c4rsys.parties.services import expand as services_expand
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext


@dataclass(frozen=True)
class CounterpartyData:
    kind: str
    first_name: str = ""
    last_name: str = ""
    middle_name: str = ""
    company_name: str = ""
    birth_date: date | None = None
    comment: str = ""
    # Optional data of the import template (§ 22.3.3).
    registration: str = ""
    address: str = ""
    category: str = ""
    external_id: str = ""
    # § 6.2.12–6.2.13 by § 6.1.13 (extended.FIELDS); none is required.
    extended: dict[str, Any] = field(default_factory=dict)

    def check_minimum(self, *, provisional: bool = False) -> None:
        """Person: first and last name; company: name (§ 6.2.7–6.2.8, § 22.3.3). A provisional
        card (§ 6.2.9, § 6.1.13) may lack them."""
        if self.kind == Counterparty.Kind.PERSON:
            if not self.first_name and not provisional:
                raise errors.validation_error("first_name", code="FIELD_REQUIRED")
            if not self.last_name and not provisional:
                raise errors.validation_error("last_name", code="FIELD_REQUIRED")
            if self.company_name:
                raise errors.validation_error("company_name", code="FIELD_NOT_APPLICABLE")
            if self.birth_date is not None and self.birth_date > timezone.localdate():
                raise errors.validation_error("birth_date", code="DATE_IN_FUTURE")
        elif self.kind == Counterparty.Kind.COMPANY:
            if self.birth_date is not None:
                raise errors.validation_error("birth_date", code="FIELD_NOT_APPLICABLE")
            if not self.company_name and not provisional:
                raise errors.validation_error("company_name", code="FIELD_REQUIRED")
            for name in ("first_name", "last_name", "middle_name"):
                if getattr(self, name):
                    raise errors.validation_error(name, code="FIELD_NOT_APPLICABLE")
        else:
            raise errors.validation_error("kind")


def snapshot(card: Counterparty) -> dict[str, Any]:
    return {
        "kind": card.kind,
        "first_name": card.first_name,
        "last_name": card.last_name,
        "middle_name": card.middle_name,
        "company_name": card.company_name,
        "birth_date": card.birth_date.isoformat() if card.birth_date else None,
        "comment": card.comment,
        "registration": card.registration,
        "address": card.address,
        "category": card.category,
        "external_id": card.external_id,
        "possible_duplicate": card.possible_duplicate,
        "state": card.state,
        "provisional": card.provisional,
        "merged_into": str(card.merged_into_id) if card.merged_into_id else None,
        **extended.values_of(card),
        "contacts": [
            {"kind": c.kind, "value": c.value, "messenger": c.messenger}
            for c in card.contacts.order_by("kind", "value")
        ],
    }


def write_contacts(card: Counterparty, contacts: list[Contact]) -> None:
    CounterpartyContact.objects.filter(counterparty=card).delete()
    contacts = services_expand(contacts)
    CounterpartyContact.objects.bulk_create(
        CounterpartyContact(
            company_id=card.company_id,
            counterparty=card,
            kind=c.kind,
            value=c.value,
            value_normalized=c.normalized,
            messenger=c.messenger,
        )
        for c in contacts
    )


def create(
    ctx: CommandContext,
    company: Company,
    data: CounterpartyData,
    contacts: list[Contact],
    *,
    possible_duplicate: bool = False,
    provisional: bool = False,
) -> Counterparty:
    data.check_minimum(provisional=provisional)
    if not contacts and not provisional:
        raise errors.validation_error("contacts", code="CONTACT_REQUIRED")
    card = Counterparty.objects.create(
        company=company,
        kind=data.kind,
        first_name=data.first_name,
        last_name=data.last_name,
        middle_name=data.middle_name,
        company_name=data.company_name,
        birth_date=data.birth_date,
        comment=data.comment,
        registration=data.registration,
        address=data.address,
        category=data.category,
        external_id=data.external_id,
        possible_duplicate=possible_duplicate,
        provisional=provisional,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
        **data.extended,
    )
    write_contacts(card, contacts)
    ctx.audit(
        "counterparty.created", object_type="counterparty", object_id=card.id, after=snapshot(card)
    )
    return card


def _lock(company_id: UUID, counterparty_id: UUID, expected_version: int) -> Counterparty:
    card = (
        Counterparty.objects.select_for_update()
        .filter(company_id=company_id, pk=counterparty_id)
        .first()
    )
    if card is None:
        raise errors.not_found()
    if card.version != expected_version:
        raise errors.version_conflict()
    if card.state == Counterparty.State.MERGED:
        raise errors.business_rule("COUNTERPARTY_MERGED", main=str(card.merged_into_id))
    return card


def update(
    ctx: CommandContext,
    company_id: UUID,
    counterparty_id: UUID,
    changes: dict[str, Any],
    contacts: list[Contact] | None,
    expected_version: int,
) -> Counterparty:
    """Names, company name, comment, the extended data and the full list of contacts; the kind
    does not change. A provisional card becomes ready once it holds the minimum (PROPOSAL)."""
    card = _lock(company_id, counterparty_id, expected_version)
    before = snapshot(card)
    matched_before = duplicate_ids(card, duplicates_of)
    for key, value in changes.items():
        setattr(card, key, value)
    CounterpartyData(
        kind=card.kind,
        first_name=card.first_name,
        last_name=card.last_name,
        middle_name=card.middle_name,
        company_name=card.company_name,
        birth_date=card.birth_date,
    ).check_minimum(provisional=card.provisional)
    if contacts is not None:
        if not contacts and not card.provisional:
            raise errors.validation_error("contacts", code="CONTACT_REQUIRED")
        write_contacts(card, contacts)
    if card.provisional and has_minimum(card, card.contacts.count()):
        card.provisional = False
    card.version += 1
    card.save()
    after = snapshot(card)
    ctx.audit(
        "counterparty.changed",
        object_type="counterparty",
        object_id=card.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    refresh_duplicate_marks(card, matched_before, duplicates_of)
    return card


def set_archived(
    ctx: CommandContext,
    company_id: UUID,
    counterparty_id: UUID,
    archived: bool,
    expected_version: int,
) -> Counterparty:
    """An archived card leaves the usual list and is found with the «Archive» filter; it can be
    restored (§ 6.6.4–6.6.5). Its history stays."""
    card = _lock(company_id, counterparty_id, expected_version)
    target = Counterparty.State.ARCHIVED if archived else Counterparty.State.ACTIVE
    if card.state == target:
        raise errors.business_rule("COUNTERPARTY_ALREADY_IN_STATE", state=target)
    card.state = target
    card.version += 1
    card.save(update_fields=["state", "version", "updated_at"])
    ctx.audit(
        "counterparty.archived" if archived else "counterparty.restored",
        object_type="counterparty",
        object_id=card.id,
        after={"state": target},
    )
    return card


# --- Reading and other modules -----------------------------------------------------------------


def display_name(card: Counterparty) -> str:
    if card.company_name:
        return card.company_name
    name = " ".join(part for part in (card.first_name, card.last_name) if part)
    if name or not card.provisional:
        return name
    contact = card.contacts.order_by("kind", "value").first()
    if contact is not None:
        return contact.value
    label = (
        ProfileLink.objects.filter(counterparty_id=card.id)
        .order_by("-created_at")
        .values_list("target_label", flat=True)
        .first()
    )
    return label or ""


def of_company(company_id: UUID) -> QuerySet[Counterparty]:
    return Counterparty.objects.filter(company_id=company_id).prefetch_related("contacts")


def get(company_id: UUID, counterparty_id: UUID) -> Counterparty:
    card = of_company(company_id).filter(pk=counterparty_id).first()
    if card is None:
        raise errors.not_found()
    return card


def usable(company_id: UUID, counterparty_id: UUID, field: str = "supplier") -> Counterparty:
    """An active counterparty of the company — for a purchase or a receipt; a provisional card
    is not ready for operations (§ 6.2.10, § 6.1.13)."""
    card = of_company(company_id).filter(pk=counterparty_id).first()
    if card is None or card.state != Counterparty.State.ACTIVE or card.provisional:
        raise errors.validation_error(field, code="COUNTERPARTY_NOT_USABLE")
    return card


def main_card_id(company_id: UUID, counterparty_id: UUID) -> UUID:
    current = counterparty_id
    for _ in range(100):
        row = (
            Counterparty.objects.filter(company_id=company_id, pk=current)
            .values_list("state", "merged_into_id")
            .first()
        )
        if row is None or row[0] != Counterparty.State.MERGED or row[1] is None:
            return current
        current = row[1]
    return current


def family_ids(company_id: UUID, counterparty_id: UUID) -> list[UUID]:
    """The card and the cards merged into it (§ 6.5.2, § 6.1.13)."""
    found = [counterparty_id]
    frontier = [counterparty_id]
    while frontier:
        frontier = list(
            Counterparty.objects.filter(
                company_id=company_id,
                merged_into_id__in=frontier,
                state=Counterparty.State.MERGED,
            ).values_list("id", flat=True)
        )
        found += [i for i in frontier if i not in found]
    return found


def names(company_id: UUID, ids: set[UUID]) -> dict[UUID, str]:
    return {
        c.id: display_name(c)
        for c in Counterparty.objects.filter(company_id=company_id, pk__in=ids)
    }


def categories(company_id: UUID) -> list[str]:
    """The directory of the categories of counterparties of the company (customer's decision of
    15.11.2026): the categories its cards carry, by the alphabet. A category appears when it is
    set in a card and is chosen from this list afterwards (PROPOSAL: no table of its own)."""
    found = (
        of_company(company_id)
        .exclude(state=Counterparty.State.MERGED)
        .exclude(category="")
        .values_list("category", flat=True)
        .distinct()
    )
    return sorted(set(found), key=str.casefold)


def search(
    company_id: UUID,
    text: str,
    state: str = Counterparty.State.ACTIVE,
    category: str = "",
    also: Collection[UUID] = (),
) -> QuerySet[Counterparty]:
    """By a part of the name or a contact (§ 2.8.3)."""
    cards = of_company(company_id)
    # Merged cards are reached through their main card (§ 6.5.2).
    cards = cards.filter(state=state) if state else cards.exclude(state=Counterparty.State.MERGED)
    if category:
        cards = cards.filter(category=category)
    every = cards
    for word in text.split():
        cards = cards.filter(
            Q(first_name__icontains=word)
            | Q(last_name__icontains=word)
            | Q(middle_name__icontains=word)
            | Q(company_name__icontains=word)
            | Q(contacts__value__icontains=word)
        ).distinct()
    if also:
        # `also` — the records marked with a tag of that name (decision of 17.11.2026).
        cards = every.filter(Q(pk__in=cards.values("pk")) | Q(pk__in=also))
    return cards.order_by("company_name", "last_name", "first_name", "created_at")


def duplicates_of(card: Counterparty) -> list[dict[str, Any]]:
    """The other counterparties the card matches, and by what: shown by the mark «Possible
    duplicate» (customer's remark of 25.11.2026)."""
    query = MatchQuery(
        first_name=card.first_name,
        last_name=card.last_name,
        company_name=card.company_name,
        contacts=tuple(
            Contact(c.kind, c.value, c.value_normalized, c.messenger) for c in card.contacts.all()
        ),
    )
    return [
        {"id": str(other.id), "name": display_name(other), "kind": other.kind, "matched": matched}
        for other, matched in matches(card.company_id, query)
        if other.id != card.id
    ]


def matches(company_id: UUID, query: MatchQuery) -> list[tuple[Counterparty, list[str]]]:
    """Possible existing counterparties for the entered data, as for the clients (§ 6.1.11): the
    phone, the email, the first and the last name together, the company name. The owner of a
    vehicle entered in its form is checked so (customer's remark of 25.11.2026)."""
    reasons: dict[UUID, set[str]] = {}

    def add(ids: Collection[UUID], reason: str) -> None:
        for card_id in ids:
            reasons.setdefault(card_id, set()).add(reason)

    for contact in query.contacts:
        add(
            list(
                CounterpartyContact.objects.filter(
                    counterparty__company_id=company_id,
                    kind=contact.kind,
                    messenger=contact.messenger,
                    value_normalized=contact.normalized,
                ).values_list("counterparty_id", flat=True)
            ),
            contact.kind,
        )
    cards = Counterparty.objects.filter(company_id=company_id)
    for ids, reason in loose_matches(cards, query):
        add(ids, reason)
    if query.first_name and query.last_name:
        add(
            list(
                cards.filter(
                    first_name__iexact=query.first_name, last_name__iexact=query.last_name
                ).values_list("id", flat=True)
            ),
            "name",
        )
    if query.company_name:
        add(
            list(
                cards.filter(company_name__iexact=query.company_name).values_list("id", flat=True)
            ),
            "company_name",
        )
    # A merged card is offered as its main card (§ 6.5.2).
    for card_id in list(reasons):
        main = main_card_id(company_id, card_id)
        if main != card_id:
            reasons.setdefault(main, set()).update(reasons.pop(card_id))
    found = {
        c.id: c
        for c in of_company(company_id)
        .filter(pk__in=reasons)
        .exclude(state=Counterparty.State.MERGED)
    }
    kept = {k: v for k, v in reasons.items() if k in found}

    def order(card_id: UUID) -> tuple[int, int, str]:
        matched = kept[card_id]
        return (0 if matched & {"phone", "email"} else 1, -len(matched), str(found[card_id]))

    return [(found[i], sorted(kept[i])) for i in sorted(found, key=order)]
