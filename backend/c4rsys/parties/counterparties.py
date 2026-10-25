"""Counterparties: the minimal list of the warehouse stage (ТЗ § 6.1.4–6.1.9, § 6.2.7–6.2.8,
§ 6.6.4–6.6.5, § 22.3.3; customer's decision of 25.10.2026).

Person: first and last name and at least one contact; company: name and at least one contact.
A counterparty is archived and restored, not deleted. Linking to a c4rsys profile, requests and
merging of cards come later.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.parties.models import Counterparty, CounterpartyContact
from c4rsys.parties.services import Contact
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext


@dataclass(frozen=True)
class CounterpartyData:
    kind: str
    first_name: str = ""
    last_name: str = ""
    middle_name: str = ""
    company_name: str = ""
    comment: str = ""

    def check_minimum(self) -> None:
        """Person: first and last name; company: name (§ 6.2.7–6.2.8, § 22.3.3)."""
        if self.kind == Counterparty.Kind.PERSON:
            if not self.first_name:
                raise errors.validation_error("first_name", code="FIELD_REQUIRED")
            if not self.last_name:
                raise errors.validation_error("last_name", code="FIELD_REQUIRED")
            if self.company_name:
                raise errors.validation_error("company_name", code="FIELD_NOT_APPLICABLE")
        elif self.kind == Counterparty.Kind.COMPANY:
            if not self.company_name:
                raise errors.validation_error("company_name", code="FIELD_REQUIRED")
            for field in ("first_name", "last_name", "middle_name"):
                if getattr(self, field):
                    raise errors.validation_error(field, code="FIELD_NOT_APPLICABLE")
        else:
            raise errors.validation_error("kind")


def snapshot(card: Counterparty) -> dict[str, Any]:
    return {
        "kind": card.kind,
        "first_name": card.first_name,
        "last_name": card.last_name,
        "middle_name": card.middle_name,
        "company_name": card.company_name,
        "comment": card.comment,
        "state": card.state,
        "contacts": [
            {"kind": c.kind, "value": c.value} for c in card.contacts.order_by("kind", "value")
        ],
    }


def _write_contacts(card: Counterparty, contacts: list[Contact]) -> None:
    CounterpartyContact.objects.filter(counterparty=card).delete()
    CounterpartyContact.objects.bulk_create(
        CounterpartyContact(
            company_id=card.company_id,
            counterparty=card,
            kind=c.kind,
            value=c.value,
            value_normalized=c.normalized,
        )
        for c in contacts
    )


def create(
    ctx: CommandContext, company: Company, data: CounterpartyData, contacts: list[Contact]
) -> Counterparty:
    data.check_minimum()
    if not contacts:
        raise errors.validation_error("contacts", code="CONTACT_REQUIRED")
    card = Counterparty.objects.create(
        company=company,
        kind=data.kind,
        first_name=data.first_name,
        last_name=data.last_name,
        middle_name=data.middle_name,
        company_name=data.company_name,
        comment=data.comment,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    _write_contacts(card, contacts)
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
    return card


def update(
    ctx: CommandContext,
    company_id: UUID,
    counterparty_id: UUID,
    changes: dict[str, Any],
    contacts: list[Contact] | None,
    expected_version: int,
) -> Counterparty:
    """Names, company name, comment and the full list of contacts; the kind does not change."""
    card = _lock(company_id, counterparty_id, expected_version)
    before = snapshot(card)
    for key, value in changes.items():
        setattr(card, key, value)
    CounterpartyData(
        kind=card.kind,
        first_name=card.first_name,
        last_name=card.last_name,
        middle_name=card.middle_name,
        company_name=card.company_name,
    ).check_minimum()
    if contacts is not None:
        if not contacts:
            raise errors.validation_error("contacts", code="CONTACT_REQUIRED")
        _write_contacts(card, contacts)
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
    return " ".join(part for part in (card.first_name, card.last_name) if part)


def of_company(company_id: UUID) -> QuerySet[Counterparty]:
    return Counterparty.objects.filter(company_id=company_id).prefetch_related("contacts")


def get(company_id: UUID, counterparty_id: UUID) -> Counterparty:
    card = of_company(company_id).filter(pk=counterparty_id).first()
    if card is None:
        raise errors.not_found()
    return card


def usable(company_id: UUID, counterparty_id: UUID, field: str = "supplier") -> Counterparty:
    """An active counterparty of the company — for a purchase or a receipt."""
    card = of_company(company_id).filter(pk=counterparty_id).first()
    if card is None or card.state != Counterparty.State.ACTIVE:
        raise errors.validation_error(field, code="COUNTERPARTY_NOT_USABLE")
    return card


def names(company_id: UUID, ids: set[UUID]) -> dict[UUID, str]:
    return {
        c.id: display_name(c)
        for c in Counterparty.objects.filter(company_id=company_id, pk__in=ids)
    }


def search(
    company_id: UUID, text: str, state: str = Counterparty.State.ACTIVE
) -> QuerySet[Counterparty]:
    """By a part of the name or a contact (§ 2.8.3)."""
    cards = of_company(company_id)
    if state:
        cards = cards.filter(state=state)
    for word in text.split():
        cards = cards.filter(
            Q(first_name__icontains=word)
            | Q(last_name__icontains=word)
            | Q(middle_name__icontains=word)
            | Q(company_name__icontains=word)
            | Q(contacts__value__icontains=word)
        ).distinct()
    return cards.order_by("company_name", "last_name", "first_name", "created_at")
