"""System dictionaries of the catalog kept by the platform administrator (ТЗ § 11.1.12–11.1.14).

* Manufacturers: the administrator adds a manufacturer to the common list and links the values
  entered earlier as «Other» with it; the history stays — every changed item is journaled in its
  company and in the system journal. Other spellings are kept as aliases for search, without new
  manufacturers.
* Attributes of parts: besides the base attributes of § 11.1.13, the administrator adds system
  attributes and their values. No value — «Not given», not universal. An attribute is used for
  search and filtering and never blocks an installation.

PROPOSAL (block 15): a linked «Other» text becomes an alias of the manufacturer; an attribute
allows several values only if it is marked so, and the mark is only switched on; an archived
attribute or value is not offered any more and stays on the items that have it; the base
attributes of § 11.1.13 keep their fixed values.
"""

import re
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q

from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse.models import Item, Manufacturer, PartAttribute, PartAttributeValue

CODE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
NAME_MAX = 100
MAX_VALUES = 50


# --- Attributes ---------------------------------------------------------------------------------


def _names(data: dict[str, str], field: str) -> dict[str, str]:
    names = {}
    for lang in ("ru", "en", "th"):
        value = (data.get(lang) or "").strip()
        if not value:
            raise errors.validation_error(f"{field}.{lang}", code="FIELD_REQUIRED")
        if len(value) > NAME_MAX:
            raise errors.validation_error(f"{field}.{lang}", code="TOO_LONG", max=NAME_MAX)
        names[f"name_{lang}"] = value
    return names


def _code(value: str, field: str) -> str:
    if not CODE.match(value):
        raise errors.validation_error(field, code="CODE_INVALID")
    return value


def attributes() -> list[PartAttribute]:
    return list(PartAttribute.objects.prefetch_related("values").order_by("position", "code"))


def attribute_payload(attribute: PartAttribute) -> dict[str, Any]:
    return {
        "id": str(attribute.id),
        "code": attribute.code,
        "name": {
            "ru": attribute.name_ru,
            "en": attribute.name_en,
            "th": attribute.name_th,
        },
        "multiple": attribute.multiple,
        "state": attribute.state,
        "values": [
            {
                "code": v.code,
                "name": {"ru": v.name_ru, "en": v.name_en, "th": v.name_th},
            }
            for v in sorted(attribute.values.all(), key=lambda v: (v.position, v.code))
        ],
    }


def add_attribute(
    ctx: CommandContext,
    *,
    code: str,
    names: dict[str, str],
    multiple: bool,
    values: list[tuple[str, dict[str, str]]],
) -> PartAttribute:
    from c4rsys.warehouse import catalog

    code = _code(code, "code")
    if code in catalog.ATTRIBUTES:
        raise errors.business_rule("ATTRIBUTE_EXISTS", field="code")
    if not values:
        raise errors.validation_error("values", code="FIELD_REQUIRED")
    if len(values) > MAX_VALUES:
        raise errors.validation_error("values", code="TOO_MANY_VALUES", max=MAX_VALUES)
    if len({c for c, _ in values}) != len(values):
        raise errors.validation_error("values", code="DUPLICATE_VALUES")
    try:
        with transaction.atomic():
            attribute = PartAttribute.objects.create(
                code=code,
                multiple=multiple,
                position=PartAttribute.objects.count(),
                **_names(names, "name"),
            )
    except IntegrityError as exc:
        raise errors.business_rule("ATTRIBUTE_EXISTS", field="code") from exc
    for index, (value_code, value_names) in enumerate(values):
        PartAttributeValue.objects.create(
            attribute=attribute,
            code=_code(value_code, f"values[{index}].code"),
            position=index,
            **_names(value_names, f"values[{index}].name"),
        )
    ctx.audit(
        "warehouse.attribute_added",
        object_type="part_attribute",
        object_id=attribute.id,
        after={"code": code, "multiple": multiple, "values": [c for c, _ in values]},
    )
    return attribute


def change_attribute(
    ctx: CommandContext,
    attribute_id: UUID,
    *,
    names: dict[str, str] | None,
    multiple: bool | None,
    archived: bool | None,
    add_values: list[tuple[str, dict[str, str]]],
) -> PartAttribute:
    attribute = PartAttribute.objects.select_for_update().filter(pk=attribute_id).first()
    if attribute is None:
        raise errors.not_found()
    before = attribute_payload(attribute)
    if names is not None:
        for key, value in _names(names, "name").items():
            setattr(attribute, key, value)
    if multiple is not None and multiple != attribute.multiple:
        if not multiple:
            # Items may already have several values; the mark is only switched on.
            raise errors.business_rule("ATTRIBUTE_MULTIPLE_ONLY_ON")
        attribute.multiple = True
    if archived is not None:
        attribute.state = PartAttribute.State.ARCHIVED if archived else PartAttribute.State.ACTIVE
    attribute.save()
    existing = set(attribute.values.values_list("code", flat=True))
    for index, (value_code, value_names) in enumerate(add_values):
        value_code = _code(value_code, f"add_values[{index}].code")
        if value_code in existing:
            raise errors.business_rule("ATTRIBUTE_VALUE_EXISTS", field=f"add_values[{index}]")
        existing.add(value_code)
        PartAttributeValue.objects.create(
            attribute=attribute,
            code=value_code,
            position=len(existing),
            **_names(value_names, f"add_values[{index}].name"),
        )
    after = attribute_payload(PartAttribute.objects.get(pk=attribute.id))
    ctx.audit(
        "warehouse.attribute_changed",
        object_type="part_attribute",
        object_id=attribute.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return attribute


def validate_extra(value: Any, current: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """Values of the system attributes of an item: known codes only; one value unless the
    attribute allows several. An archived attribute keeps only what the item already had."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise errors.validation_error("extra_attributes")
    known = {a.code: a for a in attributes()}
    result: dict[str, list[str]] = {}
    for code, chosen in value.items():
        field = f"extra_attributes.{code}"
        attribute = known.get(code)
        if attribute is None:
            raise errors.validation_error(field, code="ATTRIBUTE_UNKNOWN")
        if chosen in (None, "", []):
            continue
        if isinstance(chosen, str):
            chosen = [chosen]
        if not isinstance(chosen, list) or not all(isinstance(c, str) for c in chosen):
            raise errors.validation_error(field)
        chosen = sorted(set(chosen))
        allowed = {v.code for v in attribute.values.all()}
        if not set(chosen) <= allowed:
            raise errors.validation_error(field, code="ATTRIBUTE_VALUE_UNKNOWN")
        if len(chosen) > 1 and not attribute.multiple:
            raise errors.validation_error(field, code="ATTRIBUTE_ONE_VALUE")
        if attribute.state == PartAttribute.State.ARCHIVED and chosen != (current or {}).get(code):
            raise errors.validation_error(field, code="ATTRIBUTE_ARCHIVED")
        result[code] = chosen
    return result


# --- Manufacturers ------------------------------------------------------------------------------


def manufacturer_payload(m: Manufacturer) -> dict[str, Any]:
    return {"id": str(m.id), "name": m.name, "aliases": m.aliases}


def _aliases(values: list[str], name: str) -> list[str]:
    result: list[str] = []
    for value in values:
        text = value.strip()[:150]
        if (
            text
            and text.lower() != name.lower()
            and text.lower() not in {a.lower() for a in result}
        ):
            result.append(text)
    return result


def add_manufacturer(ctx: CommandContext, name: str, aliases: list[str]) -> Manufacturer:
    name = name.strip()
    if not name:
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    if Manufacturer.objects.filter(name__iexact=name).exists():
        raise errors.business_rule("MANUFACTURER_EXISTS", field="name")
    manufacturer = Manufacturer.objects.create(name=name, aliases=_aliases(aliases, name))
    ctx.audit(
        "warehouse.manufacturer_added",
        object_type="manufacturer",
        object_id=manufacturer.id,
        after=manufacturer_payload(manufacturer),
    )
    return manufacturer


def change_manufacturer(
    ctx: CommandContext, manufacturer_id: UUID, name: str | None, aliases: list[str] | None
) -> Manufacturer:
    manufacturer = Manufacturer.objects.select_for_update().filter(pk=manufacturer_id).first()
    if manufacturer is None:
        raise errors.not_found()
    before = manufacturer_payload(manufacturer)
    if name is not None:
        name = name.strip()
        if not name:
            raise errors.validation_error("name", code="FIELD_REQUIRED")
        if Manufacturer.objects.filter(name__iexact=name).exclude(pk=manufacturer.id).exists():
            raise errors.business_rule("MANUFACTURER_EXISTS", field="name")
        manufacturer.name = name
    if aliases is not None:
        manufacturer.aliases = _aliases(aliases, manufacturer.name)
    manufacturer.save()
    after = manufacturer_payload(manufacturer)
    ctx.audit(
        "warehouse.manufacturer_changed",
        object_type="manufacturer",
        object_id=manufacturer.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return manufacturer


def other_values() -> list[dict[str, Any]]:
    """The «Other» texts entered by companies, with the number of items and companies; the same
    text in other letter case is one group."""
    groups: dict[str, dict[str, Any]] = {}
    companies: dict[str, set[UUID]] = {}
    rows = (
        Item.objects.filter(manufacturer__isnull=True)
        .exclude(manufacturer_other="")
        .values_list("manufacturer_other", "company_id")
    )
    for text, company_id in rows:
        key = text.strip().lower()
        group = groups.setdefault(
            key, {"text": text.strip(), "variants": [], "items": 0, "companies": 0}
        )
        if text not in group["variants"]:
            group["variants"].append(text)
        group["items"] += 1
        companies.setdefault(key, set()).add(company_id)
        group["companies"] = len(companies[key])
    return sorted(groups.values(), key=lambda g: g["text"].lower())


def link_other(
    ctx: CommandContext, texts: list[str], manufacturer_id: UUID
) -> list[dict[str, Any]]:
    """The items with these «Other» texts get the manufacturer from the list; each change is
    journaled in the item's company; the texts become aliases."""
    if not texts:
        raise errors.validation_error("texts", code="FIELD_REQUIRED")
    manufacturer = Manufacturer.objects.select_for_update().filter(pk=manufacturer_id).first()
    if manufacturer is None:
        raise errors.validation_error("manufacturer_id", code="MANUFACTURER_UNKNOWN")
    query = Q()
    for text in texts:
        query |= Q(manufacturer_other__iexact=text.strip())
    changes = []
    for item in Item.objects.select_for_update().filter(query, manufacturer__isnull=True):
        before = item.manufacturer_other
        item.manufacturer = manufacturer
        item.manufacturer_other = ""
        item.version += 1
        item.save(update_fields=["manufacturer", "manufacturer_other", "version", "updated_at"])
        ctx.audit(
            "warehouse.item_changed",
            object_type="warehouse_item",
            object_id=item.id,
            before={"manufacturer": None, "manufacturer_other": before},
            after={"manufacturer": str(manufacturer.id), "manufacturer_other": ""},
            reason="manufacturer_linked_by_platform",
            company_id=item.company_id,
        )
        changes.append({"item": str(item.id), "company": str(item.company_id), "before": before})
    manufacturer.aliases = _aliases([*manufacturer.aliases, *texts], manufacturer.name)
    manufacturer.save(update_fields=["aliases"])
    return changes
