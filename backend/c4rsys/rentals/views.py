"""HTTP API of the tariff catalog (ТЗ § 13.1–13.2, § 22.1.8, § 23.4.4)."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, history, locations
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.finance import deposits
from c4rsys.fleet import services as fleet
from c4rsys.inspections import services as inspections
from c4rsys.inspections.models import Inspection
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.rentals import (
    bookings,
    formalization,
    issue,
    issue_policies,
    places,
    pricing,
    rentals,
    returns,
    setup,
    tariffs,
)
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


def _optional_uuid(data: dict[str, Any], key: str) -> UUID | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _coordinate(data: dict[str, Any], key: str) -> Decimal | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise errors.validation_error(key, code="NUMBER_INVALID")
    try:
        return Decimal(str(value).strip()).quantize(Decimal("0.000001"))
    except InvalidOperation as exc:
        raise errors.validation_error(key, code="NUMBER_INVALID") from exc


def _place_from(data: dict[str, Any]) -> places.Place:
    """The place of the issue: «location» or «other» with an address, coordinates or a comment
    (§ 14.2.1; customer's decision В1); checked by `Place.checked`."""
    return places.Place(
        get_str(data, "place_kind", max_length=10, required=False) or places.LOCATION,
        get_str(data, "place_note", max_length=500, required=False),
        get_str(data, "place_address", max_length=500, required=False),
        _coordinate(data, "place_lat"),
        _coordinate(data, "place_lng"),
    )


def _exception_request(
    data: dict[str, Any], actor: Actor, location_id: UUID | None
) -> issue_policies.ExceptionRequest:
    """`exception_mode`: «advance» — the issuing employee permits unmet requirements now (needs
    the right to permit, § 23.4.7); «after_fact» — the vehicle has already been handed over."""
    mode = get_str(data, "exception_mode", max_length=10, required=False)
    if mode not in ("", "advance", "after_fact"):
        raise errors.validation_error("exception_mode")
    may = access.RightsResolver().has_permission(
        actor, "rental.exception.authorize", location_id=location_id
    )
    if mode == "advance" and not may:
        raise errors.permission_denied("rental.exception.authorize")
    return issue_policies.ExceptionRequest(
        mode=mode,
        reason=get_str(data, "exception_reason", max_length=500, required=False),
        may_authorize=may,
    )


def _optional_reading(data: dict[str, Any], key: str, places: int) -> Decimal | None:
    if data.get(key) in (None, ""):
        return None
    return _reading(data, key, "FIELD_REQUIRED", places)


def _inspections_payload(rental: Rental) -> dict[str, Any]:
    """Issue and return readings with the fuel comparison of § 13.5.1: the values and whether
    they differ, without converting the difference into money."""
    by_kind = {i.kind: i for i in inspections.of_rental(rental.id)}
    issued, returned = by_kind.get("issue"), by_kind.get("return")

    def view(item: Any) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            "id": str(item.id),
            "state": item.state,
            "check_status": item.check_status,
            "odometer": str(item.odometer_value) if item.odometer_value is not None else None,
            "fuel": str(item.fuel_value) if item.fuel_value is not None else None,
        }

    fuel_differs = None
    if issued and returned and issued.fuel_value is not None and returned.fuel_value is not None:
        fuel_differs = issued.fuel_value != returned.fuel_value
    return {"issue": view(issued), "return": view(returned), "fuel_differs": fuel_differs}


def _rental_payload(rental: Rental) -> dict[str, Any]:
    overdue = (
        rental.state == Rental.State.ACTIVE
        and rental.planned_end is not None
        and rental.planned_end < timezone.now()
    )
    return {
        "id": str(rental.id),
        "order_id": str(rental.order_id),
        "vehicle_card_id": str(rental.vehicle_card_id),
        "client_id": str(rental.client_id) if rental.client_id else None,
        "tariff_id": str(rental.tariff_id) if rental.tariff_id else None,
        "state": rental.state,
        "issue_location_id": str(rental.issue_location_id) if rental.issue_location_id else None,
        "issue_place_kind": rental.issue_place_kind,
        "issue_place_note": rental.issue_place_note,
        "issue_place_address": rental.issue_place_address,
        "issue_place_lat": str(rental.issue_place_lat)
        if rental.issue_place_lat is not None
        else None,
        "issue_place_lng": str(rental.issue_place_lng)
        if rental.issue_place_lng is not None
        else None,
        # § 14.3.7: permitted in advance, or waiting for a decision after the transfer.
        "issue_exception": issue_policies.payload(rental.id),
        "calc_timezone": rental.calc_timezone,
        "planned_return_location_id": str(rental.planned_return_location_id)
        if rental.planned_return_location_id
        else None,
        "return_location_id": str(rental.return_location_id) if rental.return_location_id else None,
        "return_place_kind": rental.return_place_kind or None,
        "return_place_note": rental.return_place_note,
        "overdue": overdue,  # § 14.5.8: the planned end is not moved by a delay
        "inspections": _inspections_payload(rental),
        # § 14.5.6: returned, formalization completed and settlement are separate states.
        "formalization": {
            "completed_at": _utc(rental.formalization_completed_at),
            "missing": formalization.missing_conditions(rental)
            if rental.state == Rental.State.RETURNED and not rental.formalization_completed_at
            else [],
        },
        "actual_start": _utc(rental.actual_start),
        "planned_end": _utc(rental.planned_end),
        "actual_end": _utc(rental.actual_end),
        "tariff_snapshot": rental.tariff_snapshot,
        "calculation": rentals.current_calculation(rental),
        # § 14.7.3: required, received, held and the rest of the deposit; a missing deposit is
        # shown apart from the debt for the rental (§ 14.3.3).
        "deposit": deposits.payload(rental),
        "version": rental.version,
        **_booking_payload(rental),
    }


def _booking_payload(rental: Rental) -> dict[str, Any]:
    """A booking (§ 14.1): own number, planned start, contact, comment, the auto-cancel moment,
    a late return touching it (§ 14.6.2) and a different current location of the vehicle
    (§ 14.2.2)."""
    if rental.booking_number is None:
        return {"booking": None}
    card = fleet.get_card(rental.company_id, rental.vehicle_card_id)
    return {
        "booking": {
            "number": rental.booking_number,
            "planned_start": _utc(rental.planned_start),
            "contact": rental.booking_contact,
            "comment": rental.booking_comment,
            "auto_cancel_at": _utc(rental.auto_cancel_at),
            "auto_cancel_rule": rental.auto_cancel_rule,
            "cancelled_at": _utc(rental.cancelled_at),
            "cancel_reason": rental.cancel_reason,
            "deposit_override": rental.deposit_override,
            "delay_conflict": bookings.delay_conflict(rental),
            "vehicle_location_id": str(card.accounting_location_id)
            if card.accounting_location_id
            else None,
            "client_missing": rental.client_id is None,
        }
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

    place = _place_from(data).checked()
    actual_start = _moment(data, "actual_start", required=True)
    if actual_start is None:
        raise errors.validation_error("actual_start", code="FIELD_REQUIRED")
    request_data = issue.IssueRequest(
        vehicle_card_id=_uuid(data, "vehicle_card_id"),
        client_id=_uuid(data, "client_id", "CLIENT_REQUIRED"),
        tariff_id=_uuid(data, "tariff_id", "TARIFF_REQUIRED"),
        issue_location_id=location.id,
        place_kind=place.kind,
        place_note=place.note,
        place_address=place.address,
        place_lat=place.lat,
        place_lng=place.lng,
        actual_start=actual_start,
        odometer=_reading(data, "odometer", "ODOMETER_REQUIRED", 1),
        fuel=_reading(data, "fuel", "FUEL_REQUIRED", 2),
        inspection_conducted=conducted,
        planned_end=_moment(data, "planned_end", required=False),
        order_id=order_id,
        planned_return_location_id=_optional_uuid(data, "planned_return_location_id"),
        booking_id=_optional_uuid(data, "booking_id"),
        exception=_exception_request(data, actor, location.id),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        done = issue.issue(ctx, request_data)
        return CommandResult(
            201,
            {
                "rental": _rental_payload(done.rental),
                "inspection_id": str(done.inspection.id),
                "order_created": done.order_created,
                # Policies that warn (§ 14.3.5–14.3.6) and an undone required check (§ 10.3.3).
                "warnings": done.warnings,
            },
        )

    payload = {
        k: (str(v) if isinstance(v, UUID | Decimal | datetime) else v)
        for k, v in request_data.__dict__.items()
        if k != "exception"
    }
    payload["exception"] = request_data.exception.__dict__
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


# --- Return (§ 14.5) ----------------------------------------------------------------------


def _require_at(actor: Actor, location_id: UUID, conducted: bool) -> None:
    """Acceptance and later readings need rental.accept_return in the location of acceptance;
    marking the inspection as conducted also needs inspection.conduct (decisions 22.10.2026)."""
    location = locations.location_info(actor.company_id, location_id) if actor.company_id else None
    context_id = location.rental_context_id if location else None
    resolver = access.RightsResolver()
    needed = ["rental.accept_return"] + (["inspection.conduct"] if conducted else [])
    for permission in needed:
        if not resolver.has_permission(
            actor, permission, location_id=location_id, context_id=context_id
        ):
            raise errors.permission_denied(permission)


def _flag(data: dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise errors.validation_error(key)
    return value


@require_POST
def rental_return(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    rental = rentals.get_rental(context.company.id, rental_id)
    data = parse_json(request)
    place_kind = get_str(data, "place_kind", max_length=10, required=False) or "location"
    if place_kind not in Rental.PlaceKind.values:
        raise errors.validation_error("place_kind")
    place_note = get_str(data, "place_note", max_length=500, required=False)
    if place_kind == Rental.PlaceKind.OTHER and not place_note:
        raise errors.validation_error("place_note", code="FIELD_REQUIRED")
    location_id = _uuid(data, "return_location_id")
    returns.acceptance_location(rental, place_kind, location_id)
    conducted = _flag(data, "inspection_conducted")
    odometer = _optional_reading(data, "odometer", 1)
    fuel = _optional_reading(data, "fuel", 2)
    _require_at(actor, location_id, conducted and odometer is not None and fuel is not None)
    actual_end = _moment(data, "actual_end", required=True)
    if actual_end is None:
        raise errors.validation_error("actual_end", code="FIELD_REQUIRED")
    request_data = returns.ReturnRequest(
        rental_id=rental.id,
        actual_end=actual_end,
        return_location_id=location_id,
        place_kind=place_kind,
        place_note=place_note if place_kind == Rental.PlaceKind.OTHER else "",
        odometer=odometer,
        fuel=fuel,
        inspection_conducted=conducted,
        expected_version=get_int(data, "expected_version"),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        returned, inspection = returns.accept_return(ctx, request_data)
        return CommandResult(
            200,
            {
                "rental": _rental_payload(returned),
                "inspection_id": str(inspection.id),
                "inspection_state": inspection.state,
            },
        )

    payload = {
        k: (str(v) if isinstance(v, UUID | Decimal | datetime) else v)
        for k, v in request_data.__dict__.items()
    }
    return run_and_respond(request, "rental.accept_return", actor, payload, handler)


@require_POST
def inspection_save(request: HttpRequest, cmp: str, inspection_id: UUID) -> JsonResponse:
    """Readings of a return entered later into the draft (§ 14.5.5)."""
    context, actor = _actor(request, cmp)
    inspection = Inspection.objects.filter(company=context.company, pk=inspection_id).first()
    if inspection is None or inspection.rental_id is None:
        raise errors.not_found()
    rental = rentals.get_rental(context.company.id, inspection.rental_id)
    data = parse_json(request)
    conducted = _flag(data, "inspection_conducted")
    location_id = rental.return_location_id or rental.issue_location_id
    if location_id is None:  # inspections belong to issued rentals
        raise errors.not_found()
    _require_at(actor, location_id, conducted)
    odometer = _reading(data, "odometer", "ODOMETER_REQUIRED", 1)
    fuel = _reading(data, "fuel", "FUEL_REQUIRED", 2)

    def handler(ctx: CommandContext) -> CommandResult:
        saved = returns.save_return_readings(
            ctx, rental, inspection, odometer=odometer, fuel=fuel, conducted=conducted
        )
        refreshed = rentals.get_rental(context.company.id, rental.id)
        return CommandResult(
            200, {"inspection_id": str(saved.id), "rental": _rental_payload(refreshed)}
        )

    payload = {
        "inspection_id": str(inspection_id),
        "odometer": str(odometer),
        "fuel": str(fuel),
        "inspection_conducted": conducted,
    }
    return run_and_respond(request, "inspection.save", actor, payload, handler)


@require_POST
def rental_complete_formalization(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    """§ 14.5.6–14.5.7. rental.complete_formalization in the issue or the acceptance location
    (decision 22.10.2026)."""
    context, actor = _actor(request, cmp)
    rental = rentals.get_rental(context.company.id, rental_id)
    resolver = access.RightsResolver()
    places = {rental.issue_location_id, rental.return_location_id} - {None}
    if not any(
        resolver.has_permission(actor, "rental.complete_formalization", location_id=x)
        for x in places
    ):
        raise errors.permission_denied("rental.complete_formalization")
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        done = formalization.complete(ctx, context.company.id, rental_id, expected_version)
        return CommandResult(200, {"rental": _rental_payload(done)})

    payload = {"rental_id": str(rental_id), "expected_version": expected_version}
    return run_and_respond(request, "rental.complete_formalization", actor, payload, handler)


@require_http_methods(["GET"])
def rental_history(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    """History tab of the rental: time, author, action, before / after (§ 23.10, § 24.4.5)."""
    context, actor = _actor(request, cmp)
    rental = rentals.get_rental(context.company.id, rental_id)
    if not _rental_visible(actor, rental):
        raise errors.not_found()
    events = history.object_history(context.company.id, rentals.history_objects(rental))
    return JsonResponse({"history": history.payload(events)})


@require_http_methods(["GET"])
def company_readiness(request: HttpRequest, cmp: str) -> JsonResponse:
    """Final check of the initial setup (§ 22.1.12), for those who manage company settings."""
    context, actor = _actor(request, cmp)
    if not access.RightsResolver().has_permission(actor, "company.settings"):
        raise errors.permission_denied("company.settings")
    return JsonResponse(setup.readiness(context.company))


@require_POST
def company_setup_complete(request: HttpRequest, cmp: str) -> JsonResponse:
    """«Complete» of the setup wizard: allowed when the final check shows at least one ready
    context (§ 22.1.12); from then «Orders» are shown (customer's decision of 24.10.2026)."""
    context, actor = _actor(request, cmp)
    if not access.RightsResolver().has_permission(actor, "company.settings"):
        raise errors.permission_denied("company.settings")

    def handler(ctx: CommandContext) -> CommandResult:
        if not setup.readiness(context.company)["first_operation_ready"]:
            raise errors.business_rule("SETUP_NOT_READY")
        done = companies.complete_setup(ctx, context.company.id)
        completed = done.setup_completed_at
        return CommandResult(
            200, {"setup_completed_at": completed.isoformat() if completed else None}
        )

    return run_and_respond(request, "company.complete_setup", actor, {}, handler)


@require_POST
def rental_quote(request: HttpRequest, cmp: str) -> JsonResponse:
    """Preliminary calculation before the issue is confirmed (§ 13.6.6, STAGE1_SCREENS): the
    current tariff of the vehicle, the planned period. A read: nothing is posted."""
    context, actor = _actor(request, cmp)
    resolver = access.RightsResolver()
    if not any(resolver.has_permission(actor, p) for p in ("rental.issue", "rental.view")):
        raise errors.permission_denied("rental.issue")
    data = parse_json(request)
    card = fleet.get_card(context.company.id, _uuid(data, "vehicle_card_id"))
    tariff = (
        tariffs.tariffs_for_vehicle(card)
        .filter(pk=_uuid(data, "tariff_id", "TARIFF_REQUIRED"))
        .first()
    )
    terms = tariffs.vehicle_terms(tariff, card) if tariff else None
    if tariff is None or terms is None:
        raise errors.business_rule("TARIFF_NOT_AVAILABLE", field="tariff_id")
    start = _moment(data, "actual_start", required=True)
    end = _moment(data, "planned_end", required=True)
    if start is None or end is None or end < start:
        raise errors.validation_error("planned_end", code="PERIOD_INVALID")
    tariff = Tariff.objects.select_related("currency").get(pk=tariff.pk)
    snapshot = tariffs.rental_snapshot(tariff, terms)
    result = pricing.calculate(pricing.terms_from_snapshot(snapshot), start, end)
    location_id = _optional_uuid(data, "issue_location_id") or card.accounting_location_id
    booking_id = _optional_uuid(data, "booking_id")
    order_id = _optional_uuid(data, "order_id")
    if booking_id is not None:
        order_id = rentals.get_rental(context.company.id, booking_id).order_id
    assessment = issue_policies.assess(
        context.company.id,
        location_id,
        client_id=_optional_uuid(data, "client_id"),
        order_id=order_id,
        snapshot=snapshot,
        start=start,
        planned_end=end,
    )
    granted = issue_policies.exception_of(booking_id) if booking_id else None
    return JsonResponse(
        {
            "calculation": {
                "kind": "preliminary",
                "billable_minutes": int(result.billable.total_seconds() // 60),
                "units": result.units,
                "rate": str(result.rate) if result.rate is not None else None,
                "base_amount": str(result.base_amount),
                "currency": result.currency,
            },
            "vehicle_terms": snapshot["vehicle_terms"],
            "warnings": assessment.warnings,
            # Strict requirements left unmet (§ 14.3.7) and how the employee may pass them.
            "unmet": assessment.unmet,
            "inspection_required": assessment.inspection_required,
            "can_authorize": access.RightsResolver().has_permission(
                actor, "rental.exception.authorize", location_id=location_id
            ),
            "granted": issue_policies.payload(granted.rental_id)
            if granted is not None and granted.state == "granted"
            else None,
        }
    )


@require_http_methods(["GET"])
def vehicle_tariffs(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """Tariffs to choose from when issuing this vehicle (§ 13.1.2)."""
    context, actor = _actor(request, cmp)
    resolver = access.RightsResolver()
    if not any(resolver.has_permission(actor, p) for p in ("rental.issue", "tariff.view")):
        raise errors.permission_denied("rental.issue")
    card = fleet.get_card(context.company.id, card_id)
    items = [
        {
            "id": str(t.id),
            "name": t.name,
            "currency": t.currency_id,
            "ranges": tariffs.snapshot(t)["ranges"],
        }
        for t in tariffs.tariffs_for_vehicle(card).order_by("name")
    ]
    return JsonResponse({"tariffs": items})
