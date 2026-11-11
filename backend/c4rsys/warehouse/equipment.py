"""Warehouse units used as equipment of additional rental services (ТЗ § 11.1.5, § 13.4.7,
§ 13.4.12–13.4.13; customer's decisions of 11.11.2026).

The rental module attaches existing units (assets with their AST ID) to its services and never
copies them; it reads and changes them only through these functions. A unit lies in the storage
of a location — the warehouse context of that location (decision of 25.10.2026: only the
warehouse context stores). Handing out takes the unit out of the stock; accepting the return
puts it into the storage of the location of the return, which may be another one (§ 13.4.13):
no separate move is made. Both are written to the movement journal.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from c4rsys.companies import locations
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse import reserves
from c4rsys.warehouse.models import Asset, StockMovement

IN_STOCK = Asset.State.IN_STOCK


def _info(asset: Asset) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "public_id": asset.public_id,
        "item_name": asset.item.name,
        "serial_number": asset.serial_number,
        "location_id": str(asset.context.location_id),
        "location_name": asset.context.location.name,
        "state": asset.state,
    }


def _units(company_id: UUID) -> Any:
    return Asset.objects.filter(company_id=company_id).select_related("item", "context__location")


def describe(company_id: UUID, asset_ids: list[UUID]) -> list[dict[str, Any]]:
    """Units by their ids, for the catalog of services and the lines: the AST ID, the name of
    the item, the serial number, the location of the storage and the state."""
    found = _units(company_id).filter(pk__in=asset_ids)
    return [_info(a) for a in sorted(found, key=lambda a: (a.item.name, a.public_id))]


def in_stock(company_id: UUID) -> list[dict[str, Any]]:
    """Every unit of the company lying in a storage now: what may be attached to a service."""
    found = _units(company_id).filter(state=IN_STOCK)
    return [
        _info(a)
        for a in sorted(found, key=lambda a: (a.context.location.name, a.item.name, a.public_id))
    ]


def require_units(company_id: UUID, asset_ids: frozenset[UUID], field: str) -> None:
    """Every id is a unit of this company."""
    found = Asset.objects.filter(company_id=company_id, pk__in=asset_ids).count()
    if found != len(asset_ids):
        raise errors.validation_error(field, code="ASSET_UNKNOWN")


def lock(company_id: UUID, asset_id: UUID, field: str = "asset_id") -> Asset:
    """The unit locked for the command: two requests for one unit go one after the other."""
    asset = Asset.objects.select_for_update().filter(company_id=company_id, pk=asset_id).first()
    if asset is None:
        raise errors.validation_error(field, code="ASSET_UNKNOWN")
    return asset


def unavailable_reason(asset: Asset | dict[str, Any], location_id: UUID) -> str | None:
    """Why the unit cannot be taken from the storage of the location now: it lies in the storage
    of another location, is not in stock (handed out, sold, written off…) or is held by a
    warehouse reserve or an open sale. None: it lies there and is free for the warehouse."""
    if isinstance(asset, Asset):
        state, place, asset_id = asset.state, asset.context.location_id, asset.id
    else:
        state, place, asset_id = asset["state"], UUID(asset["location_id"]), UUID(asset["id"])
    if state != IN_STOCK:
        return "not_in_stock"
    if place != location_id:
        return "other_location"
    if reserves.asset_reserved(asset_id):
        return "reserved"
    return None


def _journal(ctx: CommandContext, asset: Asset, kind: str, quantity: int) -> None:
    StockMovement.objects.create(
        company_id=asset.company_id,
        kind=kind,
        context_id=asset.context_id,
        item_id=asset.item_id,
        asset=asset,
        quantity=Decimal(quantity),
        actor_user_id=ctx.actor.user_id,
    )


def _snapshot(asset: Asset) -> dict[str, Any]:
    return {"state": asset.state, "context": str(asset.context_id)}


def hand_out(ctx: CommandContext, company_id: UUID, asset_id: UUID, location_id: UUID) -> Asset:
    """The unit leaves the storage of the location with a return expected (§ 13.4.12). Only a
    unit lying there now and not held by the warehouse."""
    asset = lock(company_id, asset_id)
    reason = unavailable_reason(asset, location_id)
    if reason is not None:
        raise errors.business_rule(
            "EQUIPMENT_NOT_AVAILABLE", field="asset_id", asset=asset.public_id, reason=reason
        )
    before = _snapshot(asset)
    asset.state = Asset.State.ISSUED
    asset.save(update_fields=["state"])
    _journal(ctx, asset, StockMovement.Kind.ISSUE, -1)
    ctx.audit(
        "warehouse.equipment_handed_out",
        object_type="warehouse_asset",
        object_id=asset.id,
        before=before,
        after=_snapshot(asset),
    )
    return asset


def accept_return(
    ctx: CommandContext, company_id: UUID, asset_id: UUID, location_id: UUID
) -> Asset:
    """The unit handed out comes back into the storage of the location of the return, which
    becomes its place (§ 13.4.13). The receiving storage must be enabled (§ 11.1.5)."""
    asset = lock(company_id, asset_id)
    if asset.state != Asset.State.ISSUED:
        raise errors.business_rule(
            "EQUIPMENT_NOT_HANDED_OUT", field="asset_id", asset=asset.public_id, state=asset.state
        )
    storage = locations.storage_of_location(company_id, location_id)
    if storage is None:
        raise errors.business_rule("RETURN_STORAGE_NOT_USABLE", field="location_id")
    before = _snapshot(asset)
    asset.state = IN_STOCK
    asset.context = storage
    asset.save(update_fields=["state", "context"])
    _journal(ctx, asset, StockMovement.Kind.RETURN, 1)
    ctx.audit(
        "warehouse.equipment_returned",
        object_type="warehouse_asset",
        object_id=asset.id,
        before=before,
        after=_snapshot(asset),
    )
    return asset
