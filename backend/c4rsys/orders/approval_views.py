"""HTTP API of the agreement of additions (ТЗ § 12.3; decisions З9, З10, В-1)."""

from datetime import datetime
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.orders import approvals, services
from c4rsys.orders.views import _order_for
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_str, parse_json


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


@require_http_methods(["GET", "PUT"])
def policy(request: HttpRequest, cmp: str) -> JsonResponse:
    """«Agree additions with the client»: seen by all, changed in the company settings."""
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        return JsonResponse({"require_approval": approvals.policy_of(context.company.id)})
    if not access.RightsResolver().has_permission(actor, "company.settings", whole_company=True):
        raise errors.permission_denied("company.settings")
    value = parse_json(request).get("require_approval")
    if not isinstance(value, bool):
        raise errors.validation_error("require_approval")

    def handler(ctx: CommandContext) -> CommandResult:
        approvals.set_policy(ctx, context.company.id, value)
        return CommandResult(200, {"require_approval": value})

    return run_and_respond(request, "order.policy", actor, {"require_approval": value}, handler)


def _list(order_id: UUID) -> list[dict[str, object]]:
    return [approvals.payload(a) for a in approvals.of_order(order_id)]


@require_http_methods(["GET"])
def order_approvals(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    order = services.get_order(context.company.id, order_id)
    if not services.can_view(actor, order):
        raise errors.not_found()
    return JsonResponse({"approvals": _list(order.id)})


@require_POST
def approval_proceed(
    request: HttpRequest, cmp: str, order_id: UUID, approval_id: UUID
) -> JsonResponse:
    """«Do without the client's confirmation» (§ 12.3.2): the right to change the order."""
    context, actor = _actor(request, cmp)
    order = _order_for(actor, context.company.id, order_id, "order.edit")

    def handler(ctx: CommandContext) -> CommandResult:
        approvals.proceed(ctx, order.id, approval_id)
        return CommandResult(200, {"approvals": _list(order.id)})

    payload = {"order_id": str(order_id), "approval_id": str(approval_id)}
    return run_and_respond(request, "order.approval_proceed", actor, payload, handler)


@require_POST
def approval_answer(
    request: HttpRequest, cmp: str, order_id: UUID, approval_id: UUID
) -> JsonResponse:
    """The client's answer received outside the system (§ 12.3.4)."""
    context, actor = _actor(request, cmp)
    order = _order_for(actor, context.company.id, order_id, "order.edit")
    data = parse_json(request)
    decision = get_str(data, "decision", max_length=10)
    if decision not in ("approved", "declined"):
        raise errors.validation_error("decision")
    method = get_str(data, "method", max_length=20)
    comment = get_str(data, "comment", max_length=2000, required=False)
    raw = get_str(data, "answered_at", max_length=40)
    try:
        at = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise errors.validation_error("answered_at", code="DATETIME_INVALID") from exc
    if at.tzinfo is None:
        raise errors.validation_error("answered_at", code="DATETIME_WITHOUT_OFFSET")

    def handler(ctx: CommandContext) -> CommandResult:
        approvals.answer_by_employee(
            ctx,
            order.id,
            approval_id,
            decision == "approved",
            at=at,
            method=method,
            comment=comment,
        )
        return CommandResult(200, {"approvals": _list(order.id)})

    payload = {
        "order_id": str(order_id),
        "approval_id": str(approval_id),
        "decision": decision,
        "method": method,
        "comment": comment,
        "answered_at": raw,
    }
    return run_and_respond(request, "order.approval_answer", actor, payload, handler)
