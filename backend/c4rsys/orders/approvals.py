"""Agreement of additions with the client (ТЗ § 12.3.1–12.3.5; decisions З9, З10, В-1).

* The company's policy «Agree additions with the client» is off by default. When it is on, a
  position added to a client order that is already in work waits for the client's answer.
  An order is «in work» from the start of its first operation (PROPOSAL: § 12.3.5 says
  «after the order is taken into work»).
* A change of the cost needs no agreement (decision В-1): the client sees it as a notice in
  the order of the cabinet.
* A waiting position does not start unless an employee chose «Do without the client's
  confirmation» (§ 12.3.2, З10); that is not the client's consent. No answer is not a refusal.
* The answer: the client in the personal cabinet, or an employee records it with the date, the
  way of contact and a comment (§ 12.3.4). A refusal cancels the position if it has not started;
  a started one stays with what was done, and the payment is settled apart (§ 12.3.1).
* A waiting position without the permission to proceed keeps the order from closing.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.orders.models import Order, OrderApproval, OrderPolicy
from c4rsys.orders.services import OperationSummary, operations_of
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

State = OrderApproval.State
# States of the operations of an order that are not started yet.
NOT_STARTED = {"proposed", "booked", "planned", "reserved", "cancelled", "annulled", "removed"}
# Ways the answer was received outside the system (PROPOSAL).
CONTACT_METHODS = ("phone", "messenger", "email", "in_person", "document", "other")

# item_type -> cancels the position if it has not started; returns whether it did.
Canceller = Callable[[CommandContext, UUID, UUID, str], bool]
_cancellers: dict[str, Canceller] = {}


def register_canceller(item_type: str, canceller: Canceller) -> None:
    _cancellers[item_type] = canceller


# --- The policy -------------------------------------------------------------------------------


def policy_of(company_id: UUID) -> bool:
    found = OrderPolicy.objects.filter(company_id=company_id).first()
    return found.require_approval if found else False


def set_policy(ctx: CommandContext, company_id: UUID, require_approval: bool) -> bool:
    found = OrderPolicy.objects.select_for_update().filter(company_id=company_id).first()
    before = found.require_approval if found else False
    if found is None:
        found = OrderPolicy(company_id=company_id, updated_by_user_id=ctx.actor.user_id)
    else:
        found.version += 1
    found.require_approval = require_approval
    found.updated_by_user_id = ctx.actor.user_id
    found.updated_at = timezone.now()
    found.save()
    ctx.audit(
        "order.policy_changed",
        object_type="order_policy",
        object_id=company_id,
        before={"require_approval": before},
        after={"require_approval": require_approval},
    )
    return require_approval


# --- A position is added -----------------------------------------------------------------------


def in_work(order: Order) -> bool:
    return any(op.type != "approval" and op.state not in NOT_STARTED for op in operations_of(order))


def on_added(
    ctx: CommandContext, order_id: UUID | None, item_type: str, item_id: UUID, title: str
) -> OrderApproval | None:
    """Called by a module when it adds a position to an order."""
    if order_id is None:
        return None
    order = Order.objects.filter(pk=order_id).first()
    if (
        order is None
        or order.is_internal
        or order.client_id is None
        or order.state != Order.State.OPEN
        or not policy_of(order.company_id)
        or not in_work(order)
    ):
        return None
    approval = OrderApproval.objects.create(
        company_id=order.company_id,
        order=order,
        item_type=item_type,
        item_id=item_id,
        title=title[:300],
        requested_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "order.approval_requested",
        object_type="order",
        object_id=order.id,
        after={"approval": str(approval.id), "item_type": item_type, "item_id": str(item_id)},
    )
    return approval


def ensure_startable(item_type: str, item_id: UUID) -> None:
    """A position waiting for the client starts only with «Do without the confirmation»."""
    approval = OrderApproval.objects.filter(item_type=item_type, item_id=item_id).first()
    if approval is None:
        return
    if approval.state == State.DECLINED:
        raise errors.business_rule("APPROVAL_DECLINED")
    if approval.state == State.PENDING and approval.proceed_at is None:
        raise errors.business_rule("APPROVAL_PENDING", approval=str(approval.id))


# --- Answers ------------------------------------------------------------------------------------


def _locked(order_id: UUID, approval_id: UUID) -> OrderApproval:
    approval = (
        OrderApproval.objects.select_for_update().filter(order_id=order_id, pk=approval_id).first()
    )
    if approval is None:
        raise errors.not_found()
    if approval.state != State.PENDING:
        raise errors.business_rule("APPROVAL_ANSWERED")
    return approval


def proceed(ctx: CommandContext, order_id: UUID, approval_id: UUID) -> OrderApproval:
    """«Do without the client's confirmation» (§ 12.3.2): the company takes it on itself."""
    approval = _locked(order_id, approval_id)
    if approval.proceed_at is not None:
        raise errors.business_rule("NOTHING_CHANGED")
    approval.proceed_at = timezone.now()
    approval.proceed_by_user_id = ctx.actor.user_id
    approval.save(update_fields=["proceed_at", "proceed_by_user_id"])
    ctx.audit(
        "order.approval_proceeded",
        object_type="order",
        object_id=order_id,
        after={"approval": str(approval.id)},
    )
    return approval


def _answer(
    ctx: CommandContext,
    approval: OrderApproval,
    approved: bool,
    *,
    source: str,
    at: datetime,
    method: str = "",
    comment: str = "",
    company_id: UUID | None = None,
) -> OrderApproval:
    approval.state = State.APPROVED if approved else State.DECLINED
    approval.answered_at = at
    approval.answer_source = source
    approval.answered_by_user_id = ctx.actor.user_id
    approval.contact_method = method
    approval.comment = comment
    approval.save()
    cancelled = False
    if not approved:
        canceller = _cancellers.get(approval.item_type)
        if canceller is not None:
            cancelled = canceller(ctx, approval.company_id, approval.item_id, "client_declined")
    ctx.audit(
        "order.approval_answered",
        object_type="order",
        object_id=approval.order_id,
        after={
            "approval": str(approval.id),
            "state": approval.state,
            "source": source,
            "method": method,
            "answered_at": at,
            "cancelled": cancelled,
        },
        reason=comment,
        company_id=company_id,
    )
    return approval


def answer_by_employee(
    ctx: CommandContext,
    order_id: UUID,
    approval_id: UUID,
    approved: bool,
    *,
    at: datetime,
    method: str,
    comment: str,
) -> OrderApproval:
    """The answer received outside the system (§ 12.3.4)."""
    if method not in CONTACT_METHODS:
        raise errors.validation_error("method")
    if at > timezone.now():
        raise errors.validation_error("answered_at", code="FUTURE_TIME_NOT_ALLOWED")
    approval = _locked(order_id, approval_id)
    return _answer(
        ctx, approval, approved, source="employee", at=at, method=method, comment=comment
    )


def answer_by_client(
    ctx: CommandContext, client_ids: list[UUID], approval_id: UUID, approved: bool
) -> OrderApproval:
    """The client's own answer in the personal cabinet (§ 12.3.4)."""
    approval = (
        OrderApproval.objects.select_for_update()
        .select_related("order")
        .filter(pk=approval_id, order__client_id__in=client_ids)
        .first()
    )
    if approval is None:
        raise errors.not_found()
    if approval.state != State.PENDING:
        raise errors.business_rule("APPROVAL_ANSWERED")
    return _answer(
        ctx,
        approval,
        approved,
        source="client",
        at=timezone.now(),
        company_id=approval.company_id,
    )


# --- For the order ------------------------------------------------------------------------------


def of_order(order_id: UUID) -> QuerySet[OrderApproval]:
    return OrderApproval.objects.filter(order_id=order_id).order_by("requested_at")


def operations_for_order(company_id: UUID, order_id: UUID) -> list[OperationSummary]:
    """A waiting position without the permission to proceed keeps the order from closing."""
    return [
        OperationSummary(
            type="approval",
            id=a.id,
            state=a.state,
            location_id=None,
            # Not an operation to finish: only the condition «awaits the client» is shown.
            finished=True,
            blockers=("awaiting_client",),
        )
        for a in OrderApproval.objects.filter(
            company_id=company_id, order_id=order_id, state=State.PENDING, proceed_at__isnull=True
        )
    ]


def payload(a: OrderApproval) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "item_type": a.item_type,
        "item_id": str(a.item_id),
        "title": a.title,
        "state": a.state,
        "requested_at": a.requested_at.isoformat(),
        "proceed_at": a.proceed_at.isoformat() if a.proceed_at else None,
        "answered_at": a.answered_at.isoformat() if a.answered_at else None,
        "answer_source": a.answer_source,
        "contact_method": a.contact_method,
        "comment": a.comment,
    }


def client_payload(a: OrderApproval) -> dict[str, Any]:
    """For the client: what waits for the answer and what was answered; no employees."""
    return {
        "id": str(a.id),
        "item_type": a.item_type,
        "title": a.title,
        "state": a.state,
        "requested_at": a.requested_at.isoformat(),
        "answered_at": a.answered_at.isoformat() if a.answered_at else None,
        "answer_source": a.answer_source,
    }
