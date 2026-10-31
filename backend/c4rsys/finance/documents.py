"""Settlement records as a source of documents (ТЗ § 18.1.6; decision Р-3 of 29.10.2026).

A settlement document of a payment, a deposit, a set-off or a refund takes its data from one
record of finance: a money record (a receipt with its parts to orders and to the deposit, a refund,
a deposit paid back, another payout), a set-off of the client's funds, or a set-off of a deposit.
The documents module reads it only through these functions.

PROPOSAL (agent, not confirmed): the record is read by the employee who may see it — a money
record on an account visible to him (as in the list of payments and the search), a set-off by
any right of the settlements.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from c4rsys.companies import access
from c4rsys.finance.models import DepositEntry, MoneyMovement, Offset, PaymentAllocation
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor

SETTLEMENT_RIGHTS = (
    "finance.account.view",
    "finance.settlement.manage",
    "finance.offset",
    "finance.correct",
    "finance.deposit.manage",
)


@dataclass(frozen=True)
class Part:
    order_id: UUID
    purpose: str  # order_payment | deposit | offset
    amount: Decimal
    currency: str


@dataclass
class Record:
    # payment | manual | refund | deposit | supplier | opening | transfer | reversal | offset |
    # deposit_offset
    kind: str
    number: int | None
    at: datetime
    direction: str
    amount: Decimal
    currency: str
    method: str = ""
    account: str = ""
    client_id: UUID | None = None
    counterparty_id: UUID | None = None
    location_id: UUID | None = None
    comment: str = ""
    parts: list[Part] = field(default_factory=list)


def _movement(company_id: UUID, record_id: UUID) -> MoneyMovement | None:
    return (
        MoneyMovement.objects.select_related("cash_account")
        .filter(company_id=company_id, pk=record_id)
        .first()
    )


def can_read(actor: Actor, company_id: UUID, record_id: UUID) -> None:
    """Raises «not found» when the record is absent or hidden from the employee."""
    resolver = access.RightsResolver()
    movement = _movement(company_id, record_id)
    if movement is not None:
        from c4rsys.finance.views import ACCOUNT_RIGHTS

        if any(
            resolver.has_permission(actor, p, resource_id=movement.cash_account_id)
            for p in ACCOUNT_RIGHTS
        ):
            return
        raise errors.not_found()
    exists = (
        Offset.objects.filter(company_id=company_id, pk=record_id).exists()
        or DepositEntry.objects.filter(
            company_id=company_id, pk=record_id, kind=DepositEntry.Kind.OFFSET
        ).exists()
    )
    if exists and any(resolver.has_permission(actor, p) for p in SETTLEMENT_RIGHTS):
        return
    raise errors.not_found()


def record(company_id: UUID, record_id: UUID) -> Record:
    movement = _movement(company_id, record_id)
    if movement is not None:
        found = Record(
            kind=movement.kind,
            number=movement.number,
            at=movement.posted_at or movement.created_at,
            direction=movement.direction,
            amount=movement.amount,
            currency=movement.currency_id,
            method=movement.method,
            account=movement.cash_account.name,
            client_id=movement.client_id,
            counterparty_id=movement.counterparty_id,
            location_id=movement.location_id,
            comment=movement.note or movement.reason,
        )
        for a in PaymentAllocation.objects.filter(
            money_movement=movement, state=PaymentAllocation.State.ACTIVE
        ).order_by("created_at"):
            found.parts.append(Part(a.order_id, a.purpose, a.amount, a.currency_id))
        if movement.kind == MoneyMovement.Kind.DEPOSIT:
            for e in DepositEntry.objects.filter(
                money_movement=movement, kind=DepositEntry.Kind.REFUND
            ):
                if e.order_id:
                    found.parts.append(Part(e.order_id, "deposit", e.amount, e.currency_id))
        return found
    offset = Offset.objects.filter(company_id=company_id, pk=record_id).first()
    if offset is not None:
        found = Record(
            kind="offset",
            number=None,
            at=offset.created_at,
            direction="",
            amount=offset.amount,
            currency=offset.currency_id,
            client_id=offset.client_id,
            comment=offset.comment,
        )
        if offset.order_id:
            found.parts.append(
                Part(
                    offset.order_id,
                    "offset",
                    offset.settled_amount or offset.amount,
                    offset.settled_currency_id or offset.currency_id,
                )
            )
        return found
    entry = DepositEntry.objects.filter(
        company_id=company_id, pk=record_id, kind=DepositEntry.Kind.OFFSET
    ).first()
    if entry is None:
        raise errors.not_found()
    found = Record(
        kind="deposit_offset",
        number=None,
        at=entry.created_at,
        direction="",
        amount=entry.amount,
        currency=entry.currency_id,
        client_id=entry.client_id,
        comment=entry.reason,
    )
    if entry.order_id:
        found.parts.append(Part(entry.order_id, "offset", entry.amount, entry.currency_id))
    return found
