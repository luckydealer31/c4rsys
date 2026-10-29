"""Money of deliveries: the catalog of delivery services, the charge to the client and the
actual expenses of the company (ТЗ § 15.2.2–15.2.3, § 15.2.6; decisions Д-4…Д-6).

* A client delivery is charged at once by the price of the catalog service or the price entered
  by hand; a changed price recharges it. A delivery cancelled before the start is not charged; a
  completed one with a refusal and a terminated one keep the charge (decision Д-4). An internal
  move has no client charge (§ 15.2.6).
* Changing the price substituted from the catalog needs «Change the delivery price» (decision
  Д-5); the catalog is kept with «Delivery catalog».
* An expense is a payout from a cash desk or account with an expense article, linked to the trip;
  it does not change the client's price (§ 15.2.3; decision Д-6).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.delivery.models import Delivery, DeliveryExpense, DeliveryService
from c4rsys.finance import charges, records
from c4rsys.finance.charges import charge_ids_for_source
from c4rsys.finance.models import ChargeEntry, MoneyMovement
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money

SOURCE = ChargeEntry.SourceType.DELIVERY


def amount_text(value: Decimal | None) -> str | None:
    return None if value is None else f"{Decimal(value).normalize():f}"


# --- Catalog (§ 15.2.6) --------------------------------------------------------------------------


def service_snapshot(service: DeliveryService) -> dict[str, Any]:
    return {
        "name": service.name,
        "price": amount_text(service.price),
        "currency": service.currency_id,
        "state": service.state,
    }


def create_service(
    ctx: CommandContext, company: Company, name: str, price: Decimal
) -> DeliveryService:
    if company.main_currency_id is None:
        raise errors.business_rule("COMPANY_WITHOUT_CURRENCY")
    service = DeliveryService.objects.create(
        company=company,
        name=name,
        price=price,
        currency_id=company.main_currency_id,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "delivery_service.created",
        object_type="delivery_service",
        object_id=service.id,
        after=service_snapshot(service),
    )
    return service


def change_service(
    ctx: CommandContext,
    service: DeliveryService,
    *,
    name: str | None,
    price: Decimal | None,
    archived: bool | None,
) -> DeliveryService:
    """Added deliveries keep their price; the catalog changes only new ones."""
    before = service_snapshot(service)
    if name is not None:
        service.name = name
    if price is not None:
        service.price = price
    if archived is not None:
        service.state = DeliveryService.State.ARCHIVED if archived else DeliveryService.State.ACTIVE
    after = service_snapshot(service)
    if before == after:
        raise errors.business_rule("NOTHING_CHANGED")
    service.version += 1
    service.save()
    ctx.audit(
        "delivery_service.changed",
        object_type="delivery_service",
        object_id=service.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return service


def locked_service(company_id: UUID, service_id: UUID, expected_version: int) -> DeliveryService:
    service = (
        DeliveryService.objects.select_for_update()
        .filter(company_id=company_id, pk=service_id)
        .first()
    )
    if service is None:
        raise errors.not_found()
    if service.version != expected_version:
        raise errors.version_conflict()
    return service


# --- The client's price (§ 15.2.2, § 15.2.6) -----------------------------------------------------


@dataclass(frozen=True)
class Price:
    """A service of the catalog, a price, or both. `override` — the price differs from the
    substituted catalog price; that needs the right (decision Д-5)."""

    service_id: UUID | None
    price: Decimal | None

    def resolve(self, company_id: UUID) -> tuple[DeliveryService | None, Decimal | None, bool]:
        service = None
        if self.service_id is not None:
            service = DeliveryService.objects.filter(
                company_id=company_id, pk=self.service_id
            ).first()
            if service is None:
                raise errors.validation_error("service_id", code="DELIVERY_SERVICE_UNKNOWN")
            if service.state != DeliveryService.State.ACTIVE:
                raise errors.business_rule("DELIVERY_SERVICE_ARCHIVED", field="service_id")
        price = self.price if self.price is not None else (service.price if service else None)
        override = service is not None and self.price is not None and self.price != service.price
        return service, price, override

    @property
    def given(self) -> bool:
        return self.service_id is not None or self.price is not None


def _charge(ctx: CommandContext, d: Delivery, reason: str) -> None:
    client_id = orders.chargeable_client(d.order_id)
    if client_id is None or d.currency_id is None:
        return
    amount = Decimal(0) if d.state == Delivery.State.CANCELLED or d.price is None else d.price
    charges.set_charge(
        ctx,
        company_id=d.company_id,
        order_id=d.order_id,
        client_id=client_id,
        source_type=SOURCE,
        source_id=d.id,
        amount=amount,
        currency=d.currency_id,
        reason=reason,
        calculation={
            "service": str(d.service_id) if d.service_id else None,
            "price": amount_text(d.price),
            "state": d.state,
        },
    )


def apply_price(ctx: CommandContext, d: Delivery, price: Price, reason: str) -> None:
    """Sets the paid position of a client delivery and charges it; an internal order takes no
    client price (§ 15.2.6)."""
    if not price.given:
        return
    order = orders.get_order(d.company_id, d.order_id)
    if order.is_internal:
        raise errors.business_rule("DELIVERY_INTERNAL_NO_PRICE", field="price")
    service, amount, _ = price.resolve(d.company_id)
    minor = Currency.objects.get(pk=service.currency_id if service else _currency(d)).minor_units
    d.service = service
    d.price = round_money(amount, minor) if amount is not None else None
    d.currency_id = service.currency_id if service else _currency(d)
    d.save(update_fields=["service", "price", "currency", "updated_at"])
    _charge(ctx, d, reason)


def _currency(d: Delivery) -> str:
    code = (
        Company.objects.filter(pk=d.company_id).values_list("main_currency_id", flat=True).first()
    )
    if code is None:
        raise errors.business_rule("COMPANY_WITHOUT_CURRENCY")
    return str(code)


def recharge(ctx: CommandContext, d: Delivery, reason: str) -> None:
    """After a state change: a cancelled delivery brings its charge to zero (decision Д-4)."""
    _charge(ctx, d, reason)


def on_client_assigned(ctx: CommandContext, company_id: UUID, order_id: UUID) -> None:
    for d in Delivery.objects.select_for_update().filter(company_id=company_id, order_id=order_id):
        _charge(ctx, d, "client_assigned")


def estimated_for_order(company_id: UUID, order_id: UUID) -> dict[str, Decimal]:
    if orders.chargeable_client(order_id) is not None:
        return {}
    result: dict[str, Decimal] = {}
    for d in Delivery.objects.filter(
        company_id=company_id, order_id=order_id, price__isnull=False
    ).exclude(state=Delivery.State.CANCELLED):
        if d.price is None or d.currency_id is None:
            continue
        result[d.currency_id] = result.get(d.currency_id, Decimal(0)) + d.price
    return result


def history(delivery_id: UUID) -> list[tuple[str, str]]:
    return [("charge_entry", str(c)) for c in charge_ids_for_source(SOURCE, delivery_id)]


# --- Expenses (§ 15.2.3) -------------------------------------------------------------------------


@dataclass(frozen=True)
class NewExpense:
    cash_account_id: UUID
    amount: Decimal
    currency: str
    article_id: UUID
    counterparty_id: UUID | None = None
    note: str = ""


def add_expense(ctx: CommandContext, d: Delivery, data: NewExpense) -> DeliveryExpense:
    if d.state == Delivery.State.CANCELLED:
        raise errors.business_rule("DELIVERY_CANCELLED")
    movement = records.record_manual(
        ctx,
        d.company_id,
        records.ManualRecord(
            cash_account_id=data.cash_account_id,
            direction=MoneyMovement.Direction.OUT,
            amount=data.amount,
            currency=data.currency,
            article_id=data.article_id,
            counterparty_id=data.counterparty_id,
            location_id=d.from_location_id,
            vehicle_id=d.vehicle_card_id,
            note=data.note or f"delivery {d.number}",
        ),
    )
    expense = DeliveryExpense.objects.create(
        company_id=d.company_id,
        delivery=d,
        money_movement=movement,
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "delivery.expense_added",
        object_type="delivery",
        object_id=d.id,
        after={
            "money_movement": str(movement.id),
            "amount": data.amount,
            "currency": data.currency,
        },
    )
    return expense


def expenses_of(d: Delivery) -> list[dict[str, Any]]:
    return [
        {
            "id": str(e.id),
            "money_movement_id": str(e.money_movement_id),
            "number": e.money_movement.number,
            "amount": amount_text(e.money_movement.amount),
            "currency": e.money_movement.currency_id,
            "article_id": str(e.money_movement.article_id) if e.money_movement.article_id else None,
            "note": e.money_movement.note,
            "posted_at": e.money_movement.posted_at.isoformat()
            if e.money_movement.posted_at
            else None,
        }
        for e in DeliveryExpense.objects.select_related("money_movement")
        .filter(delivery=d)
        .order_by("created_at")
    ]
