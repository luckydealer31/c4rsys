"""Internal API of block 15: the system dictionaries of the catalog kept by the platform
administrator (ТЗ § 11.1.12–11.1.14) — manufacturers with their aliases and the linking of «Other»
values, and the system attributes of parts with their values.

PROPOSAL: a separate system permission «warehouse.dictionaries», in the starter set of the
platform administrator (§ 11.1.12 and § 11.1.13 name the platform administrator). Every command
has a reason and goes through `run_staff`; a linked item is journaled in its company too.
"""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.backoffice.core import StaffContext, logged_read, require_staff, run_staff
from c4rsys.backoffice.views import REASON_MAX
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandResult
from c4rsys.platform.http import get_str, parse_json
from c4rsys.warehouse import dictionaries
from c4rsys.warehouse.models import Manufacturer

DICTIONARIES = "warehouse.dictionaries"


def _strings(value: Any, field: str) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise errors.validation_error(field)
    return [v[:150] for v in value][:50]


def _names(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise errors.validation_error(field, code="FIELD_REQUIRED")
    return {k: str(v) for k, v in value.items() if k in ("ru", "en", "th")}


def _values(value: Any, field: str) -> list[tuple[str, dict[str, str]]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise errors.validation_error(field)
    result = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise errors.validation_error(f"{field}[{index}]")
        code = get_str(entry, "code", max_length=40)
        result.append((code, _names(entry.get("name"), f"{field}[{index}].name")))
    return result


@require_http_methods(["GET", "POST"])
def manufacturers(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, DICTIONARIES)
    if request.method == "GET":
        found = Manufacturer.objects.order_by("name")
        return JsonResponse(
            {"manufacturers": [dictionaries.manufacturer_payload(m) for m in found]}
        )
    data = parse_json(request)
    name = get_str(data, "name", max_length=150)
    aliases = _strings(data.get("aliases"), "aliases")
    reason = get_str(data, "reason", max_length=REASON_MAX)

    def handler(ctx: StaffContext) -> CommandResult:
        found = dictionaries.add_manufacturer(ctx.ctx, name, aliases)
        ctx.journal(
            "warehouse.manufacturer_added",
            object_type="manufacturer",
            object_id=found.id,
            reason=reason,
            after=dictionaries.manufacturer_payload(found),
        )
        return CommandResult(201, {"manufacturer": dictionaries.manufacturer_payload(found)})

    payload = {"name": name, "aliases": aliases, "reason": reason}
    return run_staff(request, "warehouse.manufacturer_add", staff, payload, handler)


@require_http_methods(["PATCH"])
def manufacturer_detail(request: HttpRequest, manufacturer_id: UUID) -> JsonResponse:
    staff = require_staff(request, DICTIONARIES)
    data = parse_json(request)
    name = get_str(data, "name", max_length=150) if "name" in data else None
    aliases = _strings(data.get("aliases"), "aliases") if "aliases" in data else None
    reason = get_str(data, "reason", max_length=REASON_MAX)

    def handler(ctx: StaffContext) -> CommandResult:
        found = dictionaries.change_manufacturer(ctx.ctx, manufacturer_id, name, aliases)
        ctx.journal(
            "warehouse.manufacturer_changed",
            object_type="manufacturer",
            object_id=found.id,
            reason=reason,
            after=dictionaries.manufacturer_payload(found),
        )
        return CommandResult(200, {"manufacturer": dictionaries.manufacturer_payload(found)})

    payload = {
        "manufacturer_id": str(manufacturer_id),
        "name": name,
        "aliases": aliases,
        "reason": reason,
    }
    return run_staff(request, "warehouse.manufacturer_change", staff, payload, handler)


@require_GET
def manufacturer_other(request: HttpRequest) -> JsonResponse:
    """The «Other» texts of all companies, without the companies' data (§ 11.1.12)."""
    staff = require_staff(request, DICTIONARIES)
    found = dictionaries.other_values()
    logged_read(
        request,
        staff,
        "warehouse.manufacturer_other_viewed",
        object_type="manufacturer",
        object_id="other",
        after={"groups": len(found)},
    )
    return JsonResponse({"other": found})


@require_POST
def manufacturer_link(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, DICTIONARIES)
    data = parse_json(request)
    texts = _strings(data.get("texts"), "texts")
    raw = data.get("manufacturer_id")
    try:
        manufacturer_id = UUID(str(raw))
    except ValueError as exc:
        raise errors.validation_error("manufacturer_id", code="FIELD_REQUIRED") from exc
    reason = get_str(data, "reason", max_length=REASON_MAX)

    def handler(ctx: StaffContext) -> CommandResult:
        changes = dictionaries.link_other(ctx.ctx, texts, manufacturer_id)
        for change in changes:
            ctx.journal(
                "warehouse.manufacturer_linked",
                object_type="warehouse_item",
                object_id=change["item"],
                reason=reason,
                before={"manufacturer_other": change["before"]},
                after={"manufacturer": str(manufacturer_id), "company": change["company"]},
            )
        return CommandResult(200, {"linked": len(changes)})

    payload = {"texts": sorted(texts), "manufacturer_id": str(manufacturer_id), "reason": reason}
    return run_staff(request, "warehouse.manufacturer_link", staff, payload, handler)


@require_http_methods(["GET", "POST"])
def attributes(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, DICTIONARIES)
    if request.method == "GET":
        return JsonResponse(
            {"attributes": [dictionaries.attribute_payload(a) for a in dictionaries.attributes()]}
        )
    data = parse_json(request)
    code = get_str(data, "code", max_length=40)
    names = _names(data.get("name"), "name")
    multiple = data.get("multiple") is True
    values = _values(data.get("values"), "values")
    reason = get_str(data, "reason", max_length=REASON_MAX)

    def handler(ctx: StaffContext) -> CommandResult:
        found = dictionaries.add_attribute(
            ctx.ctx, code=code, names=names, multiple=multiple, values=values
        )
        result = dictionaries.attribute_payload(found)
        ctx.journal(
            "warehouse.attribute_added",
            object_type="part_attribute",
            object_id=found.id,
            reason=reason,
            after=result,
        )
        return CommandResult(201, {"attribute": result})

    payload = {
        "code": code,
        "name": names,
        "multiple": multiple,
        "values": [[c, n] for c, n in values],
        "reason": reason,
    }
    return run_staff(request, "warehouse.attribute_add", staff, payload, handler)


@require_http_methods(["PATCH"])
def attribute_detail(request: HttpRequest, attribute_id: UUID) -> JsonResponse:
    staff = require_staff(request, DICTIONARIES)
    data = parse_json(request)
    names = _names(data.get("name"), "name") if "name" in data else None
    multiple = data.get("multiple") if isinstance(data.get("multiple"), bool) else None
    archived = data.get("archived") if isinstance(data.get("archived"), bool) else None
    add_values = _values(data.get("add_values"), "add_values")
    reason = get_str(data, "reason", max_length=REASON_MAX)

    def handler(ctx: StaffContext) -> CommandResult:
        found = dictionaries.change_attribute(
            ctx.ctx,
            attribute_id,
            names=names,
            multiple=multiple,
            archived=archived,
            add_values=add_values,
        )
        found.refresh_from_db()
        result = dictionaries.attribute_payload(found)
        ctx.journal(
            "warehouse.attribute_changed",
            object_type="part_attribute",
            object_id=found.id,
            reason=reason,
            after=result,
        )
        return CommandResult(200, {"attribute": result})

    payload = {
        "attribute_id": str(attribute_id),
        "name": names,
        "multiple": multiple,
        "archived": archived,
        "add_values": [[c, n] for c, n in add_values],
        "reason": reason,
    }
    return run_staff(request, "warehouse.attribute_change", staff, payload, handler)
