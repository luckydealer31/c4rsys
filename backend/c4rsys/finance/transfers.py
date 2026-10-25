"""Transfers between own accounts (ТЗ § 17.1.6, § 23.2.7).

A transfer creates a linked payout in the currency of the source account and a receipt in the
currency of the target account, keeping both amounts and the applied rate. It is neither income
nor expense; fees are separate expense records. A posted transfer is not edited: a mistake is
corrected by a reverse transfer.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.finance import rates
from c4rsys.finance.models import CashAccount, MoneyMovement, Transfer
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number


@dataclass(frozen=True)
class NewTransfer:
    from_account_id: UUID
    to_account_id: UUID
    amount: Decimal  # in the currency of the source account
    rate: Decimal | None = None  # manual rate; None — the suggested one
    note: str = ""


def _locked_accounts(company_id: UUID, ids: list[UUID]) -> dict[UUID, CashAccount]:
    """Both accounts locked in a constant order, so opposite transfers do not deadlock."""
    accounts = {
        a.id: a
        for a in CashAccount.objects.select_for_update()
        .filter(company_id=company_id, pk__in=ids)
        .order_by("id")
    }
    for account_id in ids:
        account = accounts.get(account_id)
        if account is None:
            raise errors.not_found()
        if account.state != CashAccount.State.ACTIVE:
            raise errors.business_rule("CASH_ACCOUNT_ARCHIVED", account_id=str(account_id))
    return accounts


def create(
    ctx: CommandContext, company_id: UUID, data: NewTransfer, *, may_override_rate: bool
) -> Transfer:
    if data.from_account_id == data.to_account_id:
        raise errors.validation_error("to_account_id", code="TRANSFER_SAME_ACCOUNT")
    accounts = _locked_accounts(company_id, [data.from_account_id, data.to_account_id])
    source = accounts[data.from_account_id]
    target = accounts[data.to_account_id]
    if source.currency_id == target.currency_id:
        if data.rate is not None and data.rate != 1:
            raise errors.validation_error("rate", code="RATE_SAME_CURRENCY")
        applied = rates.AppliedRate(rate=Decimal(1), suggested=None, fetch=None, manual=False)
        amount_in = data.amount
    else:
        applied = rates.apply(
            source.currency_id, target.currency_id, data.rate, may_override=may_override_rate
        )
        amount_in = rates.convert(data.amount, applied.rate, target.currency_id)
        if amount_in <= 0:
            raise errors.validation_error("amount", code="AMOUNT_TOO_SMALL")

    now = timezone.now()

    def movement(account: CashAccount, direction: str, amount: Decimal) -> MoneyMovement:
        return MoneyMovement.objects.create(
            company_id=company_id,
            number=next_number(company_id, "payment"),
            cash_account=account,
            kind=MoneyMovement.Kind.TRANSFER,
            direction=direction,
            amount=amount,
            currency_id=account.currency_id,
            state=MoneyMovement.State.POSTED,
            method="",
            note=data.note,
            posted_at=now,
            created_by_user_id=ctx.actor.user_id,
            created_at=now,
        )

    transfer = Transfer.objects.create(
        company_id=company_id,
        out_movement=movement(source, MoneyMovement.Direction.OUT, data.amount),
        in_movement=movement(target, MoneyMovement.Direction.IN, amount_in),
        rate_fetch=applied.fetch,
        suggested_rate=applied.suggested,
        applied_rate=applied.rate,
        rate_manual=applied.manual,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )
    ctx.audit(
        "money.transferred",
        object_type="money_transfer",
        object_id=transfer.id,
        after=snapshot(transfer, applied),
    )
    return transfer


def snapshot(transfer: Transfer, applied: rates.AppliedRate) -> dict[str, Any]:
    out, into = transfer.out_movement, transfer.in_movement
    return {
        "from_account": str(out.cash_account_id),
        "to_account": str(into.cash_account_id),
        "amount_out": out.amount,
        "currency_out": out.currency_id,
        "amount_in": into.amount,
        "currency_in": into.currency_id,
        "note": out.note,
        **rates.snapshot(applied),
    }
