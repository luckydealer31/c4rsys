"""The starter dashboard «Company overview» (ТЗ § 21.3.1; customer's decisions О-1, О-5).

Each block is counted by the right of its section over the area the employee may see (§ 21.1.1)
and links to the list it is counted from. Blocks whose functions are not in the system yet —
unconfirmed corrections and requests, conflicts of identifiers — are not shown (О-5): they are
not zeros.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone

from c4rsys.companies import access
from c4rsys.companies.models import Company
from c4rsys.platform.commands import Actor
from c4rsys.reports.scope import Area, Filters, area


@dataclass
class Block:
    """`value` None — «no data»; a counted zero is 0 (§ 21.2.6)."""

    code: str
    section: str
    unit: str  # count | money | accounts
    value: Any = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    area: Area | None = None
    link: str | None = None
    # Money is counted by the accounts the employee may view; partial access names them.
    accounts: list[str] | None = None


def _money(rows: dict[str, Decimal]) -> list[dict[str, Any]]:
    return [
        {"currency": c, "amount": str(v.quantize(Decimal("0.01")))} for c, v in sorted(rows.items())
    ]


# --- Rentals and fleet -------------------------------------------------------------------------


def _rentals(company: Company, where: Area, filters: Filters, now: datetime) -> list[Block]:
    from c4rsys.rentals.models import Rental

    rentals = Rental.objects.filter(company=company)
    if not where.whole:
        rentals = rentals.filter(issue_location_id__in=where.location_ids)
    if filters.vehicle_id:
        rentals = rentals.filter(vehicle_card_id=filters.vehicle_id)
    day_start, day_end = filters.day_bounds(filters.today())
    booked = rentals.filter(state=Rental.State.BOOKED)
    active = rentals.filter(state=Rental.State.ACTIVE)
    return [
        Block(
            "rental.issues_today",
            "rental",
            "count",
            booked.filter(planned_start__gte=day_start, planned_start__lt=day_end).count(),
            area=where,
            link="/rentals",
        ),
        Block(
            "rental.returns_today",
            "rental",
            "count",
            active.filter(planned_end__gte=day_start, planned_end__lt=day_end).count(),
            area=where,
            link="/rentals",
        ),
        Block(
            "rental.active",
            "rental",
            "count",
            active.count(),
            area=where,
            link="/rentals",
        ),
        Block(
            "rental.overdue_returns",
            "rental",
            "count",
            active.filter(planned_end__lt=now).count(),
            area=where,
            link="/rentals",
        ),
    ]


def _fleet(company: Company, where: Area, filters: Filters) -> list[Block]:
    from c4rsys.fleet.models import VehicleCard
    from c4rsys.rentals.models import Rental
    from c4rsys.service.models import ServiceOperation

    cards = VehicleCard.objects.filter(company=company, state=VehicleCard.State.ACTIVE)
    if not where.whole:
        cards = cards.filter(accounting_location_id__in=where.location_ids)
    if filters.vehicle_id:
        cards = cards.filter(pk=filters.vehicle_id)
    ids = set(cards.values_list("id", flat=True))
    in_rental = set(
        Rental.objects.filter(vehicle_card_id__in=ids, state=Rental.State.ACTIVE).values_list(
            "vehicle_card_id", flat=True
        )
    )
    in_service = set(
        ServiceOperation.objects.filter(
            vehicle_card_id__in=ids, state__in=("accepted", "in_progress")
        ).values_list("vehicle_card_id", flat=True)
    )
    return [
        Block("fleet.total", "fleet", "count", len(ids), area=where, link="/vehicles"),
        Block(
            "fleet.available",
            "fleet",
            "count",
            len(ids - in_rental - in_service),
            area=where,
            link="/vehicles",
        ),
        Block(
            "fleet.in_rental",
            "fleet",
            "count",
            len(in_rental),
            area=where,
            link="/rentals",
        ),
        Block("fleet.in_service", "fleet", "count", len(in_service), area=where, link="/service"),
    ]


def _maintenance(company: Company, where: Area, filters: Filters) -> list[Block]:
    from c4rsys.service import maintenance

    overdue = [
        card
        for card, _rule, status in maintenance.due(company.id)
        if status.state == "required"
        and where.includes(card.accounting_location_id)
        and (filters.vehicle_id is None or card.id == filters.vehicle_id)
    ]
    return [
        Block(
            "service.maintenance_overdue",
            "service",
            "count",
            len({c.id for c in overdue}),
            area=where,
            link="/service",
        )
    ]


# --- Money -------------------------------------------------------------------------------------


def _accounts(actor: Actor, company: Company) -> list[Any] | None:
    """Accounts the employee may view (§ 21.1.1; the scope of finance rights is by account)."""
    from c4rsys.finance.models import CashAccount

    resolver = access.RightsResolver()
    found = [
        a
        for a in CashAccount.objects.filter(company=company).order_by("name")
        if resolver.has_permission(actor, "finance.account.view", resource_id=a.id)
    ]
    return found or None


def _money_blocks(actor: Actor, company: Company, filters: Filters) -> list[Block]:
    from c4rsys.finance import records
    from c4rsys.finance.models import ChargeEntry, MoneyMovement

    accounts = _accounts(actor, company)
    if accounts is None:
        return []
    whole = len(accounts) == len(list(_all_accounts(company)))
    where = Area(whole, frozenset())
    ids = [a.id for a in accounts]
    movements = MoneyMovement.objects.filter(
        company=company,
        cash_account_id__in=ids,
        state=MoneyMovement.State.POSTED,
        posted_at__gte=filters.start_at,
        posted_at__lt=filters.end_at,
    ).exclude(kind__in=("opening", "transfer"))
    if filters.location_id:
        movements = movements.filter(location_id=filters.location_id)
    if filters.vehicle_id:
        movements = movements.filter(vehicle_id=filters.vehicle_id)
    receipts: dict[str, Decimal] = defaultdict(Decimal)
    expenses: dict[str, Decimal] = defaultdict(Decimal)
    for m in movements.select_related("reverses"):
        # A reversal cancels its record in the period of the reversal (§ 21.1.4).
        incoming = m.direction == "in"
        if m.kind == "reversal" and m.reverses is not None:
            target = receipts if m.reverses.direction == "in" else expenses
            target[m.currency_id] -= m.amount
            continue
        (receipts if incoming else expenses)[m.currency_id] += m.amount
    balances = records.balances(company.id)
    rows = []
    for a in accounts:
        rows.append(
            {
                "name": a.name,
                "currency": a.currency_id,
                "balance": str(balances.get(a.id, Decimal(0)).quantize(Decimal("0.01"))),
            }
        )
    main = company.main_currency_id
    summary = None
    if main:
        found = records.total(
            [(a.currency_id, balances.get(a.id, Decimal(0))) for a in accounts], main
        )
        summary = {"amount": str(found.amount), "currency": main, "missing": list(found.missing)}
    blocks = [
        Block("finance.receipts", "finance", "money", None, _money(receipts), where, "/finance"),
        Block("finance.expenses", "finance", "money", None, _money(expenses), where, "/finance"),
        Block(
            "finance.balances",
            "finance",
            "accounts",
            summary,
            rows,
            where,
            "/finance",
        ),
    ]
    # Unpaid debt of clients, by currency (§ 6.7.2); only with access to all accounts, since
    # payments to hidden accounts would distort it.
    if whole:
        from c4rsys.finance import payments

        debt: dict[str, Decimal] = defaultdict(Decimal)
        clients = set(
            ChargeEntry.objects.filter(company=company).values_list("client_id", flat=True)
        )
        for client_id in clients:
            for currency, amount in payments.client_debt(company.id, client_id).items():
                debt[currency] += amount
        blocks.append(
            Block("finance.debt", "finance", "money", None, _money(debt), where, "/clients")
        )
    for b in blocks:
        if not whole:
            b.accounts = [a.name for a in accounts]
        if b.unit == "money" and not b.rows:
            b.value = 0
    return blocks


def _all_accounts(company: Company) -> Any:
    from c4rsys.finance.models import CashAccount

    return CashAccount.objects.filter(company=company)


# --- Documents ---------------------------------------------------------------------------------


def _documents(actor: Actor, company: Company, where: Area) -> list[Block]:
    """Documents sent for signing and not signed yet (pending or declined)."""
    from c4rsys.documents import services as documents
    from c4rsys.documents import signing

    resolver = access.RightsResolver()
    count = 0
    for d in (
        documents.documents_of(company.id).filter(state="active", signers__isnull=False).distinct()
    ):
        if not documents.can(actor, "documents.view", d, resolver):
            continue
        if signing.status(d).state in ("pending", "declined"):
            count += 1
    return [
        Block(
            "documents.unsigned",
            "documents",
            "count",
            count,
            area=where,
            link="/documents",
        )
    ]


def _low_stock(company: Company, where: Area) -> list[Block]:
    """Items below the minimum stock of a warehouse (§ 11.7.8)."""
    from c4rsys.companies.models import ActivityContext
    from c4rsys.warehouse import prices

    contexts = ActivityContext.objects.filter(
        location__company=company, type=ActivityContext.Type.WAREHOUSE
    )
    if not where.whole:
        contexts = contexts.filter(location_id__in=where.location_ids)
    low = prices.low_stock(company.id, set(contexts.values_list("id", flat=True)))
    return [
        Block(
            "warehouse.below_minimum",
            "warehouse",
            "count",
            len(low),
            area=where,
            link="/dashboards",
        )
    ]


def build(actor: Actor, company: Company, filters: Filters) -> list[Block]:
    now = timezone.now()
    blocks: list[Block] = []
    rental = area(actor, "rental.view", company, filters)
    if rental is not None:
        blocks += _rentals(company, rental, filters, now)
    fleet = area(actor, "vehicle.view", company, filters)
    if fleet is not None:
        blocks += _fleet(company, fleet, filters)
    blocks += _money_blocks(actor, company, filters)
    service = area(actor, "service.view", company, filters)
    if service is not None:
        blocks += _maintenance(company, service, filters)
    stock = area(actor, "warehouse.view", company, filters)
    if stock is not None:
        blocks += _low_stock(company, stock)
    docs = area(actor, "documents.view", company, filters)
    if docs is not None:
        blocks += _documents(actor, company, docs)
    return blocks
