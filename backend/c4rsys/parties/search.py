"""Clients and counterparties in the single search, by the data their lists are searched by
(ТЗ § 2.8.3, § 6.2.2): a part of the name (and a contact of a counterparty) or the id. The list
rights count: «View the list of clients», «View counterparties»."""

from uuid import UUID

from c4rsys.companies import access
from c4rsys.parties import counterparties, services
from c4rsys.parties.models import ClientCard, Counterparty
from c4rsys.platform.commands import Actor
from c4rsys.platform.search import Hit, Query, exact_text


def clients(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    if not access.RightsResolver().has_permission(actor, "client.list"):
        return []
    found: list[ClientCard] = []
    if query.uuid is not None:
        found += list(services.clients_of(company_id).filter(pk=query.uuid))
    found += list(services.search(company_id, query.text)[: limit * 2])
    hits: list[Hit] = []
    for card in found:
        if any(h.id == str(card.id) for h in hits):
            continue
        exact = card.pk == query.uuid or exact_text(str(card), query)
        hits.append(Hit("client", str(card.id), str(card), card.kind, exact=exact))
    return hits


def counterparty_hits(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    if not access.RightsResolver().has_permission(actor, "counterparty.view"):
        return []
    found: list[Counterparty] = []
    if query.uuid is not None:
        found += list(counterparties.of_company(company_id).filter(pk=query.uuid))
    found += list(counterparties.search(company_id, query.text)[: limit * 2])
    hits: list[Hit] = []
    for card in found:
        if any(h.id == str(card.id) for h in hits):
            continue
        exact = card.pk == query.uuid or exact_text(str(card), query)
        hits.append(Hit("counterparty", str(card.id), str(card), card.kind, exact=exact))
    return hits
