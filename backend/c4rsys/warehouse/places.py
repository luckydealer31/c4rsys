"""Storage places of a warehouse (ТЗ § 11.1.9; customer's decision of 22.11.2026).

A place — «Rack 3, shelf B» — is a record of the list of its warehouse. The list is kept in the
settings of the warehouse by whoever manages them, as the posts of a service are; the storekeeper
only chooses. A unit or a lot lies in one place or in none; a renamed place is renamed for all
that lies there. (From 17.11.2026 to 22.11.2026 the places were tags of a group of the company;
the tags are one list for the whole company, so two warehouses could not both have a «Shelf 1».)

In the API a place is still given and answered as a list of names — empty or with one name.
"""

from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction

from c4rsys.companies.models import ActivityContext
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse.models import Asset, Lot, StoragePlace

ASSET = "warehouse_asset"
LOT = "warehouse_lot"
KINDS = {"asset": ASSET, "lot": LOT}
_MODELS: dict[str, Any] = {ASSET: Asset, LOT: Lot}
NAME_MAX = 100


def _key(name: str) -> str:
    return " ".join(name.split()).lower()


def payload(place: StoragePlace) -> dict[str, Any]:
    return {
        "id": str(place.id),
        "version": place.version,
        "context_id": str(place.context_id),
        "name": place.name,
        "state": place.state,
    }


def of_contexts(company_id: UUID, context_ids: list[UUID]) -> list[StoragePlace]:
    return list(
        StoragePlace.objects.filter(company_id=company_id, context_id__in=context_ids).order_by(
            "name_key"
        )
    )


def create(ctx: CommandContext, context: ActivityContext, name: str) -> StoragePlace:
    name = " ".join(name.split())
    if not name:
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    try:
        with transaction.atomic():
            place = StoragePlace.objects.create(
                company_id=context.company_id, context=context, name=name, name_key=_key(name)
            )
    except IntegrityError as exc:
        raise errors.validation_error("name", code="PLACE_NAME_TAKEN") from exc
    ctx.audit(
        "warehouse.storage_place_created",
        object_type="warehouse_storage_place",
        object_id=place.id,
        after={"context": str(context.id), "name": name},
    )
    return place


def locked(company_id: UUID, place_id: UUID, expected_version: int) -> StoragePlace:
    place = (
        StoragePlace.objects.select_for_update().filter(company_id=company_id, pk=place_id).first()
    )
    if place is None:
        raise errors.not_found()
    if place.version != expected_version:
        raise errors.version_conflict()
    return place


def change(
    ctx: CommandContext, place: StoragePlace, *, name: str | None, archived: bool | None
) -> StoragePlace:
    """A place is renamed — for everything that lies there — or archived: an archived place is
    not offered any more, what lies there keeps it until it is moved."""
    before = {"name": place.name, "state": place.state}
    if name is not None:
        place.name = " ".join(name.split())
        place.name_key = _key(name)
    if archived is not None:
        place.state = StoragePlace.State.ARCHIVED if archived else StoragePlace.State.ACTIVE
    after = {"name": place.name, "state": place.state}
    if before == after:
        raise errors.business_rule("NOTHING_CHANGED")
    place.version += 1
    try:
        with transaction.atomic():
            place.save()
    except IntegrityError as exc:
        raise errors.validation_error("name", code="PLACE_NAME_TAKEN") from exc
    ctx.audit(
        "warehouse.storage_place_changed",
        object_type="warehouse_storage_place",
        object_id=place.id,
        before=before,
        after=after,
    )
    return place


def resolve(
    ctx: CommandContext, company_id: UUID, context_id: UUID, names: Any, field: str = "tags"
) -> StoragePlace | None:
    """The place of the warehouse with this name; none for an empty list. A place is chosen from
    the list of the warehouse: a name the list does not have is refused, and so is more than
    one name."""
    wanted = list(dict.fromkeys(_key(str(n)) for n in (names or []) if str(n).strip()))
    if not wanted:
        return None
    if len(wanted) > 1:
        raise errors.validation_error(field, code="PLACE_ONE_ONLY")
    place = StoragePlace.objects.filter(
        company_id=company_id,
        context_id=context_id,
        name_key=wanted[0],
        state=StoragePlace.State.ACTIVE,
    ).first()
    if place is None:
        raise errors.validation_error(field, code="PLACE_TAG_UNKNOWN", name=str(names[0]))
    return place


def put(
    ctx: CommandContext,
    company_id: UUID,
    kind: str,
    record_ids: list[UUID],
    place: StoragePlace | None,
) -> None:
    """Lays the saved records in the place; without a place they stay where they are."""
    if place is not None and record_ids:
        _MODELS[kind].objects.filter(company_id=company_id, pk__in=record_ids).update(place=place)


def replace(
    ctx: CommandContext, company_id: UUID, kind: str, record_id: UUID, place: StoragePlace | None
) -> None:
    _MODELS[kind].objects.filter(company_id=company_id, pk=record_id).update(place=place)


def names(
    company_id: UUID, kind: str, record_ids: list[UUID] | None = None
) -> dict[UUID, list[str]]:
    """The place of each record as a list of one name; records without a place are left out."""
    rows = _MODELS[kind].objects.filter(company_id=company_id, place__isnull=False)
    if record_ids is not None:
        rows = rows.filter(pk__in=record_ids)
    return {pk: [name] for pk, name in rows.values_list("pk", "place__name")}


def item_ids_by_text(company_id: UUID, text: str) -> set[UUID]:
    """The items that have a unit or a lot in a place with the text in its name: the search of
    the warehouse finds them."""
    key = _key(text)
    if not key:
        return set()
    found: set[UUID] = set()
    for model in (Asset, Lot):
        found |= set(
            model.objects.filter(company_id=company_id, place__name_key__contains=key).values_list(
                "item_id", flat=True
            )
        )
    return found
