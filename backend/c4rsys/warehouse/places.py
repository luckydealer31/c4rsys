"""Marks of a storage place on units and lots (ТЗ § 11.1.9; customer's decision of 17.11.2026).

A mark — «Rack 3, shelf B» — is no free text any more: it is a tag of the group «Storage places»
of the company's tags. The warehouse puts the tags on its units and lots through the tags
(A04); a new place is made by whoever may make tags in that group — the employees named in it
and those who manage the tags — and is chosen from the list by the others.
"""

from typing import Any
from uuid import UUID

from c4rsys.companies import access
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.tags import services as tags
from c4rsys.warehouse.models import Asset, Lot

ASSET = "warehouse_asset"
LOT = "warehouse_lot"
KINDS = {"asset": ASSET, "lot": LOT}


def may_create(actor: Actor, company_id: UUID) -> bool:
    """Whether the employee makes new storage places."""
    if access.RightsResolver().has_permission(actor, "tags.manage"):
        return True
    return tags.is_named(tags.storage_group(company_id, actor.user_id).id, actor.employment_id)


def resolve(ctx: CommandContext, company_id: UUID, names: Any, field: str = "tags") -> list[Any]:
    """The tags of the places with these names; an unknown name is a new place for whoever may
    make one, and is refused for the others."""
    names = [str(n) for n in (names or [])]
    if not names:
        return []
    return tags.place_tags(ctx, company_id, names, may_create(ctx.actor, company_id), field)


def put(
    ctx: CommandContext, company_id: UUID, kind: str, record_ids: list[UUID], marks: list[Any]
) -> None:
    """Adds the places to what the records have."""
    tags.add_company_links(ctx.actor.user_id, company_id, kind, record_ids, marks)


def replace(
    ctx: CommandContext, company_id: UUID, kind: str, record_id: UUID, marks: list[Any]
) -> None:
    tags.replace_company_links(ctx.actor.user_id, company_id, kind, record_id, marks)


def names(
    company_id: UUID, kind: str, record_ids: list[UUID] | None = None
) -> dict[UUID, list[str]]:
    return tags.names_by_object(company_id, kind, record_ids)


def _items_of_assets(company_id: UUID, ids: list[UUID]) -> list[UUID]:
    return list(
        Asset.objects.filter(company_id=company_id, pk__in=ids).values_list("item_id", flat=True)
    )


def _items_of_lots(company_id: UUID, ids: list[UUID]) -> list[UUID]:
    return list(
        Lot.objects.filter(company_id=company_id, pk__in=ids).values_list("item_id", flat=True)
    )


def item_ids_by_text(company_id: UUID, text: str) -> set[UUID]:
    """The items that have a unit or a lot in a place with the text in its name: the search of
    the warehouse finds them."""
    found = set(
        _items_of_assets(company_id, list(tags.matching_ids(company_id, None, ASSET, text)))
    )
    return found | set(
        _items_of_lots(company_id, list(tags.matching_ids(company_id, None, LOT, text)))
    )


def register() -> None:
    """On the page of a tag a unit and a lot are shown as their item."""
    tags.register_shown_as(ASSET, "item", _items_of_assets)
    tags.register_shown_as(LOT, "item", _items_of_lots)
