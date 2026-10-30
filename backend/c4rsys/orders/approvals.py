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
* A position cancelled or removed by an employee before the answer takes its request with it
  («withdrawn»); a position moved to another order takes its request there (§ 12.3.7).
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.orders.models import Order, OrderApproval, OrderPolicy
from c4rsys.orders.services import OperationSummary, ensure_open, operations_of
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


def needs_approval(order_id: UUID | None) -> bool:
    """Whether a position added to the order now waits for the client. A module that creates a
    position already started (a direct issue, a move entered afterwards) asks this before the
    position exists, so that the position itself does not take the order into work."""
    if order_id is None:
        return False
    order = Order.objects.filter(pk=order_id).first()
    return not (
        order is None
        or order.is_internal
        or order.client_id is None
        or order.state != Order.State.OPEN
        or not policy_of(order.company_id)
        or not in_work(order)
    )


def on_added(
    ctx: CommandContext,
    order_id: UUID | None,
    item_type: str,
    item_id: UUID,
    title: str,
    *,
    required: bool | None = None,
) -> OrderApproval | None:
    """Called by a module when it adds a position to an order; `required` is the answer of
    `needs_approval` taken before the position was created."""
    if order_id is None:
        return None
    if required is None:
        required = needs_approval(order_id)
    order = Order.objects.filter(pk=order_id).first()
    if not required or order is None:
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


def _blocking(company_id: UUID, item_type: str, item_id: UUID) -> OrderApproval | None:
    """The request that keeps the position from starting, read locked so that an answer or
    «Do without the confirmation» given at the same time is seen."""
    approval = (
        OrderApproval.objects.select_for_update()
        .filter(company_id=company_id, item_type=item_type, item_id=item_id)
        .first()
    )
    if approval is None:
        return None
    if approval.state == State.DECLINED:
        return approval
    if approval.state == State.PENDING and approval.proceed_at is None:
        return approval
    return None


def ensure_startable(company_id: UUID, item_type: str, item_id: UUID) -> None:
    """A position waiting for the client starts only with «Do without the confirmation»."""
    approval = _blocking(company_id, item_type, item_id)
    if approval is None:
        return
    if approval.state == State.DECLINED:
        raise errors.business_rule("APPROVAL_DECLINED")
    raise errors.business_rule("APPROVAL_PENDING", approval=str(approval.id))


def startable(company_id: UUID, item_type: str, item_id: UUID) -> bool:
    """For an automatic transition: a waiting or refused position is left to the employee
    (§ 15.3.3 — with a doubt the automatic transition is not made)."""
    return _blocking(company_id, item_type, item_id) is None


# --- The position goes away or moves ------------------------------------------------------------


def withdraw(
    ctx: CommandContext, company_id: UUID, item_type: str, item_id: UUID, reason: str
) -> OrderApproval | None:
    """The position waiting for the answer is cancelled or removed by an employee: the request
    has nothing to agree any more and no longer keeps the order from closing. It stays in the
    history. An answered request is not changed."""
    approval = (
        OrderApproval.objects.select_for_update()
        .filter(company_id=company_id, item_type=item_type, item_id=item_id, state=State.PENDING)
        .first()
    )
    if approval is None:
        return None
    approval.state = State.WITHDRAWN
    approval.save(update_fields=["state"])
    ctx.audit(
        "order.approval_withdrawn",
        object_type="order",
        object_id=approval.order_id,
        after={"approval": str(approval.id), "item_type": item_type, "item_id": str(item_id)},
        reason=reason,
    )
    return approval


def move(
    ctx: CommandContext, company_id: UUID, item_type: str, item_id: UUID, order_id: UUID
) -> OrderApproval | None:
    """The position goes to another order with its request and answer (§ 12.3.7)."""
    approval = (
        OrderApproval.objects.select_for_update()
        .filter(company_id=company_id, item_type=item_type, item_id=item_id)
        .first()
    )
    if approval is None or approval.order_id == order_id:
        return None
    source = approval.order_id
    approval.order_id = order_id
    approval.save(update_fields=["order"])
    moved = {"approval": str(approval.id), "item_type": item_type, "item_id": str(item_id)}
    ctx.audit(
        "order.approval_moved_out",
        object_type="order",
        object_id=source,
        after={**moved, "to_order": str(order_id)},
    )
    ctx.audit(
        "order.approval_moved_in",
        object_type="order",
        object_id=order_id,
        after={**moved, "from_order": str(source)},
    )
    return approval


# --- Answers ------------------------------------------------------------------------------------


def _checked(approval: OrderApproval | None) -> OrderApproval:
    """A request is answered while it waits and its order is open (§ 12.2.3)."""
    if approval is None:
        raise errors.not_found()
    if approval.state == State.WITHDRAWN:
        raise errors.business_rule("APPROVAL_WITHDRAWN")
    if approval.state != State.PENDING:
        raise errors.business_rule("APPROVAL_ANSWERED")
    ensure_open(approval.company_id, approval.order_id)
    return approval


def _locked(company_id: UUID, order_id: UUID, approval_id: UUID) -> OrderApproval:
    return _checked(
        OrderApproval.objects.select_for_update()
        .filter(company_id=company_id, order_id=order_id, pk=approval_id)
        .first()
    )


def proceed(
    ctx: CommandContext, company_id: UUID, order_id: UUID, approval_id: UUID
) -> OrderApproval:
    """«Do without the client's confirmation» (§ 12.3.2): the company takes it on itself."""
    approval = _locked(company_id, order_id, approval_id)
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
    company_id: UUID,
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
    approval = _locked(company_id, order_id, approval_id)
    return _answer(
        ctx, approval, approved, source="employee", at=at, method=method, comment=comment
    )


def answer_by_client(
    ctx: CommandContext, client_ids: list[UUID], approval_id: UUID, approved: bool
) -> OrderApproval:
    """The client's own answer in the personal cabinet (§ 12.3.4)."""
    approval = _checked(
        OrderApproval.objects.select_for_update(of=("self",))
        .filter(pk=approval_id, order__client_id__in=client_ids)
        .first()
    )
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
