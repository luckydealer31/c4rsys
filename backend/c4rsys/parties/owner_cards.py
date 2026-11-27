"""The owner of a vehicle as a card of the company (customer's decisions of 25.11.2026).

The owner of a managed vehicle is an investor of the company (customer's decision of
26.11.2026, in place of the counterparty of 25.11.2026): he gives the vehicle into the
management; the investors are a list of their own, kept as `Counterparty` of the role
«investor». The owner of a client vehicle is a client. In the form of the vehicle the owner is a
person or a company: a card of the list is chosen, or a new one is entered with the usual data
of the card (§ 6.2.7–6.2.8) and created with the vehicle in one command.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from c4rsys.companies.models import Company
from c4rsys.parties import counterparties, extended, services
from c4rsys.parties.models import ClientCard, Counterparty
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.http import get_str

CLIENT = "client"
COUNTERPARTY = "counterparty"
NAME_MAX = 150
COMPANY_NAME_MAX = 200


@dataclass(frozen=True)
class NewCard:
    kind: str
    last_name: str = ""
    first_name: str = ""
    middle_name: str = ""
    company_name: str = ""
    # The contact person of a company (customer's remark of 25.11.2026).
    contact_person: str = ""
    address: str = ""
    contacts: tuple[services.Contact, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "last_name": self.last_name,
            "first_name": self.first_name,
            "middle_name": self.middle_name,
            "company_name": self.company_name,
            "contact_person": self.contact_person,
            "address": self.address,
            "contacts": [c.as_dict() for c in self.contacts],
        }

    def query(self) -> services.MatchQuery:
        return services.MatchQuery(
            first_name=self.first_name,
            last_name=self.last_name,
            company_name=self.company_name,
            contacts=self.contacts,
        )


def parse_new(data: Any) -> NewCard:
    """The usual minimum of a card: a person by the last and the first name, a company by its
    name, and at least one contact (§ 6.2.7–6.2.8)."""
    if not isinstance(data, dict):
        raise errors.validation_error("owner_card")
    kind = get_str(data, "kind", max_length=10)
    person = kind == ClientCard.Kind.PERSON
    card = NewCard(
        kind=kind,
        last_name=get_str(data, "last_name", max_length=NAME_MAX, required=False) if person else "",
        first_name=get_str(data, "first_name", max_length=NAME_MAX, required=False)
        if person
        else "",
        middle_name=get_str(data, "middle_name", max_length=NAME_MAX, required=False)
        if person
        else "",
        company_name=""
        if person
        else get_str(data, "company_name", max_length=COMPANY_NAME_MAX, required=False),
        contact_person=""
        if person
        else get_str(data, "contact_person", max_length=200, required=False),
        address=get_str(data, "address", max_length=500, required=False),
        contacts=tuple(services.parse_contacts(data.get("contacts", []), required=True)),
    )
    services.ClientData(
        kind=card.kind,
        first_name=card.first_name,
        last_name=card.last_name,
        company_name=card.company_name,
    ).check_minimum()
    return card


def create(
    ctx: CommandContext, company: Company, card_type: str, new: NewCard
) -> ClientCard | Counterparty:
    """A card with the same data is not merged: the new one is marked as a possible duplicate
    (§ 6.5.1); the form has shown the matches before."""
    if card_type == COUNTERPARTY:
        return counterparties.create(
            ctx,
            company,
            counterparties.CounterpartyData(
                kind=new.kind,
                role=counterparties.INVESTOR,
                first_name=new.first_name,
                last_name=new.last_name,
                middle_name=new.middle_name,
                company_name=new.company_name,
                address=new.address,
                extended={"contact_person": new.contact_person} if new.contact_person else {},
            ),
            list(new.contacts),
            possible_duplicate=bool(
                counterparties.matches(company.id, new.query(), counterparties.INVESTOR)
            ),
        )
    return services.create_client(
        ctx,
        company,
        services.ClientData(
            kind=new.kind,
            first_name=new.first_name,
            last_name=new.last_name,
            middle_name=new.middle_name,
            company_name=new.company_name,
            address=new.address,
            extended={"contact_person": new.contact_person} if new.contact_person else {},
        ),
        list(new.contacts),
        possible_duplicate=bool(services.matches(company.id, new.query())),
    )


def _contacts(investor: Counterparty) -> list[services.Contact]:
    return [
        services.Contact(c.kind, c.value, c.value_normalized, c.messenger)
        for c in investor.contacts.order_by("kind", "value")
    ]


def clients_like(company_id: UUID, investor: Counterparty) -> list[tuple[ClientCard, list[str]]]:
    """The clients of the company alike the investor, with what matched (§ 6.1.11): one of them
    may be chosen as the owner when the management ends (customer's decision of 27.11.2026).
    Only the active cards are offered."""
    found = services.matches(
        company_id,
        services.MatchQuery(
            first_name=investor.first_name,
            last_name=investor.last_name,
            birth_date=investor.birth_date,
            company_name=investor.company_name,
            contacts=tuple(_contacts(investor)),
        ),
    )
    return [(c, why) for c, why in found if c.state == ClientCard.State.ACTIVE]


def client_of_investor(ctx: CommandContext, company: Company, investor: Counterparty) -> ClientCard:
    """The management of a vehicle is ended and it becomes a client's one: its owner becomes a
    client with the same data as the investor had (customer's decision of 27.11.2026). The card
    of the investor stays. The data are copied as they are, without the minimum of a new card;
    a client alike is not merged — the new card is marked as a possible duplicate (§ 6.5.1)."""
    contacts = _contacts(investor)
    alike = clients_like(company.id, investor)
    card = ClientCard.objects.create(
        company=company,
        kind=investor.kind,
        first_name=investor.first_name,
        last_name=investor.last_name,
        middle_name=investor.middle_name,
        birth_date=investor.birth_date,
        company_name=investor.company_name,
        address=investor.address,
        possible_duplicate=bool(alike),
        created_by_user_id=ctx.actor.user_id,
        **{name: getattr(investor, name) for name in extended.FIELDS},
    )
    services.write_contacts(card, contacts)
    ctx.audit(
        "client.created",
        object_type="client_card",
        object_id=card.id,
        after={**services.snapshot(card), "copied_from_investor": str(investor.id)},
    )
    return card


def chosen(company_id: UUID, card_type: str, card_id: UUID) -> ClientCard | Counterparty:
    """A card of the list that is not archived nor merged."""
    model: type[ClientCard] | type[Counterparty] = (
        Counterparty if card_type == COUNTERPARTY else ClientCard
    )
    card = model.objects.filter(company_id=company_id, pk=card_id).first()
    if isinstance(card, Counterparty) and card.role != counterparties.INVESTOR:
        # Only an investor is the owner of a managed vehicle (decision of 26.11.2026).
        card = None
    field = "owner_counterparty_id" if card_type == COUNTERPARTY else "owner_client_id"
    if card is None:
        raise errors.validation_error(field, code="CLIENT_UNKNOWN")
    if card.state != model.State.ACTIVE:
        raise errors.validation_error(field, code="OWNER_CARD_NOT_ACTIVE")
    return card


def full_name(card: ClientCard | Counterparty) -> str:
    """«Last First Middle» of a person, the name of a company."""
    if card.company_name:
        return card.company_name
    name = " ".join(p for p in (card.last_name, card.first_name, card.middle_name) if p)
    if name:
        return name
    if isinstance(card, Counterparty):
        return counterparties.display_name(card)
    return services.display_name(card)


def first_contact(card: ClientCard | Counterparty) -> str:
    """The email, else the phone, of the card: the request to the owner goes to it when no ID
    is given (agent's choice, not confirmed)."""
    found: list[tuple[str, str]] = [(c.kind, c.value) for c in card.contacts.all()]
    for kind in ("email", "phone"):
        for given, value in found:
            if given == kind:
                return value
    return ""
