"""HTTP API of the company garage (ТЗ § 8.1, § 8.7.7, § 23.5)."""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import services
from c4rsys.fleet.models import VehicleCard
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
    if relation != VehicleCard.Relation.OWN:
        # Stage 1: own vehicles only (STAGE1_PLAN, T09).
        raise errors.validation_error("relation", code="RELATION_NOT_AVAILABLE")
    unit = get_str(data, "odometer_unit", max_length=2)
    if unit not in VehicleCard.OdometerUnit.values:
        raise errors.validation_error("odometer_unit")
    try:
        location_id = UUID(get_str(data, "location_id", max_length=40))
    except ValueError as exc:
        raise errors.validation_error("location_id") from exc
    if not access.RightsResolver().has_permission(actor, "vehicle.create", location_id=location_id):
        raise errors.permission_denied("vehicle.create")
    new = services.NewVehicle(
        name=get_str(data, "name", max_length=NAME_MAX), location_id=location_id, odometer_unit=unit
    )

    def handler(ctx: CommandContext) -> CommandResult:
        card = services.create_vehicle(ctx, context.company, new)
        return CommandResult(
            201, {"vehicle": _payload(services.get_card(context.company.id, card.id))}
        )

    payload = {"name": new.name, "location_id": str(location_id), "odometer_unit": unit}
    return run_and_respond(request, "vehicle.create", actor, payload, handler)


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


def _contexts(value: Any) -> frozenset[str]:
    """Usage context types of the vehicle (§ 8.5.1); an empty list switches all off."""
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise errors.validation_error("contexts")
    types = frozenset(value)
    if types - services.USAGE_CONTEXT_TYPES:
        raise errors.validation_error("contexts", code="CONTEXT_TYPE_INVALID")
    return types
