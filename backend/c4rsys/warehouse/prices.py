"""Selling prices and minimum stock (ТЗ § 11.7.2, § 11.7.4–11.7.6, § 11.7.8).

A material sells at prices of the item or — in the lot mode — at prices of each lot. In either
mode the price of each packaging and the price of the base unit sold loose are set separately;
one is never derived from another. An individual item has the price of a piece, and a unit may
have its own price over it; so may a vehicle transferred to a warehouse. Prices are kept in the
main currency of the company. A changed price applies to new additions to orders only.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import Company
from c4rsys.fleet import services as fleet
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse.models import (
    Asset,
    Item,
    Lot,
    MinimumStock,
    Packaging,
    SalePrice,
    VehicleSalePrice,
)
from c4rsys.warehouse.stock import ZERO, company_currency, money_text, totals

ITEM_MODE = "item"
LOT_MODE = "lot"


def _item(company_id: UUID, item_id: UUID) -> Item:
    item = Item.objects.select_for_update().filter(company_id=company_id, pk=item_id).first()
    if item is None:
        raise errors.not_found()
    return item


def _packaging(
    item: Item, packaging_id: UUID | None, field: str = "packaging_id"
) -> Packaging | None:
    if packaging_id is None:
        return None
    if item.accounting != Item.Accounting.BATCH:
        raise errors.validation_error(field, code="PACKAGING_FOR_MATERIALS_ONLY")
    packaging = Packaging.objects.filter(item=item, pk=packaging_id).first()
    if packaging is None:
        raise errors.validation_error(field, code="PACKAGING_UNKNOWN")
    return packaging


def _write(
    ctx: CommandContext,
    company: Company,
    item: Item,
    lot: Lot | None,
    packaging: Packaging | None,
    amount: Decimal | None,
) -> None:
    current = SalePrice.objects.filter(item=item, lot=lot, packaging=packaging).first()
    before = str(current.amount) if current else None
    if amount is None:
        if current is not None:
            current.delete()
    elif current is None:
        SalePrice.objects.create(
            company=company,
            item=item,
            lot=lot,
            packaging=packaging,
            amount=amount,
            currency=company_currency(company).code,
            updated_by_user_id=ctx.actor.user_id,
        )
    else:
        current.amount = amount
        current.updated_by_user_id = ctx.actor.user_id
        current.updated_at = timezone.now()
        current.save(update_fields=["amount", "updated_by_user_id", "updated_at"])
    ctx.audit(
        "warehouse.price_set",
        object_type="warehouse_item",
        object_id=item.id,
        before={
            "lot": str(lot.id) if lot else None,
            "packaging": str(packaging.id) if packaging else None,
            "amount": before,
        },
        after={
            "lot": str(lot.id) if lot else None,
            "packaging": str(packaging.id) if packaging else None,
            "amount": str(amount) if amount is not None else None,
        },
    )


def set_price(
    ctx: CommandContext,
    company: Company,
    item_id: UUID,
    lot_id: UUID | None,
    packaging_id: UUID | None,
    amount: Decimal | None,
) -> Item:
    """A price is set on the level of the current mode: of the item, or of a lot in the lot
    mode (§ 11.7.6). Empty clears it."""
    item = _item(company.id, item_id)
    packaging = _packaging(item, packaging_id)
    lot = None
    if item.price_mode == LOT_MODE:
        if lot_id is None:
            raise errors.validation_error("lot_id", code="PRICE_LOT_REQUIRED")
        lot = Lot.objects.filter(company_id=company.id, pk=lot_id, item=item).first()
        if lot is None:
            raise errors.validation_error("lot_id", code="LOT_UNKNOWN")
    elif lot_id is not None:
        raise errors.validation_error("lot_id", code="PRICE_ITEM_MODE")
    _write(ctx, company, item, lot, packaging, amount)
    return item


def set_mode(
    ctx: CommandContext,
    company: Company,
    item_id: UUID,
    mode: str,
    prices: tuple[tuple[UUID | None, Decimal], ...],
) -> Item:
    """§ 11.7.2: to the lot mode, the item's prices of each packaging and of loose sale are first
    given to every lot; to the item mode, the employee states the new prices explicitly — no
    average is chosen. Lines already in orders keep their prices; purchase costs do not change."""
    item = _item(company.id, item_id)
    if item.accounting != Item.Accounting.BATCH:
        raise errors.business_rule("PRICE_MODE_FOR_MATERIALS_ONLY")
    if mode not in (ITEM_MODE, LOT_MODE):
        raise errors.validation_error("mode")
    if mode == item.price_mode:
        raise errors.business_rule("PRICE_MODE_ALREADY", mode=mode)
    before = item.price_mode
    if mode == LOT_MODE:
        if prices:
            raise errors.validation_error("prices", code="PRICES_ONLY_FOR_ITEM_MODE")
        general = list(SalePrice.objects.filter(item=item, lot__isnull=True))
        for lot in Lot.objects.filter(item=item):
            for price in general:
                SalePrice.objects.update_or_create(
                    item=item,
                    lot=lot,
                    packaging=price.packaging,
                    defaults={
                        "company": company,
                        "amount": price.amount,
                        "currency": price.currency,
                        "updated_by_user_id": ctx.actor.user_id,
                        "updated_at": timezone.now(),
                    },
                )
        SalePrice.objects.filter(item=item, lot__isnull=True).delete()
    else:
        SalePrice.objects.filter(item=item, lot__isnull=False).delete()
        SalePrice.objects.filter(item=item, lot__isnull=True).delete()
        for index, (packaging_id, amount) in enumerate(prices):
            packaging = _packaging(item, packaging_id, f"prices[{index}].packaging_id")
            SalePrice.objects.create(
                company=company,
                item=item,
                packaging=packaging,
                amount=amount,
                currency=company_currency(company).code,
                updated_by_user_id=ctx.actor.user_id,
            )
    item.price_mode = mode
    item.version += 1
    item.save(update_fields=["price_mode", "version", "updated_at"])
    ctx.audit(
        "warehouse.price_mode_changed",
        object_type="warehouse_item",
        object_id=item.id,
        before={"price_mode": before},
        after={"price_mode": mode, "prices": [[str(p) if p else None, str(a)] for p, a in prices]},
    )
    return item


def set_asset_price(
    ctx: CommandContext, company: Company, asset_id: UUID, amount: Decimal | None
) -> Asset:
    """A unit's own selling price over the price of the item; its purchase cost stays
    (§ 11.7.4)."""
    asset = Asset.objects.select_for_update().filter(company_id=company.id, pk=asset_id).first()
    if asset is None:
        raise errors.not_found()
    before = asset.sale_price
    asset.sale_price = amount
    asset.save(update_fields=["sale_price"])
    ctx.audit(
        "warehouse.asset_price_set",
        object_type="warehouse_asset",
        object_id=asset.id,
        before={"sale_price": str(before) if before is not None else None},
        after={"sale_price": str(amount) if amount is not None else None},
    )
    return asset


def set_vehicle_price(
    ctx: CommandContext, company: Company, card_id: UUID, amount: Decimal | None
) -> None:
    """The price of a vehicle in a warehouse (§ 11.7.4)."""
    card = fleet.get_card(company.id, card_id)
    if card.storage_context_id is None:
        raise errors.business_rule("VEHICLE_NOT_IN_WAREHOUSE")
    current = VehicleSalePrice.objects.filter(vehicle_id=card.id).first()
    before = str(current.amount) if current else None
    if amount is None:
        if current is not None:
            current.delete()
    else:
        VehicleSalePrice.objects.update_or_create(
            vehicle_id=card.id,
            defaults={
                "company": company,
                "amount": amount,
                "currency": company_currency(company).code,
                "updated_by_user_id": ctx.actor.user_id,
                "updated_at": timezone.now(),
            },
        )
    ctx.audit(
        "warehouse.vehicle_price_set",
        object_type="vehicle_card",
        object_id=card.id,
        before={"sale_price": before},
        after={"sale_price": str(amount) if amount is not None else None},
    )


def vehicle_prices(company_id: UUID) -> dict[UUID, tuple[str | None, str]]:
    return {
        p.vehicle_id: (money_text(p.amount, p.currency), p.currency)
        for p in VehicleSalePrice.objects.filter(company_id=company_id)
    }


def price_for(
    item: Item,
    *,
    lot: Lot | None = None,
    packaging: Packaging | None = None,
    asset: Asset | None = None,
) -> Decimal | None:
    """The applied price for an addition to an order (part С6): a unit's own price first, then
    the price of the item or — in the lot mode — of the lot. Unknown — not for sale yet."""
    if asset is not None and asset.sale_price is not None:
        return asset.sale_price
    level_lot = (
        lot if item.price_mode == LOT_MODE and item.accounting == Item.Accounting.BATCH else None
    )
    found = SalePrice.objects.filter(item=item, lot=level_lot, packaging=packaging).first()
    return found.amount if found else None


# --- Minimum stock (§ 11.7.8) ---------------------------------------------------------------------


def set_minimum(
    ctx: CommandContext, company_id: UUID, item_id: UUID, context_id: UUID, quantity: Decimal | None
) -> None:
    item = _item(company_id, item_id)
    context = locations.storage_contexts(company_id).filter(pk=context_id).first()
    if context is None:
        raise errors.validation_error("context_id", code="WAREHOUSE_NOT_USABLE")
    if (
        quantity is not None
        and item.accounting == Item.Accounting.INDIVIDUAL
        and quantity != quantity.to_integral_value()
    ):
        raise errors.validation_error("quantity", code="QUANTITY_WHOLE_UNITS")
    current = MinimumStock.objects.filter(item=item, context=context).first()
    before = str(current.quantity) if current else None
    if quantity is None:
        if current is not None:
            current.delete()
    else:
        MinimumStock.objects.update_or_create(
            item=item, context=context, defaults={"company_id": company_id, "quantity": quantity}
        )
    ctx.audit(
        "warehouse.minimum_set",
        object_type="warehouse_item",
        object_id=item.id,
        before={"context": str(context.id), "quantity": before},
        after={
            "context": str(context.id),
            "quantity": str(quantity) if quantity is not None else None,
        },
    )


def minimums(company_id: UUID, context_ids: set[UUID]) -> dict[tuple[UUID, UUID], Decimal]:
    return {
        (m.item_id, m.context_id): m.quantity
        for m in MinimumStock.objects.filter(company_id=company_id, context_id__in=context_ids)
    }


def low_stock(company_id: UUID, context_ids: set[UUID]) -> list[dict[str, Any]]:
    """Items whose available quantity in a warehouse is below its threshold. Reserves come
    later, so the available quantity is the stock in the warehouse (decision of 25.10.2026)."""
    thresholds = minimums(company_id, context_ids)
    if not thresholds:
        return []
    stock = totals(company_id, context_ids)
    items = {i.id: i for i in Item.objects.filter(pk__in={item for item, _ in thresholds})}
    places = {c.id: c for c in locations.storage_contexts(company_id).filter(pk__in=context_ids)}
    result: list[dict[str, Any]] = []
    for (item_id, context_id), minimum in thresholds.items():
        available = stock.get(item_id, {}).get(context_id, {}).get("quantity", ZERO)
        if available < minimum:
            item = items[item_id]
            result.append(
                {
                    "item": {"id": str(item.id), "name": item.name, "unit": item.unit},
                    "context_id": str(context_id),
                    "location_name": places[context_id].location.name,
                    "available": str(available),
                    "minimum": str(minimum),
                }
            )
    result.sort(key=lambda row: (str(row["location_name"]), str(row["item"]["name"])))
    return result


# --- Reading --------------------------------------------------------------------------------------


def item_prices(company: Company, item_id: UUID, context_ids: set[UUID]) -> dict[str, Any]:
    item = Item.objects.filter(company_id=company.id, pk=item_id).first()
    if item is None:
        raise errors.not_found()
    currency = company.main_currency_id or ""
    rows = list(SalePrice.objects.filter(item=item).select_related("lot"))

    def entry(price: SalePrice) -> dict[str, Any]:
        return {
            "packaging_id": str(price.packaging_id) if price.packaging_id else None,
            "amount": money_text(price.amount, price.currency),
            "updated_at": price.updated_at.isoformat(),
        }

    lots = []
    if item.accounting == Item.Accounting.BATCH:
        for lot in Lot.objects.filter(item=item, context_id__in=context_ids).order_by("created_at"):
            lots.append(
                {
                    "id": str(lot.id),
                    "public_id": lot.public_id,
                    "prices": [entry(p) for p in rows if p.lot_id == lot.id],
                }
            )
    assets = [
        {
            "id": str(a.id),
            "public_id": a.public_id,
            "sale_price": money_text(a.sale_price, currency) if a.sale_price is not None else None,
        }
        for a in Asset.objects.filter(
            item=item, context_id__in=context_ids, state=Asset.State.IN_STOCK
        ).order_by("created_at")
    ]
    places = locations.storage_contexts(company.id).filter(pk__in=context_ids)
    thresholds = minimums(company.id, context_ids)
    return {
        "mode": item.price_mode,
        "currency": currency,
        "item_prices": [entry(p) for p in rows if p.lot_id is None],
        "lots": lots,
        "assets": assets,
        "minimums": [
            {
                "context_id": str(place.id),
                "location_name": place.location.name,
                "quantity": str(thresholds[(item.id, place.id)])
                if (item.id, place.id) in thresholds
                else None,
            }
            for place in places
        ],
    }
