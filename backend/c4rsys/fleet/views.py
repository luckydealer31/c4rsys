"""HTTP API of the company garage (ТЗ § 8.1, § 8.7.7, § 23.5)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, locations
from c4rsys.companies import services as companies
from c4rsys.companies.models import Employment
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import characteristics, moves, owners, services
from c4rsys.fleet.models import Vehicle, VehicleCard, VehicleMake, VehicleModel, VehicleMove
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json

NAME_MAX = 200


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _allowed(actor: Actor, permission: str, card: VehicleCard) -> bool:
    """Vehicle rights apply in the scope of the vehicle's accounting location."""
    return access.RightsResolver().has_permission(
        actor, permission, location_id=card.accounting_location_id
    )


def _payload(card: VehicleCard) -> dict[str, Any]:
    reading = services.current_reading(card.id)
    return {
        "id": str(card.id),
        "vehicle_id": card.vehicle.public_id,
        "name": card.name,
        "relation": card.relation,
        "location_id": str(card.accounting_location_id) if card.accounting_location_id else None,
        "odometer_unit": card.odometer_unit,
        "contexts": sorted(services.allowed_contexts(card)),
        "state": card.state,
        "version": card.version,
        "odometer": None
        if reading is None
        else {
            "value": str(reading.value),
            "unit": reading.unit,
            "measured_at": reading.measured_at.isoformat(),
        },
        "rental_readiness": services.rental_readiness(card),
        "characteristics": characteristics.describe(card.vehicle),
        "owner": owners.describe(card),
        "acquisition": None
        if card.acquisition_cost is None
        else {"cost": str(card.acquisition_cost), "currency": card.acquisition_currency_id},
        "storage": None
        if card.storage_context_id is None
        else {
            "context_id": str(card.storage_context_id),
            "location_id": str(card.accounting_location_id),
            "since": card.stored_at.isoformat() if card.stored_at else None,
        },
    }


@require_http_methods(["GET", "POST"])
def vehicle_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        items = [
            _payload(c)
            for c in services.cards_of(context.company.id)
            if _allowed(actor, "vehicle.view", c)
        ]
        return JsonResponse({"vehicles": items})

    data = parse_json(request)
    relation = get_str(data, "relation", max_length=10, required=False) or "own"
    # Own, managed or client (§ 8.1.6, decisions of 24.10.2026).
    if relation not in VehicleCard.Relation.values:
        raise errors.validation_error("relation")
    owner = owners.parse(data, relation)
    unit = get_str(data, "odometer_unit", max_length=2)
    if unit not in VehicleCard.OdometerUnit.values:
        raise errors.validation_error("odometer_unit")
    location_id: UUID | None = None
    if relation != VehicleCard.Relation.CLIENT:
        try:
            location_id = UUID(get_str(data, "location_id", max_length=40))
        except ValueError as exc:
            raise errors.validation_error("location_id") from exc
    # A client vehicle has no location: the right counts anywhere in the company.
    if not access.RightsResolver().has_permission(actor, "vehicle.create", location_id=location_id):
        raise errors.permission_denied("vehicle.create")
    details = characteristics.parse(data, timezone.localdate().year)
    new = services.NewVehicle(
        name=get_str(data, "name", max_length=NAME_MAX, required=False),
        location_id=location_id,
        odometer_unit=unit,
        details=details,
        relation=relation,
        owner=owner,
    )

    def handler(ctx: CommandContext) -> CommandResult:
        card = services.create_vehicle(ctx, context.company, new)
        return CommandResult(
            201,
            {
                "vehicle": _payload(services.get_card(context.company.id, card.id)),
                "warnings": details.warnings,
            },
        )

    payload = {
        "name": new.name,
        "location_id": str(location_id) if location_id else None,
        "odometer_unit": unit,
        "relation": relation,
        "owner": owner.payload(),
        **details.payload(),
    }
    return run_and_respond(request, "vehicle.create", actor, payload, handler)


@require_http_methods(["GET"])
def catalog_makes(request: HttpRequest) -> JsonResponse:
    """Makes of the catalog for a vehicle type (§ 8.1.11); popular ones are marked."""
    require_user(request)
    vehicle_type = request.GET.get("type", "")
    items = VehicleMake.objects.filter(vehicle_type=vehicle_type).order_by("name")
    return JsonResponse(
        {
            "makes": [
                {
                    "code": m.code,
                    "name": m.name,
                    "name_cyrillic": m.name_cyrillic,
                    "popular": m.popular,
                    "country": m.country,
                }
                for m in items
            ]
        }
    )


@require_http_methods(["GET"])
def catalog_models(request: HttpRequest, make: str) -> JsonResponse:
    require_user(request)
    items = VehicleModel.objects.filter(make_id=make).order_by("name")
    return JsonResponse(
        {
            "models": [
                {
                    "code": m.code,
                    "name": m.name,
                    "name_cyrillic": m.name_cyrillic,
                    "year_from": m.year_from,
                    "year_to": m.year_to,
                }
                for m in items
            ]
        }
    )


@require_http_methods(["GET"])
def catalog_options(request: HttpRequest) -> JsonResponse:
    """Lists of the creation card: classes, types, bodies, fuels, gearboxes, drives."""
    require_user(request)
    return JsonResponse(
        {
            "transport_classes": [
                {"code": c, "available": c in characteristics.AVAILABLE_CLASSES}
                for c in Vehicle.TransportClass.values
            ],
            "vehicle_types": characteristics.VEHICLE_TYPES[Vehicle.TransportClass.GROUND],
            "catalog_types": sorted(
                set(VehicleMake.objects.values_list("vehicle_type", flat=True))
            ),
            "body_types": characteristics.BODY_TYPES,
            "fuel_types": characteristics.FUEL_TYPES,
            "transmissions": characteristics.TRANSMISSIONS,
            "drives": characteristics.DRIVES,
        }
    )


@require_http_methods(["GET", "PATCH"])
def vehicle_detail(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    card = services.get_card(context.company.id, card_id)
    if not _allowed(actor, "vehicle.view", card):
        raise errors.not_found()
    if request.method == "GET":
        return JsonResponse({"vehicle": _payload(card)})

    if not _allowed(actor, "vehicle.edit", card):
        raise errors.permission_denied("vehicle.edit")
    data = parse_json(request)
    if set(data) - {"name", "contexts", "expected_version"}:
        raise errors.validation_error("body", code="VEHICLE_FIELD_NOT_EDITABLE")
    expected_version = get_int(data, "expected_version")
    changes = services.VehicleChanges(
        name=get_str(data, "name", max_length=NAME_MAX) if "name" in data else None,
        contexts=_contexts(data["contexts"]) if "contexts" in data else None,
    )
    if changes == services.VehicleChanges():
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")

    def handler(ctx: CommandContext) -> CommandResult:
        services.update_vehicle(ctx, context.company.id, card_id, changes, expected_version)
        return CommandResult(
            200, {"vehicle": _payload(services.get_card(context.company.id, card_id))}
        )

    payload: dict[str, Any] = {"card_id": str(card_id), "expected_version": expected_version}
    if changes.name is not None:
        payload["name"] = changes.name
    if changes.contexts is not None:
        payload["contexts"] = sorted(changes.contexts)
    return run_and_respond(request, "vehicle.update", actor, payload, handler)


@require_http_methods(["PUT"])
def vehicle_details(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """The whole set of data of the creation card except relation, location and odometer unit;
    the interface sends all fields (decision of 24.10.2026: «Edit» of a vehicle)."""
    context, actor = _actor(request, cmp)
    card = services.get_card(context.company.id, card_id)
    if not _allowed(actor, "vehicle.view", card):
        raise errors.not_found()
    if not _allowed(actor, "vehicle.edit", card):
        raise errors.permission_denied("vehicle.edit")
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    details = characteristics.parse(data, timezone.localdate().year)
    owner = owners.parse(data, card.relation)
    name = get_str(data, "name", max_length=NAME_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        services.update_details(
            ctx, context.company, card_id, details, owner, name, expected_version
        )
        return CommandResult(
            200,
            {
                "vehicle": _payload(services.get_card(context.company.id, card_id)),
                "warnings": details.warnings,
            },
        )

    payload = {
        "card_id": str(card_id),
        "expected_version": expected_version,
        "name": name,
        "owner": owner.payload(),
        **details.payload(),
    }
    return run_and_respond(request, "vehicle.update_details", actor, payload, handler)


@require_POST
def vehicle_to_warehouse(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """Transfer of an own vehicle without operational contexts to a warehouse (§ 8.1.5; decision
    of 24.10.2026). The right counts in the target location."""
    context, actor = _actor(request, cmp)
    card = services.get_card(context.company.id, card_id)
    if not _allowed(actor, "vehicle.view", card):
        raise errors.not_found()
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    try:
        context_id = UUID(get_str(data, "context_id", max_length=40))
    except ValueError as exc:
        raise errors.validation_error("context_id") from exc
    target = locations.storage_context(context.company.id, context_id)
    if target is None:
        raise errors.validation_error("context_id", code="WAREHOUSE_UNKNOWN")
    if not access.RightsResolver().has_permission(
        actor, "vehicle.transfer_to_warehouse", location_id=target.location_id
    ):
        raise errors.permission_denied("vehicle.transfer_to_warehouse")

    def handler(ctx: CommandContext) -> CommandResult:
        services.transfer_to_warehouse(
            ctx, context.company.id, card_id, context_id, expected_version
        )
        return CommandResult(
            200, {"vehicle": _payload(services.get_card(context.company.id, card_id))}
        )

    payload = {
        "card_id": str(card_id),
        "context_id": str(context_id),
        "expected_version": expected_version,
    }
    return run_and_respond(request, "vehicle.transfer_to_warehouse", actor, payload, handler)


@require_POST
def vehicle_to_garage(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """Return of a vehicle from a warehouse to the garage (§ 8.4.1). PROPOSAL: the same special
    right as the transfer to a warehouse, in the location of the warehouse."""
    context, actor = _actor(request, cmp)
    card = services.get_card(context.company.id, card_id)
    if not _allowed(actor, "vehicle.view", card):
        raise errors.not_found()
    if card.storage_context_id is None:
        raise errors.business_rule("VEHICLE_NOT_IN_WAREHOUSE")
    if not access.RightsResolver().has_permission(
        actor, "vehicle.transfer_to_warehouse", location_id=card.accounting_location_id
    ):
        raise errors.permission_denied("vehicle.transfer_to_warehouse")
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        services.return_to_garage(ctx, context.company.id, card_id, expected_version)
        return CommandResult(
            200, {"vehicle": _payload(services.get_card(context.company.id, card_id))}
        )

    payload = {"card_id": str(card_id), "expected_version": expected_version}
    return run_and_respond(request, "vehicle.return_to_garage", actor, payload, handler)


def _contexts(value: Any) -> frozenset[str]:
    """Usage context types of the vehicle (§ 8.5.1); an empty list switches all off."""
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise errors.validation_error("contexts")
    types = frozenset(value)
    if types - services.USAGE_CONTEXT_TYPES:
        raise errors.validation_error("contexts", code="CONTEXT_TYPE_INVALID")
    return types


# --- Internal move recorded after it happened (§ 8.5.2, § 15.1.3–15.1.5, § 23.6.2) -----------


def _moment(data: dict[str, Any], key: str) -> datetime:
    """An ISO 8601 moment with its offset (§ 4.2.2)."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise errors.validation_error(key, code="FIELD_REQUIRED")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise errors.validation_error(key, code="DATETIME_INVALID") from exc
    if moment.tzinfo is None:
        raise errors.validation_error(key, code="DATETIME_WITHOUT_OFFSET")
    return moment


def _uuid(data: dict[str, Any], key: str) -> UUID:
    value = data.get(key)
    if value in (None, ""):
        raise errors.validation_error(key, code="FIELD_REQUIRED")
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _move_payload(move: VehicleMove) -> dict[str, Any]:
    return {
        "id": str(move.id),
        "order_id": str(move.order_id),
        "from_location_id": str(move.from_location_id),
        "to_location_id": str(move.to_location_id),
        "driver": {
            "employment_id": str(move.driver_employment_id),
            "name": _name(move.driver_employment),
        },
        "actual_start": move.actual_start.astimezone(UTC).isoformat(),
        "actual_end": move.actual_end.astimezone(UTC).isoformat(),
        "note": move.note,
    }


def _name(employment: Employment) -> str:
    user = employment.user
    return f"{user.first_name} {user.last_name}".strip() if user else ""


@require_POST
def vehicle_move(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """vehicle.move in the source location; the destination is chosen freely among the company's
    locations without access to it (§ 15.1.4, decision 23.10.2026)."""
    context, actor = _actor(request, cmp)
    card = services.get_card(context.company.id, card_id)
    if not _allowed(actor, "vehicle.view", card):
        raise errors.not_found()
    if not _allowed(actor, "vehicle.move", card):
        raise errors.permission_denied("vehicle.move")
    data = parse_json(request)
    move_request = moves.MoveRequest(
        vehicle_card_id=card.id,
        to_location_id=_uuid(data, "to_location_id"),
        driver_employment_id=_uuid(data, "driver_employment_id"),
        actual_start=_moment(data, "actual_start"),
        actual_end=_moment(data, "actual_end"),
        note=get_str(data, "note", max_length=500, required=False),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        move = moves.record_move(ctx, context.company.id, move_request)
        refreshed = services.get_card(context.company.id, card_id)
        return CommandResult(201, {"move": _move_payload(move), "vehicle": _payload(refreshed)})

    payload = {
        k: (str(v) if isinstance(v, UUID | datetime) else v)
        for k, v in move_request.__dict__.items()
    }
    return run_and_respond(request, "vehicle.move", actor, payload, handler)


@require_http_methods(["GET"])
def vehicle_moves(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    card = services.get_card(context.company.id, card_id)
    if not _allowed(actor, "vehicle.view", card):
        raise errors.not_found()
    items = moves.moves_of(context.company.id, card.id)
    return JsonResponse({"moves": [_move_payload(m) for m in items]})


@require_http_methods(["GET"])
def drivers(request: HttpRequest, cmp: str) -> JsonResponse:
    """Employees who may be recorded as the driver of a move: any active employee of the company
    (§ 15.2.4). Only names, for those who move vehicles."""
    context, actor = _actor(request, cmp)
    if not access.RightsResolver().has_permission(actor, "vehicle.move"):
        raise errors.permission_denied("vehicle.move")
    employments = Employment.objects.filter(
        company=context.company, state=Employment.State.ACTIVE
    ).select_related("user")
    items = sorted(
        ({"employment_id": str(e.id), "name": _name(e)} for e in employments),
        key=lambda x: x["name"],
    )
    return JsonResponse({"drivers": items})


@require_http_methods(["PUT"])
def vehicle_acquisition(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """The cost of acquisition of an own vehicle (§ 21.4.2; decision О-3); empty — unknown."""
    from decimal import Decimal, InvalidOperation

    context, actor = _actor(request, cmp)
    card = services.get_card(context.company.id, card_id)
    if not _allowed(actor, "vehicle.view", card):
        raise errors.not_found()
    if not _allowed(actor, "vehicle.edit", card):
        raise errors.permission_denied("vehicle.edit")
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    raw = data.get("cost")
    cost = None
    currency = None
    if raw not in (None, ""):
        try:
            cost = Decimal(str(raw))
        except InvalidOperation as exc:
            raise errors.validation_error("cost", code="AMOUNT_INVALID") from exc
        currency = get_str(data, "currency", max_length=3).upper()

    def handler(ctx: CommandContext) -> CommandResult:
        services.set_acquisition(ctx, context.company.id, card_id, cost, currency, expected_version)
        return CommandResult(
            200, {"vehicle": _payload(services.get_card(context.company.id, card_id))}
        )

    payload = {
        "card_id": str(card_id),
        "cost": str(cost) if cost is not None else None,
        "currency": currency,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "vehicle.acquisition", actor, payload, handler)


# Kinds on the schedule and the right that shows each of them in the vehicle's location.
SCHEDULE_RIGHTS = {
    "rental": "rental.view",
    "service": "service.view",
    "delivery": "delivery.view",
}
SCHEDULE_MAX_DAYS = 62


@require_http_methods(["GET"])
def vehicle_schedule(request: HttpRequest, cmp: str) -> JsonResponse:
    """The schedule of vehicles (decision Б3, В-4): own and managed vehicles in the garage with
    their bookings, rentals, service and moves over [from, to), optionally of one location.
    Vehicles by the right to view them; each kind only with its own right in the vehicle's
    location."""
    context, actor = _actor(request, cmp)

    def moment(key: str) -> datetime:
        try:
            value = datetime.fromisoformat(request.GET.get(key, ""))
        except ValueError as exc:
            raise errors.validation_error(key, code="DATETIME_INVALID") from exc
        if value.tzinfo is None:
            raise errors.validation_error(key, code="DATETIME_WITHOUT_OFFSET")
        return value

    start, end = moment("from"), moment("to")
    if end <= start or (end - start).days > SCHEDULE_MAX_DAYS:
        raise errors.validation_error("to", code="PERIOD_INVALID")
    location = request.GET.get("location", "")
    cards = [
        c
        for c in services.cards_of(context.company.id).filter(
            relation__in=(VehicleCard.Relation.OWN, VehicleCard.Relation.MANAGED),
            state=VehicleCard.State.ACTIVE,
            storage_context__isnull=True,
        )
        if _allowed(actor, "vehicle.view", c)
        and (not location or str(c.accounting_location_id) == location)
    ]
    by_card = {str(c.id): c for c in cards}
    kinds = set(SCHEDULE_RIGHTS)
    items = [
        item
        for item in services.schedule(context.company.id, [c.id for c in cards], start, end, kinds)
        if _allowed(
            actor,
            SCHEDULE_RIGHTS["rental" if item["kind"] == "booking" else item["kind"]],
            by_card[item["card_id"]],
        )
    ]
    return JsonResponse(
        {
            "vehicles": [
                {
                    "id": str(c.id),
                    "name": c.name,
                    "location_id": str(c.accounting_location_id)
                    if c.accounting_location_id
                    else None,
                }
                for c in cards
            ],
            "items": items,
        }
    )
