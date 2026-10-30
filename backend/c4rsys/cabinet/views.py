"""HTTP API of the personal cabinet (ТЗ § 7.1, § 2.1.8; decisions Б-1, Б-2, Б-3 of 30.10.2026).

The cabinet has no tables of its own: it reads the history, the documents and the garage of the
profile through the services of the modules that own them. The same account signs in on the
cabinet domain separately (customer's decision of 30.10.2026).
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies.views import run_and_respond
from c4rsys.documents import cabinet as documents
from c4rsys.fleet import owner_requests
from c4rsys.orders import services as orders
from c4rsys.parties import links
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money

HISTORY_LIMIT = 100


def is_cabinet_host(request: HttpRequest) -> bool:
    host = request.META.get("HTTP_HOST", "").split(":")[0].lower()
    return host in settings.C4R_CABINET_HOSTS


@require_GET
def site(request: HttpRequest) -> JsonResponse:
    """Which interface this address serves: the personal cabinet or the B2B one."""
    return JsonResponse({"cabinet": is_cabinet_host(request)})


@require_GET
def companies(request: HttpRequest) -> JsonResponse:
    """Companies linked to the profile (§ 7.1.3); a broken link keeps what was shared."""
    user, _session = require_user(request)
    items = [
        {
            "link_id": str(link.id),
            "company": {
                "public_id": link.company.public_id,
                "display_name": link.company.display_name,
            },
            "card_type": links.card_type_of(link),
            "state": link.state,
            "linked_at": link.decided_at.isoformat() if link.decided_at else None,
            "broken_at": link.broken_at.isoformat() if link.broken_at else None,
        }
        for link in links.links_of_user(user.id)
    ]
    return JsonResponse({"companies": items})


def _money(currency: str, value: Decimal, places: dict[str, int]) -> str:
    if currency not in places:
        found = Currency.objects.filter(code=currency).values_list("minor_units", flat=True)
        places[currency] = found.first() or 2
    return str(round_money(value, places[currency]))


def _order(order: Any, places: dict[str, int]) -> dict[str, Any]:
    view = orders.client_view(order)
    view["lines"] = [
        {**line, "amount": _money(line["currency"], line["amount"], places)}
        for line in view["lines"]
    ]
    view["amounts"] = {
        currency: {
            "charged": _money(currency, m.charged, places),
            "paid": _money(currency, m.paid, places),
            "to_pay": _money(currency, max(m.balance, Decimal(0)), places),
            "overpaid": _money(currency, max(-m.balance, Decimal(0)), places),
        }
        for currency, m in sorted(view["amounts"].items())
    }
    return view


@require_GET
def history(request: HttpRequest, link_id: UUID) -> JsonResponse:
    """The allowed history of a linked company: its orders with rentals, service, purchases and
    the amounts (decision Б-1). After a break only the orders from before it (§ 6.6.3)."""
    user, _session = require_user(request)
    link = links.links_of_user(user.id).filter(pk=link_id).first()
    if link is None:
        raise errors.not_found()
    if link.client_id is None:
        return JsonResponse({"orders": []})
    places: dict[str, int] = {}
    found = orders.client_orders(link.company_id, [link.client_id], link.broken_at)
    return JsonResponse({"orders": [_order(o, places) for o in found[:HISTORY_LIMIT]]})


@require_GET
def document_list(request: HttpRequest) -> JsonResponse:
    user, _session = require_user(request)
    return JsonResponse(
        {"documents": [documents.payload(d, user.id) for d in documents.documents_of(user.id)]}
    )


@require_GET
def document_detail(request: HttpRequest, document_id: UUID) -> JsonResponse:
    user, _session = require_user(request)
    d = documents.get(user.id, document_id)
    return JsonResponse({"document": documents.payload(d, user.id, detail=True)})


@require_POST
def document_code(request: HttpRequest, document_id: UUID) -> JsonResponse:
    """A one-time code to the email of the profile (§ 18.3.9)."""
    user, session = require_user(request)
    d = documents.get(user.id, document_id)

    def handler(ctx: CommandContext) -> CommandResult:
        return CommandResult(200, {"sent_to": documents.request_code(ctx, d)})

    actor = Actor(user_id=user.id, session_id=session.id)
    return run_and_respond(
        request, "cabinet.signing_code", actor, {"document_id": str(document_id)}, handler
    )


@require_POST
def document_decide(request: HttpRequest, document_id: UUID) -> JsonResponse:
    user, session = require_user(request)
    d = documents.get(user.id, document_id)
    data = parse_json(request)
    code = get_str(data, "code", max_length=10)
    decision = get_str(data, "decision", max_length=10)
    if decision not in ("sign", "decline"):
        raise errors.validation_error("decision")
    comment = get_str(data, "comment", max_length=2000, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        failed = documents.decide(ctx, d, code, decision, comment)
        if failed is not None:
            # The counted attempt is kept: the answer is the error, the transaction commits.
            return CommandResult(failed.status, failed.payload(ctx.request_id))
        return CommandResult(200, {"document": documents.payload(d, user.id, detail=True)})

    actor = Actor(user_id=user.id, session_id=session.id)
    payload = {"document_id": str(document_id), "decision": decision, "comment": comment}
    return run_and_respond(request, "cabinet.signing_decide", actor, payload, handler)


@require_GET
def garage(request: HttpRequest) -> JsonResponse:
    """The minimal personal garage: vehicles with a confirmed ownership (§ 7.1.2)."""
    user, _session = require_user(request)
    return JsonResponse({"vehicles": owner_requests.garage_payload(user.id)})
