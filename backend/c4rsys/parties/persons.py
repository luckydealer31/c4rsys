"""One person or company behind several cards (customer's decisions of 28.11.2026).

A client, an investor and a counterparty are one being with roles: the data — the name, the
birth date, the contacts, the addresses, the documents and their photos — are the same in every
role, and what happened to it in a role (rentals, vehicles given, supplies) stays with the role.

The first step: the cards stay where they are — the clients in their table, the investors and
the counterparties in theirs — so that nothing that points at a card is touched; a `Party` joins
the cards of one being, and a change of the common data of one card is written to the others in
the same command. A being has one card of a role at most. The comment, the outer id and the
archive belong to the role; the blacklist — to the client."""

from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.companies import access
from c4rsys.companies.models import Company, Employment
from c4rsys.parties import counterparties, extended, services
from c4rsys.parties.models import ClientCard, Counterparty, Party
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext

Card = ClientCard | Counterparty
CLIENT = "client"
ROLES = (CLIENT, counterparties.INVESTOR, counterparties.COUNTERPARTY)
# What is the same in every role.
COMMON = (
    "first_name",
    "last_name",
    "middle_name",
    "birth_date",
    "company_name",
    "address",
    *extended.FIELDS,
    "papers",
)
VIEW = {CLIENT: ("client.find", "client.list"), "investor": ("investor.view",)}
JOIN = "party.join"
# Who sees the card of an employee (companies/staff_views.py).
EMPLOYEE_VIEW = (
    "employee.invite",
    "employee.rights.manage",
    "employee.access.block",
    "employee.access.restore",
    "employee.access.terminate",
    "employee.card.edit",
    "employee.positions.manage",
    "org.manage",
    "employee.sets.manage",
)


def role_of(card: Card) -> str:
    return CLIENT if isinstance(card, ClientCard) else card.role


def get(company_id: UUID, role: str, card_id: UUID) -> Card:
    if role == CLIENT:
        return services.get_client(company_id, card_id)
    if role not in ROLES:
        raise errors.validation_error("card_type", code="VALUE_UNKNOWN")
    return counterparties.get(company_id, card_id, role)


def _may(actor: Actor, *permissions: str) -> bool:
    resolver = access.RightsResolver()
    return any(resolver.has_permission(actor, p) for p in permissions)


def may_view(actor: Actor, role: str) -> bool:
    return _may(actor, *VIEW.get(role, (f"{role}.view",)))


def require_view(actor: Actor, role: str) -> None:
    if not may_view(actor, role):
        raise errors.permission_denied(VIEW.get(role, (f"{role}.view",))[0])


def _alive(cards: Any) -> list[Card]:
    return list(cards.exclude(state="merged"))


def cards_of(party_id: UUID | None) -> list[Card]:
    """The cards of the being, by the order of the roles; merged cards are not counted."""
    if party_id is None:
        return []
    found: list[Card] = [
        *_alive(ClientCard.objects.filter(party_id=party_id)),
        *_alive(Counterparty.objects.filter(party_id=party_id)),
    ]
    return sorted(found, key=lambda c: ROLES.index(role_of(c)))


def siblings(card: Card) -> list[Card]:
    return [c for c in cards_of(card.party_id) if (role_of(c), c.id) != (role_of(card), card.id)]


def _party(card: Card) -> Party:
    party = card.party
    if party is None:
        party = Party.objects.create(company_id=card.company_id)
        card.party = party
        card.save(update_fields=["party"])
    return party


def _contacts(card: Card) -> list[services.Contact]:
    return [
        services.Contact(c.kind, c.value, c.value_normalized, c.messenger)
        for c in card.contacts.order_by("kind", "value")
    ]


def _write(target: Card, source: Card, contacts: list[services.Contact]) -> None:
    for name in COMMON:
        setattr(target, name, getattr(source, name))
    target.version += 1
    target.save()
    if isinstance(target, ClientCard):
        services.write_contacts(target, contacts)
    else:
        counterparties.write_contacts(target, contacts)


def sync_from(ctx: CommandContext, card: Card) -> None:
    """The common data of the card are written to the other cards of the being."""
    others = siblings(card)
    if not others:
        return
    contacts = _contacts(card)
    for other in others:
        _write(other, card, contacts)
        ctx.audit(
            "card.common_data_changed",
            object_type="client_card" if isinstance(other, ClientCard) else other.role,
            object_id=other.id,
            after={"from": role_of(card), "card": str(card.id)},
        )


def join(ctx: CommandContext, main: Card, other: Card) -> None:
    """Two cards are of one being: the data of `main` become the data of both. Of one company,
    of one kind, of different roles; a being has one card of a role."""
    if main.company_id != other.company_id or (role_of(main), main.id) == (
        role_of(other),
        other.id,
    ):
        raise errors.validation_error("other_id")
    if main.kind != other.kind:
        raise errors.business_rule("PARTY_KIND_DIFFERS")
    for card in (main, other):
        if card.state == "merged":
            raise errors.business_rule("PARTY_CARD_MERGED")
    if main.party_id is not None and main.party_id == other.party_id:
        raise errors.business_rule("PARTY_ALREADY_JOINED")
    together = [main, *siblings(main), other, *siblings(other)]
    roles = [role_of(c) for c in together]
    if len(set(roles)) != len(roles):
        raise errors.business_rule("PARTY_ROLE_TAKEN")
    party = _party(main)
    for card in together[1:]:
        card.party = party
        card.save(update_fields=["party"])
    from c4rsys.tags import services as tags

    tags.unite(ctx, main.company_id, [(role_of(c), c.id) for c in together])
    sync_from(ctx, main)
    ctx.audit(
        "party.joined",
        object_type="client_card" if isinstance(main, ClientCard) else main.role,
        object_id=main.id,
        after={"cards": [f"{role_of(c)}:{c.id}" for c in together]},
    )


def leave(ctx: CommandContext, card: Card) -> None:
    """The card is of a being of its own again; its data stay as they are."""
    if not siblings(card):
        raise errors.business_rule("PARTY_ALONE")
    left = card.party_id
    card.party = Party.objects.create(company_id=card.company_id)
    card.save(update_fields=["party"])
    ctx.audit(
        "party.left",
        object_type="client_card" if isinstance(card, ClientCard) else card.role,
        object_id=card.id,
        before={"party": str(left)},
    )


def add_role(ctx: CommandContext, company: Company, card: Card, role: str) -> Card:
    """The being gets one more role: a card of that list with the same data. Nothing is entered
    twice (customer's decision of 28.11.2026)."""
    if role not in ROLES:
        raise errors.validation_error("role", code="VALUE_UNKNOWN")
    if role in [role_of(c) for c in cards_of(_party(card).id)]:
        raise errors.business_rule("PARTY_ROLE_TAKEN")
    common = {name: getattr(card, name) for name in COMMON}
    made: Card
    if role == CLIENT:
        made = ClientCard.objects.create(
            company=company,
            kind=card.kind,
            party=card.party,
            created_by_user_id=ctx.actor.user_id,
            created_at=timezone.now(),
            **common,
        )
        services.write_contacts(made, _contacts(card))
        ctx.audit(
            "client.created",
            object_type="client_card",
            object_id=made.id,
            after={**services.snapshot(made), "role_of": f"{role_of(card)}:{card.id}"},
        )
    else:
        made = Counterparty.objects.create(
            company=company,
            role=role,
            kind=card.kind,
            party=card.party,
            created_by_user_id=ctx.actor.user_id,
            created_at=timezone.now(),
            **common,
        )
        counterparties.write_contacts(made, _contacts(card))
        ctx.audit(
            "counterparty.created",
            object_type=role,
            object_id=made.id,
            after={**counterparties.snapshot(made), "role_of": f"{role_of(card)}:{card.id}"},
        )
    from c4rsys.tags import services as tags

    tags.unite(ctx, company.id, [(role_of(c), c.id) for c in cards_of(card.party_id)])
    return made


def card_of_role(card: Card, role: str) -> Card | None:
    """The active card of the being in the role, if it has one."""
    return next(
        (c for c in cards_of(card.party_id) if role_of(c) == role and c.state == "active"), None
    )


def same_being(company_id: UUID, object_type: str, object_id: UUID) -> list[tuple[str, UUID]]:
    """The other cards of the being of a card, for the tags: a tag is of the being."""
    if object_type not in ROLES:
        return []
    model = ClientCard if object_type == CLIENT else Counterparty
    card = model.objects.filter(company_id=company_id, pk=object_id).first()
    if card is None or card.party_id is None:
        return []
    return [(role_of(c), c.id) for c in siblings(card)]


def _name(card: Card) -> str:
    if isinstance(card, ClientCard):
        return services.display_name(card)
    return counterparties.display_name(card)


def _employment(card: Card) -> Employment | None:
    """The employee of the company the being is, by the profile its card is linked with."""
    users = {c.linked_user_id for c in [card, *siblings(card)] if c.linked_user_id}
    if not users:
        return None
    return (
        Employment.objects.filter(company_id=card.company_id, user_id__in=users)
        .exclude(state=Employment.State.TERMINATED)
        .first()
    )


def _like(card: Card, role: str) -> list[tuple[Card, list[str]]]:
    query = services.MatchQuery(
        first_name=card.first_name,
        last_name=card.last_name,
        birth_date=card.birth_date,
        company_name=card.company_name,
        contacts=tuple(_contacts(card)),
    )
    found: list[tuple[Any, list[str]]] = (
        services.matches(card.company_id, query)
        if role == CLIENT
        else counterparties.matches(card.company_id, query, role)
    )
    return [(c, why) for c, why in found if c.state == "active" and c.kind == card.kind]


def payload(card: Card, actor: Actor) -> dict[str, Any]:
    """The roles of the being as the employee may see them, and what may be done."""
    cards = cards_of(card.party_id) or [card]
    taken = {role_of(c) for c in cards}
    roles = [
        {
            "role": role_of(c),
            "id": str(c.id),
            "state": c.state,
            "current": (role_of(c), c.id) == (role_of(card), card.id),
            # The comment belongs to the role and is shown with its name.
            "comment": c.comment,
        }
        for c in cards
        if may_view(actor, role_of(c))
    ]
    may_join = _may(actor, JOIN)
    employment = _employment(card)
    offered = []
    if may_join:
        for role in ROLES:
            if role in taken or not may_view(actor, role):
                continue
            for found, why in _like(card, role):
                if found.party_id is not None and found.party_id == card.party_id:
                    continue
                offered.append(
                    {"role": role, "id": str(found.id), "name": _name(found), "matched": why}
                )
    return {
        "roles": roles,
        "hidden": len(cards) - len(roles),
        "can_add": [r for r in ROLES if r not in taken and _may(actor, f"{r}.create")],
        "may_join": may_join,
        "offered": offered,
        "employee": {"id": str(employment.id)}
        if employment is not None and _may(actor, *EMPLOYEE_VIEW)
        else None,
    }


def of_employee(company_id: UUID, user_id: UUID, actor: Actor) -> list[dict[str, Any]]:
    """The cards of the employee as a client, an investor or a counterparty."""
    found: list[Card] = [
        *_alive(ClientCard.objects.filter(company_id=company_id, linked_user_id=user_id)),
        *_alive(Counterparty.objects.filter(company_id=company_id, linked_user_id=user_id)),
    ]
    return [
        {"role": role_of(c), "id": str(c.id), "name": _name(c)}
        for c in sorted(found, key=lambda c: ROLES.index(role_of(c)))
        if may_view(actor, role_of(c))
    ]
