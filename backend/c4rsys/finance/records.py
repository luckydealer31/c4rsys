"""Initial balances of accounts and receipts and payouts entered by hand (ТЗ § 17.2.1–17.2.2,
§ 17.2.7, § 17.2.9, § 22.1.7).

Every record is posted at once in the currency of its account; exchange comes with § 17.1. A
posted record is not changed: corrections are separate records (§ 17.2.11).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Case, DecimalField, F, QuerySet, Sum, When
from django.utils import timezone

from c4rsys.companies.models import ActivityContext, Location
from c4rsys.finance import articles, rates
from c4rsys.finance.models import CashAccount, FinanceArticle, MoneyMovement
from c4rsys.fleet.models import VehicleCard
from c4rsys.parties import services as parties
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.counters import next_number
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money

IN = MoneyMovement.Direction.IN
OUT = MoneyMovement.Direction.OUT


def _active_account(company_id: UUID, account_id: UUID) -> CashAccount:
    account = (
        CashAccount.objects.select_for_update().filter(company_id=company_id, pk=account_id).first()
    )
    if account is None:
        raise errors.not_found()
    if account.state != CashAccount.State.ACTIVE:
        raise errors.business_rule("CASH_ACCOUNT_ARCHIVED", field="cash_account_id")
    return account


def _same_currency(account: CashAccount, currency: str) -> None:
    # TODO(spec): operations in another currency come with the exchange rates (§ 17.1.3).
    if account.currency_id != currency:
        raise errors.business_rule(
            "CURRENCY_MISMATCH", field="currency", account_currency=account.currency_id
        )


def _record_snapshot(movement: MoneyMovement) -> dict[str, Any]:
    return {
        "number": movement.number,
        "kind": movement.kind,
        "cash_account": str(movement.cash_account_id),
        "direction": movement.direction,
        "amount": movement.amount,
        "currency": movement.currency_id,
        "article": str(movement.article_id) if movement.article_id else None,
        "client": str(movement.client_id) if movement.client_id else None,
        "location": str(movement.location_id) if movement.location_id else None,
        "context": str(movement.context_id) if movement.context_id else None,
        "vehicle": str(movement.vehicle_id) if movement.vehicle_id else None,
        "note": movement.note,
    }


# --- Initial balance (§ 22.1.7) -----------------------------------------------------------------


def set_opening(
    ctx: CommandContext, company_id: UUID, account_id: UUID, amount: Decimal, currency: str
) -> MoneyMovement:
    """A separate initial record, not a receipt of money; one per account (PROPOSAL: it may be
    entered after the account is created). TODO(spec): a negative initial balance is not set."""
    account = _active_account(company_id, account_id)
    _same_currency(account, currency)
    if MoneyMovement.objects.filter(cash_account=account, kind=MoneyMovement.Kind.OPENING).exists():
        raise errors.business_rule("OPENING_BALANCE_EXISTS", field="amount")
    now = timezone.now()
    movement = MoneyMovement.objects.create(
        company_id=company_id,
        number=next_number(company_id, "payment"),
        cash_account=account,
        kind=MoneyMovement.Kind.OPENING,
        direction=IN,
        amount=amount,
        currency_id=currency,
        state=MoneyMovement.State.POSTED,
        method="",
        posted_at=now,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )
    ctx.audit(
        "cash_account.opening_balance_set",
        object_type="money_movement",
        object_id=movement.id,
        after=_record_snapshot(movement),
    )
    return movement


# --- Manual receipts and payouts (§ 17.2.2, § 17.2.7) -------------------------------------------


@dataclass(frozen=True)
class ManualRecord:
    cash_account_id: UUID
    direction: str
    amount: Decimal
    currency: str
    article_id: UUID
    client_id: UUID | None = None
    location_id: UUID | None = None
    context_id: UUID | None = None
    vehicle_id: UUID | None = None
    note: str = ""


def _dimensions(company_id: UUID, data: ManualRecord) -> None:
    """Analytic links of the result (§ 17.2.4, § 17.3.1) belong to the company; a context
    belongs to the chosen location."""
    if data.location_id is not None:
        if not Location.objects.filter(company_id=company_id, pk=data.location_id).exists():
            raise errors.validation_error("location_id", code="LOCATION_INVALID")
    if data.context_id is not None:
        context = ActivityContext.objects.filter(company_id=company_id, pk=data.context_id).first()
        if context is None:
            raise errors.validation_error("context_id", code="CONTEXT_INVALID")
        if data.location_id is not None and context.location_id != data.location_id:
            raise errors.validation_error("context_id", code="CONTEXT_NOT_IN_LOCATION")
    if data.vehicle_id is not None:
        if not VehicleCard.objects.filter(company_id=company_id, pk=data.vehicle_id).exists():
            raise errors.validation_error("vehicle_id", code="VEHICLE_INVALID")


def record_manual(ctx: CommandContext, company_id: UUID, data: ManualRecord) -> MoneyMovement:
    """A receipt or payout without an order, classified by an article (§ 17.2.8). The money
    stays an independent record (§ 17.2.11).

    TODO(spec): whether a payout may exceed the balance of the account is not set; it is not
    blocked. A counterparty or vehicle owner as the party comes with their cards (§ 17.2.3)."""
    account = _active_account(company_id, data.cash_account_id)
    _same_currency(account, data.currency)
    kind = FinanceArticle.Kind.INCOME if data.direction == IN else FinanceArticle.Kind.EXPENSE
    article = articles.usable(company_id, data.article_id, kind)
    client = parties.get_client(company_id, data.client_id) if data.client_id else None
    _dimensions(company_id, data)
    location_id = data.location_id
    if data.context_id is not None and location_id is None:
        location_id = ActivityContext.objects.get(pk=data.context_id).location_id

    now = timezone.now()
    movement = MoneyMovement.objects.create(
        company_id=company_id,
        number=next_number(company_id, "payment"),
        cash_account=account,
        kind=MoneyMovement.Kind.MANUAL,
        direction=data.direction,
        amount=data.amount,
        currency_id=data.currency,
        client=client,
        article=article,
        location_id=location_id,
        context_id=data.context_id,
        vehicle_id=data.vehicle_id,
        state=MoneyMovement.State.POSTED,
        # The way of an operation is not recorded: cash or non-cash is the type of the account
        # (decision 26.10.2026).
        method="",
        note=data.note,
        posted_at=now,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
    )
    ctx.audit(
        "money.received" if data.direction == IN else "money.paid_out",
        object_type="money_movement",
        object_id=movement.id,
        after=_record_snapshot(movement),
    )
    return movement


# --- Balances (§ 17.2.1) ------------------------------------------------------------------------


@dataclass(frozen=True)
class Total:
    """Balances of accounts valued in one currency by the current rates (§ 17.1.2, § 17.1.5).
    Accounts without a rate are listed apart and are not in the amount."""

    amount: Decimal
    currency: str
    missing: tuple[str, ...]
    fetch: Any  # RateFetch | None — the newest download used
    stale: bool


def total(balances_by_account: list[tuple[str, Decimal]], currency: str) -> Total:
    amount = Decimal(0)
    missing: set[str] = set()
    newest = None
    stale = False
    for account_currency, balance in balances_by_account:
        if account_currency == currency:
            amount += balance
            continue
        suggestion = rates.suggest(account_currency, currency)
        if suggestion is None:
            missing.add(account_currency)
            continue
        amount += balance * suggestion.rate
        stale = stale or suggestion.stale
        if newest is None or suggestion.fetch.fetched_at > newest.fetched_at:
            newest = suggestion.fetch
    places = Currency.objects.get(code=currency).minor_units
    return Total(
        amount=round_money(amount, places),
        currency=currency,
        missing=tuple(sorted(missing)),
        fetch=newest,
        stale=stale,
    )


def balance_of(company_id: UUID, account_id: UUID) -> Decimal:
    return balances(company_id).get(account_id) or Decimal(0)


def movements_of(company_id: UUID, account_id: UUID) -> QuerySet[MoneyMovement]:
    return MoneyMovement.objects.filter(
        company_id=company_id, cash_account_id=account_id, state=MoneyMovement.State.POSTED
    ).order_by("-posted_at", "-number")


def balances(company_id: UUID) -> dict[UUID, Decimal]:
    """Balance of each account in its own currency: posted money in minus money out."""
    signed = Case(
        When(direction=IN, then=F("amount")),
        default=-F("amount"),
        output_field=DecimalField(max_digits=20, decimal_places=4),
    )
    rows = (
        MoneyMovement.objects.filter(company_id=company_id, state=MoneyMovement.State.POSTED)
        .values("cash_account_id")
        .annotate(total=Sum(signed))
    )
    return {r["cash_account_id"]: r["total"] for r in rows}
