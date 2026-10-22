"""HTTP API of the tariff catalog (ТЗ § 13.1–13.2, § 22.1.8, § 23.4.4)."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, locations
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import services as fleet
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.rentals import issue, rentals, tariffs
from c4rsys.rentals.models import Rental, Tariff

NAME_MAX = 150


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _payload(tariff: Tariff) -> dict[str, Any]:
    return {"id": str(tariff.id), "version": tariff.version, **tariffs.snapshot(tariff)}


def _availability(data: dict[str, Any]) -> tariffs.Availability:
    """`locations`: "all" for the whole company or a non-empty list of location IDs."""
    value = data.get("locations", "all")
    if value == "all":
        return tariffs.Availability(all_locations=True, location_ids=frozenset())
    if not isinstance(value, list) or not value:
        raise errors.validation_error("locations", code="SCOPE_INVALID")
    try:
        ids = frozenset(UUID(str(x)) for x in value)
    except ValueError as exc:
        raise errors.validation_error("locations", code="SCOPE_INVALID") from exc
    return tariffs.Availability(all_locations=False, location_ids=ids)


def _require(actor: Actor, permission: str, availability: tariffs.Availability) -> None:
    if not tariffs.covers(actor, permission, availability):
        raise errors.permission_denied(permission)


def _get_visible(context: companies.CompanyContext, actor: Actor, tariff_id: UUID) -> Tariff:
    tariff = Tariff.objects.filter(company=context.company, pk=tariff_id).first()
    if tariff is None or not tariffs.visible(actor, tariffs.availability_of(tariff)):
        raise errors.not_found()
    return tariff


@require_http_methods(["GET", "POST"])
def tariff_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        items = [
            _payload(t)
            for t in Tariff.objects.filter(company=context.company).order_by("created_at")
            if tariffs.visible(actor, tariffs.availability_of(t))
        ]
        return JsonResponse({"tariffs": items})

    data = parse_json(request)
    availability = _availability(data)
    _require(actor, "tariff.create", availability)
    if data.get("unit", "day") != "day" or data.get("day_mode", "from_issue") != "from_issue":
        # Stage 1: only the daily tariff «24 hours from issue» (STAGE1_PLAN, T07).
        raise errors.validation_error("unit", code="TARIFF_UNIT_NOT_AVAILABLE")
    tariffs.validate_locations(context.company.id, availability.location_ids)
    new = tariffs.NewTariff(
        name=get_str(data, "name", max_length=NAME_MAX),
        currency=get_str(data, "currency", max_length=3, required=False).upper(),
        ranges=tariffs.parse_ranges(data.get("ranges")),
        location_ids=None if availability.all_locations else availability.location_ids,
    )

    def handler(ctx: CommandContext) -> CommandResult:
        tariff = tariffs.create_tariff(ctx, context.company, new)
        return CommandResult(201, {"tariff": _payload(tariff)})

    payload = {
        "name": new.name,
        "currency": new.currency,
        "ranges": [r.as_dict() for r in new.ranges],
        "locations": "all" if new.location_ids is None else sorted(map(str, new.location_ids)),
    }
    return run_and_respond(request, "tariff.create", actor, payload, handler)


@require_http_methods(["GET", "PATCH"])
def tariff_detail(request: HttpRequest, cmp: str, tariff_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    tariff = _get_visible(context, actor, tariff_id)
    if request.method == "GET":
        return JsonResponse({"tariff": _payload(tariff)})

    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    if {"currency", "unit", "day_mode"} & data.keys():
        raise errors.validation_error("currency", code="TARIFF_FIELD_IMMUTABLE")
    new_availability = _availability(data) if "locations" in data else None
    changes = tariffs.TariffChanges(
        name=get_str(data, "name", max_length=NAME_MAX) if "name" in data else None,
        ranges=tariffs.parse_ranges(data["ranges"]) if "ranges" in data else None,
        all_locations=new_availability.all_locations if new_availability else None,
        location_ids=new_availability.location_ids if new_availability else None,
    )
    if changes == tariffs.TariffChanges():
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")
    if new_availability is not None:
        tariffs.validate_locations(context.company.id, new_availability.location_ids)
        _require(actor, "tariff.edit", new_availability)

    def handler(ctx: CommandContext) -> CommandResult:
        locked = tariffs.locked_tariff(context.company.id, tariff_id, expected_version)
        _require(actor, "tariff.edit", tariffs.availability_of(locked))
        return CommandResult(200, {"tariff": _payload(tariffs.change_tariff(ctx, locked, changes))})

    payload: dict[str, Any] = {"tariff_id": str(tariff_id), "expected_version": expected_version}
    if changes.name is not None:
        payload["name"] = changes.name
    if changes.ranges is not None:
        payload["ranges"] = [r.as_dict() for r in changes.ranges]
    if new_availability is not None:
        payload["locations"] = (
            "all"
            if new_availability.all_locations
            else sorted(map(str, new_availability.location_ids))
        )
    return run_and_respond(request, "tariff.change", actor, payload, handler)


def _set_archived(
    request: HttpRequest, cmp: str, tariff_id: UUID, *, archived: bool
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _get_visible(context, actor, tariff_id)
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        locked = tariffs.locked_tariff(context.company.id, tariff_id, expected_version)
        _require(actor, "tariff.archive", tariffs.availability_of(locked))
        tariff = tariffs.set_archived(ctx, locked, archived=archived)
        return CommandResult(200, {"tariff": _payload(tariff)})

    command = "tariff.archive" if archived else "tariff.restore"
    payload = {"tariff_id": str(tariff_id), "expected_version": expected_version}
    return run_and_respond(request, command, actor, payload, handler)


@require_POST
def tariff_archive(request: HttpRequest, cmp: str, tariff_id: UUID) -> JsonResponse:
    return _set_archived(request, cmp, tariff_id, archived=True)


@require_POST
def tariff_restore(request: HttpRequest, cmp: str, tariff_id: UUID) -> JsonResponse:
    return _set_archived(request, cmp, tariff_id, archived=False)


# --- Terms for vehicles (§ 13.1.2, § 13.1.6, § 13.4.1) ----------------------------------------


@require_http_methods(["GET"])
def tariff_vehicles(request: HttpRequest, cmp: str, tariff_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    tariff = _get_visible(context, actor, tariff_id)
    items = [
        tariffs.terms_payload(t)
        for t in tariff.vehicle_terms.select_related("tariff").order_by("created_at")
    ]
    return JsonResponse({"vehicles": items})


@require_http_methods(["PUT"])
def tariff_vehicle_terms(
    request: HttpRequest, cmp: str, tariff_id: UUID, card_id: UUID
) -> JsonResponse:
    """Assigning a tariff to a vehicle is a change of the tariff: it needs tariff.edit
    (decision 22.10.2026)."""
    context, actor = _actor(request, cmp)
    _get_visible(context, actor, tariff_id)
    card = fleet.get_card(context.company.id, card_id)
    terms = tariffs.parse_vehicle_terms(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        locked = Tariff.objects.select_for_update().get(company=context.company, pk=tariff_id)
        _require(actor, "tariff.edit", tariffs.availability_of(locked))
        record = tariffs.set_vehicle_terms(ctx, locked, card, terms)
        return CommandResult(200, {"terms": tariffs.terms_payload(record)})

    payload = {"tariff_id": str(tariff_id), "vehicle_card_id": str(card_id), **terms.as_dict()}
    return run_and_respond(request, "tariff.set_vehicle_terms", actor, payload, handler)


# --- Rentals (§ 14.4) -----------------------------------------------------------------------------


def _moment(data: dict[str, Any], key: str, *, required: bool) -> datetime | None:
    """An ISO 8601 moment with its offset; stored in UTC (§ 4.2.2)."""
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise errors.validation_error(key, code="FIELD_REQUIRED")
        return None
    if not isinstance(value, str):
        raise errors.validation_error(key, code="DATETIME_INVALID")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise errors.validation_error(key, code="DATETIME_INVALID") from exc
    if moment.tzinfo is None:
        raise errors.validation_error(key, code="DATETIME_WITHOUT_OFFSET")
    return moment


def _uuid(data: dict[str, Any], key: str, code: str = "FIELD_REQUIRED") -> UUID:
    value = data.get(key)
    if value in (None, ""):
        raise errors.business_rule(code, field=key)
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _reading(data: dict[str, Any], key: str, code: str, places: int) -> Decimal:
    """Odometer and fuel are required for the issue (§ 14.4.3); fuel is a number from the panel
    without a scale (§ 13.5.1)."""
    value = data.get(key)
    if value in (None, ""):
        raise errors.business_rule(code, field=key)
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise errors.validation_error(key, code="NUMBER_INVALID")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise errors.validation_error(key, code="NUMBER_INVALID") from exc
    exponent = number.as_tuple().exponent
    if not number.is_finite() or number < 0 or (isinstance(exponent, int) and -exponent > places):
        raise errors.validation_error(key, code="NUMBER_INVALID", max_places=places)
    return number


def _utc(moment: datetime | None) -> str | None:
    """Moments are answered in UTC, the same right after a command and on later reads; the
    interface shows them in the location's time zone (§ 4.2.2)."""
    return moment.astimezone(UTC).isoformat() if moment else None


def _rental_payload(rental: Rental) -> dict[str, Any]:
    return {
        "id": str(rental.id),
        "order_id": str(rental.order_id),
        "vehicle_card_id": str(rental.vehicle_card_id),
        "client_id": str(rental.client_id),
        "tariff_id": str(rental.tariff_id),
        "state": rental.state,
        "issue_location_id": str(rental.issue_location_id),
        "issue_place_kind": rental.issue_place_kind,
        "issue_place_note": rental.issue_place_note,
        "calc_timezone": rental.calc_timezone,
        "actual_start": _utc(rental.actual_start),
        "planned_end": _utc(rental.planned_end),
        "actual_end": _utc(rental.actual_end),
        "tariff_snapshot": rental.tariff_snapshot,
        "calculation": rentals.current_calculation(rental),
        "version": rental.version,
    }


@require_POST
def rental_issue(request: HttpRequest, cmp: str) -> JsonResponse:
    """Issue without a booking (§ 14.4.4). Permissions in the scope of the issue location and its
    rental context: rental.issue; marking the inspection as conducted also needs
    inspection.conduct; adding to an existing order needs order.edit (decisions 22.10.2026)."""
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    location_id = _uuid(data, "issue_location_id")
    location = locations.location_info(context.company.id, location_id)
    if location is None:
        raise errors.validation_error("issue_location_id", code="LOCATION_UNKNOWN")
    conducted = data.get("inspection_conducted", False)
    if not isinstance(conducted, bool):
        raise errors.validation_error("inspection_conducted")
    order_id = UUID(str(data["order_id"])) if data.get("order_id") else None
    resolver = access.RightsResolver()
    needed = ["rental.issue"]
    if conducted:
        needed.append("inspection.conduct")
    for permission in needed:
        if not resolver.has_permission(
            actor, permission, location_id=location.id, context_id=location.rental_context_id
        ):
            raise errors.permission_denied(permission)
    if order_id is not None and not resolver.has_permission(
        actor, "order.edit", location_id=location.id
    ):
        raise errors.permission_denied("order.edit")

    place_kind = get_str(data, "place_kind", max_length=10, required=False) or "location"
    if place_kind not in Rental.PlaceKind.values:
        raise errors.validation_error("place_kind")
    place_note = get_str(data, "place_note", max_length=500, required=False)
    if place_kind == Rental.PlaceKind.OTHER and not place_note:
        raise errors.validation_error("place_note", code="FIELD_REQUIRED")
    actual_start = _moment(data, "actual_start", required=True)
    if actual_start is None:
        raise errors.validation_error("actual_start", code="FIELD_REQUIRED")
    request_data = issue.IssueRequest(
        vehicle_card_id=_uuid(data, "vehicle_card_id"),
        client_id=_uuid(data, "client_id", "CLIENT_REQUIRED"),
        tariff_id=_uuid(data, "tariff_id", "TARIFF_REQUIRED"),
        issue_location_id=location.id,
        place_kind=place_kind,
        place_note=place_note if place_kind == Rental.PlaceKind.OTHER else "",
        actual_start=actual_start,
        odometer=_reading(data, "odometer", "ODOMETER_REQUIRED", 1),
        fuel=_reading(data, "fuel", "FUEL_REQUIRED", 2),
        inspection_conducted=conducted,
        planned_end=_moment(data, "planned_end", required=False),
        order_id=order_id,
    )

    def handler(ctx: CommandContext) -> CommandResult:
        rental, inspection, created_order = issue.issue(ctx, request_data)
        return CommandResult(
            201,
            {
                "rental": _rental_payload(rental),
                "inspection_id": str(inspection.id),
                "order_created": created_order,
                # Policies of a new company (§ 14.3.6): client debt and documents only warn.
                "warnings": [],
            },
        )

    payload = {
        k: (str(v) if isinstance(v, UUID | Decimal | datetime) else v)
        for k, v in request_data.__dict__.items()
    }
    return run_and_respond(request, "rental.issue", actor, payload, handler)


def _rental_visible(actor: Actor, rental: Rental) -> bool:
    return access.RightsResolver().has_permission(
        actor, "rental.view", location_id=rental.issue_location_id
    )


@require_http_methods(["GET"])
def rental_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    items = [
        _rental_payload(r)
        for r in rentals.rentals_of(context.company.id)[:100]
        if _rental_visible(actor, r)
    ]
    return JsonResponse({"rentals": items})


@require_http_methods(["GET"])
def rental_detail(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    rental = rentals.get_rental(context.company.id, rental_id)
    if not _rental_visible(actor, rental):
        raise errors.not_found()
    return JsonResponse({"rental": _rental_payload(rental)})
