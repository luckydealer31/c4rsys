"""The lists of cards of a company and their rights (ТЗ § 6.1.7, § 23.8.2; customer's decision of
26.11.2026).

A company keeps three lists apart: the clients, the counterparties and the investors — those who
gave vehicles into the management or put in money. Each list has rights of its own. The cards
of the counterparties and of the investors are kept in one table (`Counterparty.role`), so the
HTTP layer names the list, checks that the card is of it and passes the kind of the stored card
(«client» or «counterparty») on to the services.
"""

from typing import Any
from uuid import UUID

from c4rsys.parties.models import CardMerge, Counterparty, ProfileLink
from c4rsys.platform import errors

CLIENT = "client"
COUNTERPARTY = "counterparty"
INVESTOR = "investor"
LISTS = (CLIENT, COUNTERPARTY, INVESTOR)

VIEW: dict[str, tuple[str, ...]] = {
    CLIENT: ("client.find", "client.list"),
    COUNTERPARTY: ("counterparty.view",),
    INVESTOR: ("investor.view",),
}
EDIT = {name: f"{name}.edit" for name in LISTS}
# § 23.8.2 (block 14): sending an invitation or a request to link is a right of its own.
REQUEST = {name: f"{name}.link.request" for name in LISTS}
MERGE = {name: f"{name}.merge" for name in LISTS}
SPLIT = {name: f"{name}.split" for name in LISTS}


def parse(value: Any, field: str = "card_type") -> str:
    if value not in LISTS:
        raise errors.validation_error(field)
    return str(value)


def stored(name: str) -> str:
    """The kind of the stored card: an investor is kept as a counterparty."""
    return CLIENT if name == CLIENT else COUNTERPARTY


def require_card(company_id: UUID, name: str, card_id: UUID) -> None:
    """A card of another list is not found: the right of one list does not open the other."""
    if name == CLIENT:
        return
    if not Counterparty.objects.filter(company_id=company_id, pk=card_id, role=name).exists():
        raise errors.not_found()


def _of_counterparty(card_id: UUID | None) -> str:
    if card_id is None:
        return COUNTERPARTY
    role = Counterparty.objects.filter(pk=card_id).values_list("role", flat=True).first()
    return str(role or COUNTERPARTY)


def of_link(link: ProfileLink) -> str:
    return CLIENT if link.client_id is not None else _of_counterparty(link.counterparty_id)


def of_merge(merge: CardMerge) -> str:
    if merge.card_type == CLIENT:
        return CLIENT
    return _of_counterparty(merge.primary_counterparty_id)
