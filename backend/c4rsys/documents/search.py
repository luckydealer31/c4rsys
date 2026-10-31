"""Documents in the single search and in the list of documents: by the number or the id and a
part of the title (ТЗ § 2.8.3). A document is found only in its scope (§ 18.5.4)."""

from uuid import UUID

from django.db.models import Q, QuerySet

from c4rsys.companies import access
from c4rsys.documents import services
from c4rsys.documents.models import Document
from c4rsys.platform.commands import Actor
from c4rsys.platform.search import Hit, Query, exact_text


def matching(found: QuerySet[Document], query: Query) -> QuerySet[Document]:
    condition = Q(title__icontains=query.text)
    if query.number is not None:
        condition |= Q(number=query.number)
    if query.uuid is not None:
        condition |= Q(pk=query.uuid)
    return found.filter(condition)


def provider(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    resolver = access.RightsResolver()
    hits = []
    for d in matching(services.documents_of(company_id), query).order_by("-number")[: limit * 3]:
        if not services.can(actor, "documents.view", d, resolver):
            continue
        exact = d.number == query.number or d.pk == query.uuid or exact_text(d.title, query)
        hits.append(Hit("document", str(d.id), str(d.number), d.title, exact=exact))
    return hits[: limit * 2]
