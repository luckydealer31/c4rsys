"""Cash desks and bank accounts (ТЗ § 17.2.5, § 22.1.7)."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.companies.models import Company, Location
from c4rsys.finance.models import CashAccount
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import Currency


@dataclass(frozen=True)
class AccountPlace:
    """Where a cash account belongs, for permission scopes (§ 23.1.11–23.1.12)."""

    id: UUID
    location_id: UUID | None


def account_place(company_id: UUID, account_id: UUID) -> AccountPlace | None:
    rows = list(
        CashAccount.objects.filter(company_id=company_id, pk=account_id).values_list(
            "location_id", flat=True
        )
    )
    return AccountPlace(id=account_id, location_id=rows[0]) if rows else None


def accounts_of(company_id: UUID) -> QuerySet[CashAccount]:
    return CashAccount.objects.filter(company_id=company_id).order_by("created_at")


def get_account(company_id: UUID, account_id: UUID) -> CashAccount:
    account = accounts_of(company_id).filter(pk=account_id).first()
    if account is None:
        raise errors.not_found()
    return account


def snapshot(account: CashAccount) -> dict[str, Any]:
    return {
        "name": account.name,
        "type": account.type,
        "currency": account.currency_id,
        "location": str(account.location_id) if account.location_id else None,
        "state": account.state,
    }


@dataclass(frozen=True)
class NewAccount:
    name: str
    type: str
    currency: str  # empty: the company's main currency is proposed (§ 22.1.7)
    location_id: UUID | None = None


def create_account(ctx: CommandContext, company: Company, data: NewAccount) -> CashAccount:
    """Name, type and currency are required; the scope is the whole company or one location
    (§ 22.1.7). The initial balance comes with money records (T15)."""
    code = data.currency or (company.main_currency_id or "")
    if not code:
        raise errors.validation_error("currency", code="FIELD_REQUIRED")
    currency = Currency.objects.filter(code=code.upper()).first()
    if currency is None:
        raise errors.validation_error("currency", code="CURRENCY_INVALID")
    location = None
    if data.location_id is not None:
        location = Location.objects.filter(company=company, pk=data.location_id).first()
        if location is None:
            raise errors.validation_error("location_id", code="LOCATION_UNKNOWN")
    account = CashAccount.objects.create(
        company=company,
        name=data.name,
        type=data.type,
        currency=currency,
        location=location,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "cash_account.created",
        object_type="cash_account",
        object_id=account.id,
        after=snapshot(account),
    )
    return account


def _locked(company_id: UUID, account_id: UUID, expected_version: int) -> CashAccount:
    account = (
        CashAccount.objects.select_for_update().filter(company_id=company_id, pk=account_id).first()
    )
    if account is None:
        raise errors.not_found()
    if account.version != expected_version:
        raise errors.version_conflict()
    return account


def rename_account(
    ctx: CommandContext, company_id: UUID, account_id: UUID, name: str, expected_version: int
) -> CashAccount:
    """Only the name changes; currency, type and scope stay (decision 22.10.2026)."""
    account = _locked(company_id, account_id, expected_version)
    before = account.name
    account.name = name
    account.version += 1
    account.save(update_fields=["name", "version", "updated_at"])
    ctx.audit(
        "cash_account.renamed",
        object_type="cash_account",
        object_id=account.id,
        before={"name": before},
        after={"name": name},
    )
    return account


def archive_account(
    ctx: CommandContext, company_id: UUID, account_id: UUID, expected_version: int
) -> CashAccount:
    """An archived account is not offered for new operations; its history stays.

    TODO(spec): conditions of archiving (for example a non-zero balance) are not defined; there
    are no money records before T15.
    """
    account = _locked(company_id, account_id, expected_version)
    if account.state == CashAccount.State.ARCHIVED:
        raise errors.business_rule("CASH_ACCOUNT_ARCHIVED")
    account.state = CashAccount.State.ARCHIVED
    account.version += 1
    account.save(update_fields=["state", "version", "updated_at"])
    ctx.audit(
        "cash_account.archived",
        object_type="cash_account",
        object_id=account.id,
        before={"state": CashAccount.State.ACTIVE},
        after={"state": account.state},
    )
    return account
