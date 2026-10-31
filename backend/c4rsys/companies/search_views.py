"""The single search line of the active company (ТЗ § 2.8.2–2.8.3). A read: no business
operation, no Idempotency-Key. Each group is found by its module with the rights of the section
(c4rsys.platform.search), so the search widens no rights."""

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import services
from c4rsys.platform import errors, search

MIN_TEXT = 1


@require_GET
def company_search(request: HttpRequest, cmp: str) -> JsonResponse:
    """`q` — the text; `kinds` — a comma-separated list to search only these groups."""
    user, session = require_user(request)
    context = services.resolve_company(user, cmp, session.id)
    actor = services.company_actor(user, context, session.id)
    text = request.GET.get("q", "")
    only = [k for k in request.GET.get("kinds", "").split(",") if k]
    if any(k not in search.kinds() for k in only):
        raise errors.validation_error("kinds")
    query = search.parse(text)
    if len(query.text) < MIN_TEXT:
        return JsonResponse({"query": query.text, "groups": []})
    found = search.search(actor, context.company.id, query.text, only or None)
    return JsonResponse(
        {
            "query": query.text,
            "groups": [{"kind": kind, "hits": hits} for kind, hits in found.items()],
        }
    )
