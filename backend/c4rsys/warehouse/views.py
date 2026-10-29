"""HTTP API of the warehouse catalog and storage (ТЗ § 11.1, § 23.7.3)."""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, locations
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.warehouse import catalog
from c4rsys.warehouse import categories as part_catalog
from c4rsys.warehouse.models import Item, Manufacturer

NAME_MAX = 200
LIST_LIMIT = 200


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _require(actor: Actor, permission: str) -> None:
    """The catalog is shared by the company (§ 11.1.1): the right counts anywhere."""
    if not access.RightsResolver().has_permission(actor, permission):
        raise errors.permission_denied(permission)


def _uuid(value: Any, field: str) -> UUID | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise errors.validation_error(field)
    try:
        return UUID(value)
    except ValueError as exc:
        raise errors.validation_error(field) from exc


def _item_payload(item: Item) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "version": item.version,
        **catalog.snapshot(item),
        "possible_duplicate": item.possible_duplicate,
        "import_operation": str(item.import_operation_id) if item.import_operation_id else None,
        "manufacturer_name": item.manufacturer.name
        if item.manufacturer
        else item.manufacturer_other,
        "applicability": [
            {"make": a.make_id, "model": a.model_id} for a in item.applicability.all()
        ],
        "packagings": [
            {"id": str(p.id), "name": p.name, "quantity": str(p.quantity), "state": p.state}
            for p in sorted(item.packagings.all(), key=lambda p: p.quantity)
        ],
        "barcodes": [
            {
                "id": str(b.id),
                "code": b.code,
                "packaging_id": str(b.packaging_id) if b.packaging_id else None,
            }
            for b in item.barcodes.all()
        ],
    }


def _item_result(status: int, company_id: UUID, item_id: UUID) -> CommandResult:
    return CommandResult(status, {"item": _item_payload(catalog.get_item(company_id, item_id))})


def _item_data(data: dict[str, Any]) -> catalog.ItemData:
    applicability = data.get("applicability", [])
    if not isinstance(applicability, list):
        raise errors.validation_error("applicability")
    pairs = []
    for entry in applicability:
        if not isinstance(entry, dict):
            raise errors.validation_error("applicability")
        make = get_str(entry, "make", max_length=60)
        model = get_str(entry, "model", max_length=120, required=False) or None
        pairs.append((make, model))
    return catalog.ItemData(
        name=get_str(data, "name", max_length=NAME_MAX),
        manufacturer_id=_uuid(data.get("manufacturer"), "manufacturer"),
        manufacturer_other=get_str(data, "manufacturer_other", max_length=150, required=False),
        attributes={
            key: get_str(data, key, max_length=10, required=False) for key in catalog.ATTRIBUTES
        },
        texts={
            key: get_str(data, key, max_length=length, required=False)
            for key, length in catalog.TEXT_FIELDS.items()
        },
        photo_id=_uuid(data.get("photo"), "photo"),
        part_category_id=_uuid(data.get("part_category"), "part_category"),
        part_subcategory_id=_uuid(data.get("part_subcategory"), "part_subcategory"),
        applicability=tuple(pairs),
    )


def _data_payload(data: catalog.ItemData) -> dict[str, Any]:
    return {
        "name": data.name,
        "manufacturer": str(data.manufacturer_id) if data.manufacturer_id else None,
        "manufacturer_other": data.manufacturer_other,
        "attributes": data.attributes,
        "texts": data.texts,
        "photo": str(data.photo_id) if data.photo_id else None,
        "part_category": str(data.part_category_id) if data.part_category_id else None,
        "part_subcategory": str(data.part_subcategory_id) if data.part_subcategory_id else None,
        "applicability": [list(p) for p in data.applicability],
    }


@require_http_methods(["GET", "POST"])
def item_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require(actor, "warehouse.view")
        state = request.GET.get("state", Item.State.ACTIVE)
        if state not in ("", *Item.State.values):
            raise errors.validation_error("state")
        found = catalog.search(context.company.id, request.GET.get("q", "")[:100], state)
        return JsonResponse({"items": [_item_payload(i) for i in found[:LIST_LIMIT]]})

    _require(actor, "warehouse.catalog.manage")
    data = parse_json(request)
    unit = get_str(data, "unit", max_length=20)
    item_data = _item_data(data)

    def handler(ctx: CommandContext) -> CommandResult:
        item = catalog.create_item(ctx, context.company, unit, item_data)
        return _item_result(201, context.company.id, item.id)

    payload = {"unit": unit, **_data_payload(item_data)}
    return run_and_respond(request, "warehouse.create_item", actor, payload, handler)


@require_http_methods(["GET", "PATCH", "DELETE"])
def item_detail(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require(actor, "warehouse.view")
        return JsonResponse({"item": _item_payload(catalog.get_item(context.company.id, item_id))})

    _require(actor, "warehouse.catalog.manage")
    catalog.get_item(context.company.id, item_id)
    if request.method == "DELETE":

        def remove(ctx: CommandContext) -> CommandResult:
            catalog.delete_item(ctx, context.company.id, item_id)
            return CommandResult(200, {"status": "deleted"})

        return run_and_respond(
            request, "warehouse.delete_item", actor, {"item_id": str(item_id)}, remove
        )

    data = parse_json(request)
    if "unit" in data:
        # § 11.1.11, § 11.8.5: a wrong unit is fixed by a new item.
        raise errors.validation_error("unit", code="ITEM_UNIT_IMMUTABLE")
    expected_version = get_int(data, "expected_version")
    item_data = _item_data(data)

    def handler(ctx: CommandContext) -> CommandResult:
        catalog.update_item(ctx, context.company.id, item_id, item_data, expected_version)
        return _item_result(200, context.company.id, item_id)

    payload = {
        "item_id": str(item_id),
        "expected_version": expected_version,
        **_data_payload(item_data),
    }
    return run_and_respond(request, "warehouse.update_item", actor, payload, handler)


def _archive_command(request: HttpRequest, cmp: str, item_id: UUID, archived: bool) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.catalog.manage")
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        catalog.set_archived(ctx, context.company.id, item_id, archived, expected_version)
        return _item_result(200, context.company.id, item_id)

    payload = {"item_id": str(item_id), "expected_version": expected_version}
    name = "warehouse.archive_item" if archived else "warehouse.restore_item"
    return run_and_respond(request, name, actor, payload, handler)


@require_POST
def item_archive(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    return _archive_command(request, cmp, item_id, archived=True)


@require_POST
def item_restore(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    return _archive_command(request, cmp, item_id, archived=False)


@require_POST
def packaging_add(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.catalog.manage")
    data = parse_json(request)
    amount = catalog.quantity(data.get("quantity"), "quantity")
    name = get_str(data, "name", max_length=100, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        catalog.add_packaging(ctx, context.company.id, item_id, amount, name)
        return _item_result(201, context.company.id, item_id)

    payload = {"item_id": str(item_id), "quantity": str(amount), "name": name}
    return run_and_respond(request, "warehouse.add_packaging", actor, payload, handler)


@require_POST
def packaging_archive(
    request: HttpRequest, cmp: str, item_id: UUID, packaging_id: UUID
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.catalog.manage")

    def handler(ctx: CommandContext) -> CommandResult:
        catalog.archive_packaging(ctx, context.company.id, item_id, packaging_id)
        return _item_result(200, context.company.id, item_id)

    payload = {"item_id": str(item_id), "packaging_id": str(packaging_id)}
    return run_and_respond(request, "warehouse.archive_packaging", actor, payload, handler)


@require_POST
def barcode_add(request: HttpRequest, cmp: str, item_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.catalog.manage")
    data = parse_json(request)
    code = get_str(data, "code", max_length=64)
    packaging_id = _uuid(data.get("packaging_id"), "packaging_id")

    def handler(ctx: CommandContext) -> CommandResult:
        catalog.add_barcode(ctx, context.company.id, item_id, code, packaging_id)
        return _item_result(201, context.company.id, item_id)

    payload = {"item_id": str(item_id), "code": code, "packaging_id": str(packaging_id or "")}
    return run_and_respond(request, "warehouse.add_barcode", actor, payload, handler)


@require_http_methods(["DELETE"])
def barcode_remove(request: HttpRequest, cmp: str, item_id: UUID, barcode_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.catalog.manage")

    def handler(ctx: CommandContext) -> CommandResult:
        catalog.remove_barcode(ctx, context.company.id, item_id, barcode_id)
        return _item_result(200, context.company.id, item_id)

    payload = {"item_id": str(item_id), "barcode_id": str(barcode_id)}
    return run_and_respond(request, "warehouse.remove_barcode", actor, payload, handler)


@require_http_methods(["GET"])
def barcode_lookup(request: HttpRequest, cmp: str, code: str) -> JsonResponse:
    """A scanned barcode gives the item and the packaging (§ 11.1.16)."""
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    barcode = catalog.find_by_barcode(context.company.id, code)
    if barcode is None:
        raise errors.not_found()
    return JsonResponse(
        {
            "item": _item_payload(catalog.get_item(context.company.id, barcode.item_id)),
            "packaging_id": str(barcode.packaging_id) if barcode.packaging_id else None,
        }
    )


@require_http_methods(["GET"])
def storages(request: HttpRequest, cmp: str) -> JsonResponse:
    """Warehouses of locations the employee may see (§ 11.1.1, § 23.7.3): stock is kept apart by
    them. Stock itself comes with receipts."""
    context, actor = _actor(request, cmp)
    resolver = access.RightsResolver()
    items = [
        {
            "context_id": str(c.id),
            "type": c.type,
            "state": c.state,
            "location_id": str(c.location_id),
            "location_name": c.location.name,
            "location_deactivated": c.location.deactivated_at is not None,
        }
        for c in locations.storage_contexts(context.company.id)
        if resolver.has_permission(
            actor, "warehouse.view", location_id=c.location_id, context_id=c.id
        )
    ]
    return JsonResponse({"storages": items})


@require_http_methods(["GET"])
def manufacturers(request: HttpRequest) -> JsonResponse:
    """System catalog of manufacturers (§ 11.1.12), searched by name and aliases."""
    require_user(request)
    text = request.GET.get("q", "").strip()[:100]
    needle = text.lower()
    found = [
        m
        for m in Manufacturer.objects.order_by("name")
        if not needle
        or needle in m.name.lower()
        or any(needle in alias.lower() for alias in m.aliases)
    ]
    return JsonResponse(
        {"manufacturers": [{"id": str(m.id), "name": m.name} for m in found[:LIST_LIMIT]]}
    )


@require_http_methods(["GET"])
def categories(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "warehouse.view")
    return JsonResponse({"categories": catalog.categories(context.company.id)})


@require_http_methods(["GET"])
def options(request: HttpRequest) -> JsonResponse:
    """Units and attribute values of the catalog."""
    require_user(request)
    return JsonResponse(
        {
            "units": [
                {"code": u, "accounting": catalog.accounting_for(u)} for u in Item.Unit.values
            ],
            "attributes": catalog.ATTRIBUTES,
        }
    )


@require_http_methods(["GET"])
def part_categories(request: HttpRequest) -> JsonResponse:
    """The platform catalog of categories and subcategories of parts (decision К1)."""
    require_user(request)
    return JsonResponse({"categories": part_catalog.tree_payload()})


@require_http_methods(["GET"])
def part_category_suggest(request: HttpRequest) -> JsonResponse:
    """Categories for a name being typed: the first is applied, the others are offered (К2, К3)."""
    require_user(request)
    name = request.GET.get("name", "")[:200]
    return JsonResponse(
        {
            "suggestions": [
                {
                    "category": str(c.category_id),
                    "subcategory": str(c.subcategory_id),
                    "matched": c.matched,
                }
                for c in part_catalog.suggest(name)
            ]
        }
    )
