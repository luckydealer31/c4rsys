"""Tasks in the single search and in the list of tasks: by the number or the id, a part of the
title and the tags (ТЗ § 2.8.3, § 19.4.7). A task is found only where it is visible."""

from uuid import UUID

from django.db.models import Q, QuerySet

from c4rsys.companies import access
from c4rsys.platform.commands import Actor
from c4rsys.platform.search import Hit, Query, exact_text
from c4rsys.tags import services as tags
from c4rsys.tasks import services
from c4rsys.tasks.models import Task


def matching(found: QuerySet[Task], query: Query, company_id: UUID) -> QuerySet[Task]:
    # The tags of a task are the tags of the company (customer's decision of 17.11.2026).
    tagged = tags.matching_ids(company_id, None, "task", query.text)
    condition = Q(title__icontains=query.text) | Q(pk__in=tagged)
    if query.number is not None:
        condition |= Q(number=query.number)
    if query.uuid is not None:
        condition |= Q(pk=query.uuid)
    return found.filter(condition).distinct()


def provider(actor: Actor, company_id: UUID, query: Query, limit: int) -> list[Hit]:
    resolver = access.RightsResolver()
    hits = []
    tagged = tags.matching_ids(company_id, None, "task", query.text)
    for t in matching(services.tasks_of(company_id), query, company_id).order_by("-number")[
        : limit * 3
    ]:
        if not services.can_view(actor, t, resolver):
            continue
        exact = (
            t.number == query.number
            or t.pk == query.uuid
            or exact_text(t.title, query)
            or t.id in tagged
        )
        hits.append(Hit("task", str(t.id), str(t.number), t.title, exact=exact))
    return hits[: limit * 2]
