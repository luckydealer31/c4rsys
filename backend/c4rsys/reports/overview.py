"""The starter dashboard «Company overview» (ТЗ § 21.3.1; customer's decisions О-1, О-5).

Each block is counted by the right of its section over the area the employee may see (§ 21.1.1)
and links to the list it is counted from, filtered by the block where the list has such a filter.
Decision О-5 kept the blocks of functions not yet in the system hidden; block 16 Б adds them now
that the functions exist: vehicles unavailable because of a restriction or a conflict,
unconfirmed adjustments of inventories, requests to the company awaiting its answer and
unresolved conflicts of identifiers.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

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
    # The warehouses of the calculation with partial access (§ 21.1.1).
    warehouses: list[str] | None = None


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
    unavailable = unavailable_cards(ids, timezone.now())
    conflicts = conflict_cards(ids)
    return [
        Block("fleet.total", "fleet", "count", len(ids), area=where, link="/vehicles"),
        Block(
            "fleet.available",
            "fleet",
            "count",
            # PROPOSAL: a vehicle unavailable by a restriction or a dispute is not available.
            len(ids - in_rental - in_service - unavailable),
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
        Block(
            "fleet.unavailable",
            "fleet",
            "count",
            len(unavailable),
            area=where,
            link="/vehicles?only=unavailable",
        ),
        Block(
            "fleet.identifier_conflicts",
            "fleet",
            "count",
            len(conflicts),
            area=where,
            link="/vehicles?only=identifier_conflict",
        ),
    ]


def unavailable_cards(card_ids: set[UUID], now: datetime) -> set[UUID]:
    """§ 21.3.1: cards with a restriction in force now (§ 8.8) or in an open dispute of ownership
    or management (В-12.3). PROPOSAL: a conflict of identifiers alone is a warning and does not
    make the vehicle unavailable (§ 8.6.3); it has its own block."""
    from c4rsys.fleet.models import VehicleOperationalDisputeParty, VehicleRestriction

    restricted = set(
        VehicleRestriction.objects.filter(
            vehicle_card_id__in=card_ids, period__contains=now
        ).values_list("vehicle_card_id", flat=True)
    )
    disputed = set(
        VehicleOperationalDisputeParty.objects.filter(
            vehicle_card_id__in=card_ids, dispute__is_open=True
        ).values_list("vehicle_card_id", flat=True)
    )
    return restricted | disputed


def conflict_cards(card_ids: set[UUID]) -> set[UUID]:
    """Cards that take part in an open conflict of identifiers (§ 8.6.1)."""
    from c4rsys.fleet.models import IdentifierConflictParty

    return {
        x
        for x in IdentifierConflictParty.objects.filter(
            vehicle_card_id__in=card_ids, left_at__isnull=True, conflict__is_open=True
        ).values_list("vehicle_card_id", flat=True)
        if x is not None
    }


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


def _unconfirmed_counts(company: Company, where: Area) -> list[Block]:
    """§ 21.3.1, § 11.6.3: inventories submitted and waiting for the confirmation of the
    adjustment."""
    from c4rsys.warehouse.models import InventoryCount

    counts = InventoryCount.objects.filter(company=company, state=InventoryCount.State.SUBMITTED)
    if not where.whole:
        counts = counts.filter(context__location_id__in=where.location_ids)
    return [
        Block(
            "warehouse.unconfirmed_counts",
            "warehouse",
            "count",
            counts.count(),
            area=where,
            link="/warehouse?tab=inventory",
        )
    ]


def _requests(company: Company, now: datetime) -> list[Block]:
    """§ 21.3.1, § 6.3.8, § 6.4.4, § 8.3.1: requests addressed to the company and waiting for its
    answer — to link a card with its profile, for fresh data of the profile, to the company as
    the owner of a vehicle. PROPOSAL: counted for those who answer them («company.link.answer»
    for the whole company), since the requests are not bound to a location."""
    from c4rsys.fleet import owner_requests
    from c4rsys.parties import data_requests, links

    count = (
        links.for_company(company.id).filter(state="pending", expires_at__gt=now).count()
        + data_requests.for_company(company.id).filter(state="pending", expires_at__gt=now).count()
        + owner_requests.for_company(company.id).filter(state="open", expires_at__gt=now).count()
    )
    return [
        Block(
            "company.pending_requests",
            "company",
            "count",
            count,
            area=Area(True, frozenset()),
            link="/link-requests",
        )
    ]


def build(actor: Actor, company: Company, filters: Filters) -> list[Block]:
    now = timezone.now()
    blocks: list[Block] = []
    rental = area(actor, "rental.view", company, filters, "rental")
    if rental is not None:
        blocks += _rentals(company, rental, filters, now)
    fleet = area(actor, "vehicle.view", company, filters)
    if fleet is not None:
        blocks += _fleet(company, fleet, filters)
    blocks += _money_blocks(actor, company, filters)
    service = area(actor, "service.view", company, filters, "service")
    if service is not None:
        blocks += _maintenance(company, service, filters)
    stock = area(actor, "warehouse.view", company, filters, "warehouse")
    if stock is not None:
        blocks += _low_stock(company, stock)
        blocks += _unconfirmed_counts(company, stock)
    if filters.location_id is None and access.RightsResolver().has_permission(
        actor, "company.link.answer", whole_company=True
    ):
        blocks += _requests(company, now)
    docs = area(actor, "documents.view", company, filters)
    if docs is not None:
        blocks += _documents(actor, company, docs)
    return blocks
