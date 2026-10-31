"""Client cards: creation, change, list and search for matches (ТЗ § 6.1.11, § 6.2.7–6.2.8)."""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.parties import extended
from c4rsys.parties.models import ClientCard, ClientContact, ClientRestriction, ProfileLink
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

_PHONE_RE = re.compile(r"^\+?\d{5,15}$")
_PHONE_SEPARATORS = re.compile(r"[\s\-().]")


# --- Contacts ---------------------------------------------------------------------------------


# The priority way of contact by a phone number: a call or SMS, or one messenger from a list of
# common ones (customer's decisions of 30.10.2026). WhatsApp, Telegram, LINE, WeChat and Viber were
# named by the customer; Signal, KakaoTalk, Zalo and Facebook Messenger are a PROPOSAL.
MESSENGERS = (
    "whatsapp",
    "telegram",
    "line",
    "wechat",
    "viber",
    "signal",
    "kakaotalk",
    "zalo",
    "facebook_messenger",
    "other",
)


@dataclass(frozen=True)
class Contact:
    kind: str
    value: str
    normalized: str
    messenger: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value, "messenger": self.messenger}


def normalize_contact(
    kind: str, value: str, field: str = "contacts", messenger: str = ""
) -> Contact:
    """Email: trimmed and lower-cased. Phone: separators removed, an optional leading «+» and
    5–15 digits. Numbers written with and without the country code are different values; no
    country is assumed. A phone is contacted by a call or SMS unless a messenger is bound to it;
    an email has no messenger."""
    raw = value.strip()
    messenger = messenger.strip().lower()
    if messenger and (kind != ClientContact.Kind.PHONE or messenger not in MESSENGERS):
        raise errors.validation_error(field, code="MESSENGER_INVALID")
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
        return Contact(kind, raw, phone, messenger)
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
        messenger = item.get("messenger") or ""
        if not isinstance(messenger, str):
            raise errors.validation_error(where)
        contact = normalize_contact(str(item.get("kind")), item["value"], where, messenger)
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
    # Optional data of the import template (§ 22.3.2).
    address: str = ""
    comment: str = ""
    external_id: str = ""
    # § 6.2.12–6.2.13 (extended.FIELDS); none is required.
    extended: dict[str, Any] = field(default_factory=dict)

    def check_minimum(self, *, provisional: bool = False) -> None:
        """Person: first and last name; company: name (§ 6.2.7–6.2.8). A provisional card
        (§ 6.2.9) may lack them; the fields of the other kind are refused anyway."""
        if self.kind == ClientCard.Kind.PERSON:
            if not self.first_name and not provisional:
                raise errors.validation_error("first_name", code="FIELD_REQUIRED")
            if not self.last_name and not provisional:
                raise errors.validation_error("last_name", code="FIELD_REQUIRED")
            if self.company_name:
                raise errors.validation_error("company_name", code="FIELD_NOT_APPLICABLE")
        elif self.kind == ClientCard.Kind.COMPANY:
            if not self.company_name and not provisional:
                raise errors.validation_error("company_name", code="FIELD_REQUIRED")
            for name in ("first_name", "last_name", "middle_name"):
                if getattr(self, name):
                    raise errors.validation_error(name, code="FIELD_NOT_APPLICABLE")
            if self.birth_date is not None:
                raise errors.validation_error("birth_date", code="FIELD_NOT_APPLICABLE")
        else:
            raise errors.validation_error("kind")
        if self.birth_date is not None and self.birth_date > timezone.localdate():
            raise errors.validation_error("birth_date", code="DATE_IN_FUTURE")


def has_minimum(card: ClientCard | Any, contacts: int) -> bool:
    """The minimum of § 6.2.7–6.2.8: the names (or the company name) and at least one contact."""
    if card.kind == ClientCard.Kind.COMPANY:
        return bool(card.company_name) and contacts > 0
    return bool(card.first_name and card.last_name) and contacts > 0


def snapshot(card: ClientCard) -> dict[str, Any]:
    return {
        "kind": card.kind,
        "first_name": card.first_name,
        "last_name": card.last_name,
        "middle_name": card.middle_name,
        "birth_date": card.birth_date.isoformat() if card.birth_date else None,
        "company_name": card.company_name,
        "address": card.address,
        "comment": card.comment,
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


def write_contacts(card: ClientCard, contacts: list[Contact]) -> None:
    ClientContact.objects.filter(client=card).delete()
    ClientContact.objects.bulk_create(
        ClientContact(
            company_id=card.company_id,
            client=card,
            kind=c.kind,
            value=c.value,
            value_normalized=c.normalized,
            messenger=c.messenger,
        )
        for c in contacts
    )


def create_client(
    ctx: CommandContext,
    company: Company,
    data: ClientData,
    contacts: list[Contact],
    *,
    possible_duplicate: bool = False,
    provisional: bool = False,
) -> ClientCard:
    """A local card without an account (§ 6.2.4, § 6.2.10). Possible duplicates do not block
    creation: the manager has compared them and decided (§ 6.1.11). A provisional card holds
    only the contact or the ID the request is sent to (§ 6.2.9)."""
    data.check_minimum(provisional=provisional)
    if not contacts and not provisional:
        raise errors.validation_error("contacts", code="CONTACT_REQUIRED")
    card = ClientCard.objects.create(
        company=company,
        kind=data.kind,
        first_name=data.first_name,
        last_name=data.last_name,
        middle_name=data.middle_name,
        birth_date=data.birth_date,
        company_name=data.company_name,
        address=data.address,
        comment=data.comment,
        external_id=data.external_id,
        possible_duplicate=possible_duplicate,
        provisional=provisional,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
        **data.extended,
    )
    write_contacts(card, contacts)
    ctx.audit("client.created", object_type="client_card", object_id=card.id, after=snapshot(card))
    return card


def lock_client(company_id: UUID, client_id: UUID) -> ClientCard:
    card = (
        ClientCard.objects.select_for_update().filter(company_id=company_id, pk=client_id).first()
    )
    if card is None:
        raise errors.not_found()
    return card


def update_client(
    ctx: CommandContext,
    company_id: UUID,
    client_id: UUID,
    changes: dict[str, Any],
    contacts: list[Contact] | None,
    expected_version: int,
) -> ClientCard:
    """Names, birth date, company name, the extended data and the full list of contacts; the kind
    does not change. The minimum of § 6.2.7–6.2.8 stays. A provisional card may stay incomplete;
    it becomes ready for operations once it holds the minimum (§ 6.2.10, PROPOSAL). A merged card
    is changed through its main card."""
    card = lock_client(company_id, client_id)
    if card.version != expected_version:
        raise errors.version_conflict()
    if card.state == ClientCard.State.MERGED:
        raise errors.business_rule("CLIENT_MERGED", main=str(card.merged_into_id))
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
        "client.changed",
        object_type="client_card",
        object_id=card.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return card


# --- Archive (§ 6.6.4–6.6.5) --------------------------------------------------------------------


def set_archived(
    ctx: CommandContext, company_id: UUID, client_id: UUID, archived: bool, expected_version: int
) -> ClientCard:
    """An archived card leaves the usual list and is found with the «Archive» filter; its data
    and history stay, the link with the profile is not broken (§ 6.6.4–6.6.5). Started operations
    go on; new ones need the card restored (PROPOSAL, § 6.6.5 «при повторном обращении карточку
    можно восстановить»)."""
    card = lock_client(company_id, client_id)
    if card.version != expected_version:
        raise errors.version_conflict()
    if card.state == ClientCard.State.MERGED:
        raise errors.business_rule("CLIENT_MERGED", main=str(card.merged_into_id))
    target = ClientCard.State.ARCHIVED if archived else ClientCard.State.ACTIVE
    if card.state == target:
        raise errors.business_rule("CLIENT_ALREADY_IN_STATE", state=target)
    card.state = target
    card.version += 1
    card.save(update_fields=["state", "version", "updated_at"])
    ctx.audit(
        "client.archived" if archived else "client.restored",
        object_type="client_card",
        object_id=card.id,
        before={"state": ClientCard.State.ACTIVE if archived else ClientCard.State.ARCHIVED},
        after={"state": target},
    )
    return card


# --- Blacklist (§ 6.7.1, § 23.8.7) ---------------------------------------------------------------


def active_restrictions(company_id: UUID, client_ids: list[UUID]) -> QuerySet[ClientRestriction]:
    return ClientRestriction.objects.filter(
        company_id=company_id, client_id__in=client_ids, lifted_at__isnull=True
    ).order_by("added_at")


def add_restriction(
    ctx: CommandContext, company_id: UUID, client_id: UUID, reason: str
) -> ClientRestriction:
    """Puts the client on the blacklist of the company with a reason; the author and the time
    are kept. A merged card is marked through its main card."""
    card = lock_client(company_id, client_id)
    if card.state == ClientCard.State.MERGED:
        raise errors.business_rule("CLIENT_MERGED", main=str(card.merged_into_id))
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    if active_restrictions(company_id, family_ids(company_id, card.id)).exists():
        raise errors.business_rule("CLIENT_ALREADY_BLACKLISTED")
    mark = ClientRestriction.objects.create(
        company_id=company_id,
        client=card,
        kind=ClientRestriction.Kind.BLACKLIST,
        reason=reason.strip(),
        added_by_user_id=ctx.actor.user_id,
        added_at=timezone.now(),
    )
    ctx.audit(
        "client.blacklisted",
        object_type="client_card",
        object_id=card.id,
        after={"restriction": str(mark.id), "kind": mark.kind},
        reason=mark.reason,
    )
    return mark


def lift_restriction(
    ctx: CommandContext, company_id: UUID, restriction_id: UUID, reason: str
) -> ClientRestriction:
    """Lifts the mark with a reason; the mark stays in the history with both authors."""
    peek = ClientRestriction.objects.filter(company_id=company_id, pk=restriction_id).first()
    if peek is None:
        raise errors.not_found()
    lock_client(company_id, peek.client_id)
    mark = ClientRestriction.objects.select_for_update().get(pk=restriction_id)
    if mark.lifted_at is not None:
        raise errors.business_rule("RESTRICTION_ALREADY_LIFTED")
    if not reason.strip():
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    mark.lifted_at = timezone.now()
    mark.lifted_by_user_id = ctx.actor.user_id
    mark.lifted_reason = reason.strip()
    mark.save(update_fields=["lifted_at", "lifted_by_user_id", "lifted_reason"])
    ctx.audit(
        "client.blacklist_lifted",
        object_type="client_card",
        object_id=mark.client_id,
        before={"restriction": str(mark.id)},
        reason=mark.lifted_reason,
    )
    return mark


def restriction_payload(mark: ClientRestriction, names: dict[UUID, str]) -> dict[str, Any]:
    return {
        "id": str(mark.id),
        "kind": mark.kind,
        "client_id": str(mark.client_id),
        "reason": mark.reason,
        "added_at": mark.added_at.isoformat(),
        "added_by": names.get(mark.added_by_user_id, ""),
        "lifted_at": mark.lifted_at.isoformat() if mark.lifted_at else None,
        "lifted_by": names.get(mark.lifted_by_user_id, "") if mark.lifted_by_user_id else None,
        "lifted_reason": mark.lifted_reason,
    }


# --- Merged cards (§ 6.5.2) ----------------------------------------------------------------------


def main_card_id(company_id: UUID, client_id: UUID) -> UUID:
    """The card a merged card leads to, through a chain of merges."""
    current = client_id
    for _ in range(100):
        row = (
            ClientCard.objects.filter(company_id=company_id, pk=current)
            .values_list("state", "merged_into_id")
            .first()
        )
        if row is None or row[0] != ClientCard.State.MERGED or row[1] is None:
            return current
        current = row[1]
    return current


def family_ids(company_id: UUID, client_id: UUID) -> list[UUID]:
    """The card and the cards merged into it, directly or through other merges: their records
    are shown from it (§ 6.5.2)."""
    found = [client_id]
    frontier = [client_id]
    while frontier:
        frontier = list(
            ClientCard.objects.filter(
                company_id=company_id, merged_into_id__in=frontier, state=ClientCard.State.MERGED
            ).values_list("id", flat=True)
        )
        found += [i for i in frontier if i not in found]
    return found


# --- Use in operations (§ 6.2.10, § 6.6.5, § 6.7.1) ----------------------------------------------


def require_operable(company_id: UUID, client_id: UUID, field: str = "client_id") -> ClientCard:
    """A card that may take a new order or a new service operation: not merged (its main card is
    used), not archived (it is restored first, PROPOSAL), not provisional (§ 6.2.10), and not on
    the blacklist of the company (§ 6.7.1; a mark of a card merged into it counts). Started
    operations, settlements and refunds do not call this."""
    card = clients_of(company_id).filter(pk=client_id).first()
    if card is None:
        raise errors.not_found()
    if card.state == ClientCard.State.MERGED:
        raise errors.business_rule(
            "CLIENT_MERGED", field=field, main=str(main_card_id(company_id, card.id))
        )
    if card.state == ClientCard.State.ARCHIVED:
        raise errors.business_rule("CLIENT_ARCHIVED", field=field)
    if card.provisional:
        raise errors.business_rule("CLIENT_PROVISIONAL", field=field)
    if active_restrictions(company_id, family_ids(company_id, card.id)).exists():
        raise errors.business_rule("CLIENT_BLACKLISTED", field=field)
    return card


# --- Reading ----------------------------------------------------------------------------------


def display_name(card: ClientCard) -> str:
    """Name of a client for other modules: company name or «First Last»."""
    if card.company_name:
        return card.company_name
    name = " ".join(part for part in (card.first_name, card.last_name) if part)
    if name or not card.provisional:
        return name
    # A provisional card holds only the contact the request was sent to (§ 6.2.9).
    contact = card.contacts.order_by("kind", "value").first()
    if contact is not None:
        return contact.value
    # Or the ID the request was sent to.
    label = (
        ProfileLink.objects.filter(client_id=card.id)
        .order_by("-created_at")
        .values_list("target_label", flat=True)
        .first()
    )
    return label or ""


def clients_of(company_id: UUID) -> QuerySet[ClientCard]:
    return ClientCard.objects.filter(company_id=company_id).prefetch_related("contacts")


def get_client(company_id: UUID, client_id: UUID) -> ClientCard:
    card = clients_of(company_id).filter(pk=client_id).first()
    if card is None:
        raise errors.not_found()
    return card


def search(
    company_id: UUID, text: str, state: str = ClientCard.State.ACTIVE
) -> QuerySet[ClientCard]:
    """List with search by a part of the name (§ 2.8.3); exact matches first is left to ordering
    by name. The usual list shows active cards; the «Archive» filter the archived ones (§ 6.6.4);
    merged cards are reached through their main card (§ 6.5.2)."""
    cards = clients_of(company_id).filter(state=state)
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
    # A merged card is offered as its main card (§ 6.5.2).
    for client_id in list(reasons):
        main = main_card_id(company_id, client_id)
        if main != client_id:
            reasons.setdefault(main, set()).update(reasons.pop(client_id))
    found = {
        c.id: c
        for c in clients_of(company_id)
        .filter(pk__in=reasons)
        .exclude(state=ClientCard.State.MERGED)
    }
    reasons = {k: v for k, v in reasons.items() if k in found}

    def order(client_id: UUID) -> tuple[int, int, str]:
        matched = reasons[client_id]
        by_contact = bool(matched & {"phone", "email"})
        return (0 if by_contact else 1, -len(matched), str(found[client_id]))

    return [(found[i], sorted(reasons[i])) for i in sorted(found, key=order)]
