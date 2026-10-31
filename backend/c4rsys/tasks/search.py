"""Tasks in the single search and in the list of tasks: by the number or the id, a part of the
title and the tags (ТЗ § 2.8.3, § 19.4.7). A task is found only where it is visible."""

from uuid import UUID

from django.db.models import Q, QuerySet

from c4rsys.companies import access
from c4rsys.platform.commands import Actor
from c4rsys.platform.search import Hit, Query, exact_text
from c4rsys.tasks import services
from c4rsys.tasks.models import Task


def matching(found: QuerySet[Task], query: Query) -> QuerySet[Task]:
    condition = Q(title__icontains=query.text) | Q(tag_links__tag__name__iexact=query.text)
    if query.number is not None:
        condition |= Q(number=query.number)
    if query.uuid is not None:
        condition |= Q(pk=query.uuid)
    return found.filter(condition).distinct()


def provider(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    resolver = access.RightsResolver()
    hits = []
    for t in matching(services.tasks_of(company_id), query).order_by("-number")[: limit * 3]:
        if not services.can_view(actor, t, resolver):
            continue
        exact = (
            t.number == query.number
            or t.pk == query.uuid
            or exact_text(t.title, query)
            or t.tag_links.filter(tag__name__iexact=query.text).exists()
        )
        hits.append(Hit("task", str(t.id), str(t.number), t.title, exact=exact))
    return hits[: limit * 2]
