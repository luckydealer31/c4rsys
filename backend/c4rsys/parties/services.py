"""Client cards: creation, change, list and search for matches (ТЗ § 6.1.11, § 6.2.7–6.2.8)."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.parties.models import ClientCard, ClientContact
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

_PHONE_RE = re.compile(r"^\+?\d{5,15}$")
_PHONE_SEPARATORS = re.compile(r"[\s\-().]")


# --- Contacts ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Contact:
    kind: str
    value: str
    normalized: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value}


def normalize_contact(kind: str, value: str, field: str = "contacts") -> Contact:
    """Email: trimmed and lower-cased. Phone: separators removed, an optional leading «+» and
    5–15 digits. Numbers written with and without the country code are different values; no
    country is assumed."""
    raw = value.strip()
    if kind == ClientContact.Kind.EMAIL:
        email = raw.lower()
        try:
            validate_email(email)
        except ValidationError as exc:
            raise errors.validation_error(field, code="EMAIL_INVALID") from exc
        return Contact(kind, raw, email)
    if kind == ClientContact.Kind.PHONE:
        phone = _PHONE_SEPARATORS.sub("", raw)
        if not _PHONE_RE.match(phone):
            raise errors.validation_error(field, code="PHONE_INVALID")
        return Contact(kind, raw, phone)
    raise errors.validation_error(field, code="CONTACT_KIND_INVALID")


def parse_contacts(value: Any, *, required: bool) -> list[Contact]:
    """At least one phone or email for a new card (§ 6.2.7–6.2.8, decision 22.10.2026)."""
    if not isinstance(value, list):
        raise errors.validation_error("contacts")
    contacts: list[Contact] = []
    for index, item in enumerate(value):
        where = f"contacts[{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("value"), str):
            raise errors.validation_error(where)
        contact = normalize_contact(str(item.get("kind")), item["value"], where)
        if all((c.kind, c.normalized) != (contact.kind, contact.normalized) for c in contacts):
            contacts.append(contact)
    if required and not contacts:
        raise errors.validation_error("contacts", code="CONTACT_REQUIRED")
    return contacts


# --- Card data ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientData:
    kind: str
    first_name: str = ""
    last_name: str = ""
    middle_name: str = ""
    birth_date: date | None = None
    company_name: str = ""

    def check_minimum(self) -> None:
        """Person: first and last name; company: name (§ 6.2.7–6.2.8)."""
        if self.kind == ClientCard.Kind.PERSON:
            if not self.first_name:
                raise errors.validation_error("first_name", code="FIELD_REQUIRED")
            if not self.last_name:
                raise errors.validation_error("last_name", code="FIELD_REQUIRED")
            if self.company_name:
                raise errors.validation_error("company_name", code="FIELD_NOT_APPLICABLE")
        elif self.kind == ClientCard.Kind.COMPANY:
            if not self.company_name:
                raise errors.validation_error("company_name", code="FIELD_REQUIRED")
            for field in ("first_name", "last_name", "middle_name"):
                if getattr(self, field):
                    raise errors.validation_error(field, code="FIELD_NOT_APPLICABLE")
            if self.birth_date is not None:
                raise errors.validation_error("birth_date", code="FIELD_NOT_APPLICABLE")
        else:
            raise errors.validation_error("kind")
        if self.birth_date is not None and self.birth_date > timezone.localdate():
            raise errors.validation_error("birth_date", code="DATE_IN_FUTURE")


def snapshot(card: ClientCard) -> dict[str, Any]:
    return {
        "kind": card.kind,
        "first_name": card.first_name,
        "last_name": card.last_name,
        "middle_name": card.middle_name,
        "birth_date": card.birth_date.isoformat() if card.birth_date else None,
        "company_name": card.company_name,
        "contacts": [
            {"kind": c.kind, "value": c.value} for c in card.contacts.order_by("kind", "value")
        ],
    }


def _write_contacts(card: ClientCard, contacts: list[Contact]) -> None:
    ClientContact.objects.filter(client=card).delete()
    ClientContact.objects.bulk_create(
        ClientContact(
            company_id=card.company_id,
            client=card,
            kind=c.kind,
            value=c.value,
            value_normalized=c.normalized,
        )
        for c in contacts
    )


def create_client(
    ctx: CommandContext, company: Company, data: ClientData, contacts: list[Contact]
) -> ClientCard:
    """A local card without an account (§ 6.2.4, § 6.2.10). Possible duplicates do not block
    creation: the manager has compared them and decided (§ 6.1.11)."""
    data.check_minimum()
    if not contacts:
        raise errors.validation_error("contacts", code="CONTACT_REQUIRED")
    card = ClientCard.objects.create(
        company=company,
        kind=data.kind,
        first_name=data.first_name,
        last_name=data.last_name,
        middle_name=data.middle_name,
        birth_date=data.birth_date,
        company_name=data.company_name,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    _write_contacts(card, contacts)
    ctx.audit("client.created", object_type="client_card", object_id=card.id, after=snapshot(card))
    return card


def update_client(
    ctx: CommandContext,
    company_id: UUID,
    client_id: UUID,
    changes: dict[str, Any],
    contacts: list[Contact] | None,
    expected_version: int,
) -> ClientCard:
    """Names, birth date, company name and the full list of contacts; the kind does not change.
    The minimum of § 6.2.7–6.2.8 stays."""
    card = (
        ClientCard.objects.select_for_update().filter(company_id=company_id, pk=client_id).first()
    )
    if card is None:
        raise errors.not_found()
    if card.version != expected_version:
        raise errors.version_conflict()
    before = snapshot(card)
    for key, value in changes.items():
        setattr(card, key, value)
    ClientData(
        kind=card.kind,
        first_name=card.first_name,
        last_name=card.last_name,
        middle_name=card.middle_name,
        birth_date=card.birth_date,
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
        "client.changed",
        object_type="client_card",
        object_id=card.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return card


# --- Reading ----------------------------------------------------------------------------------


def clients_of(company_id: UUID) -> QuerySet[ClientCard]:
    return ClientCard.objects.filter(company_id=company_id).prefetch_related("contacts")


def get_client(company_id: UUID, client_id: UUID) -> ClientCard:
    card = clients_of(company_id).filter(pk=client_id).first()
    if card is None:
        raise errors.not_found()
    return card


def search(company_id: UUID, text: str) -> QuerySet[ClientCard]:
    """List with search by a part of the name (§ 2.8.3); exact matches first is left to ordering
    by name."""
    cards = clients_of(company_id)
    for word in text.split():
        cards = cards.filter(
            Q(first_name__icontains=word)
            | Q(last_name__icontains=word)
            | Q(middle_name__icontains=word)
            | Q(company_name__icontains=word)
        )
    return cards.order_by("last_name", "first_name", "company_name", "created_at")


@dataclass(frozen=True)
class MatchQuery:
    first_name: str = ""
    last_name: str = ""
    birth_date: date | None = None
    company_name: str = ""
    contacts: tuple[Contact, ...] = ()


def matches(company_id: UUID, query: MatchQuery) -> list[tuple[ClientCard, list[str]]]:
    """Possible existing cards for the entered data (§ 6.1.11, decision 22.10.2026): a card is a
    candidate if any of these matches — phone, email, first and last name (case-insensitive),
    birth date together with the last name, company name. Contact matches come first; each
    candidate lists what matched."""
    reasons: dict[UUID, set[str]] = {}

    def add(ids: QuerySet[Any], reason: str) -> None:
        for client_id in ids:
            reasons.setdefault(client_id, set()).add(reason)

    for contact in query.contacts:
        add(
            ClientContact.objects.filter(
                company_id=company_id, kind=contact.kind, value_normalized=contact.normalized
            ).values_list("client_id", flat=True),
            contact.kind,
        )
    cards = ClientCard.objects.filter(company_id=company_id)
    if query.first_name and query.last_name:
        add(
            cards.filter(
                first_name__iexact=query.first_name, last_name__iexact=query.last_name
            ).values_list("id", flat=True),
            "name",
        )
    if query.birth_date and query.last_name:
        add(
            cards.filter(
                birth_date=query.birth_date, last_name__iexact=query.last_name
            ).values_list("id", flat=True),
            "birth_date",
        )
    if query.company_name:
        add(
            cards.filter(company_name__iexact=query.company_name).values_list("id", flat=True),
            "company_name",
        )
    found = {c.id: c for c in clients_of(company_id).filter(pk__in=reasons)}

    def order(client_id: UUID) -> tuple[int, int, str]:
        matched = reasons[client_id]
        by_contact = bool(matched & {"phone", "email"})
        return (0 if by_contact else 1, -len(matched), str(found[client_id]))

    return [(found[i], sorted(reasons[i])) for i in sorted(found, key=order)]
