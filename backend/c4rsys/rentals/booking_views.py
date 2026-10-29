"""HTTP API of bookings (ТЗ § 14.1, § 14.6, § 14.7.3, § 14.8.1). Creating, changing and
cancelling a booking and its auto-cancel moment need «Book» (customer's decision of 26.10.2026);
the issue by a booking is the ordinary issue with `booking_id`; the deposit of a booking needs
«Manage deposits»; the auto-cancel policy is a company setting."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.companies import access
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import services as fleet
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.rentals import bookings, issue_policies, rentals
from c4rsys.rentals.models import RentalPolicy
from c4rsys.rentals.places import Place
from c4rsys.rentals.views import (
    _actor,
    _moment,
    _optional_uuid,
    _place_from,
    _rental_payload,
    _uuid,
)

BOOK = "rental.book"
DEPOSIT = "finance.deposit.manage"
SETTINGS = "company.settings"


def _require(actor: Actor, permission: str, location_id: UUID | None = None) -> None:
    if not access.RightsResolver().has_permission(actor, permission, location_id=location_id):
        raise errors.permission_denied(permission)


def _text(data: dict[str, Any], key: str, limit: int) -> str | None:
    if key not in data:
        return None
    return get_str(data, key, max_length=limit, required=False)


def _result(company_id: UUID, rental_id: UUID, status: int = 200) -> CommandResult:
    return CommandResult(
        status, {"rental": _rental_payload(rentals.get_rental(company_id, rental_id))}
    )


def _encode(data: Any) -> dict[str, Any]:
    def value(v: Any) -> Any:
        if isinstance(v, Place):
            return v.as_dict()
        return str(v) if isinstance(v, UUID | Decimal | datetime) else v

    return {k: value(v) for k, v in data.__dict__.items()}


@require_POST
def booking_create(request: HttpRequest, cmp: str) -> JsonResponse:
    """A booking of a concrete vehicle; without a client, tariff or location it is saved with
    «client not specified» (§ 14.1.8–14.1.9)."""
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    location_id = _optional_uuid(data, "issue_location_id")
    _require(actor, BOOK, location_id)
    start = _moment(data, "planned_start", required=True)
    end = _moment(data, "planned_end", required=True)
    if start is None or end is None:
        raise errors.validation_error("planned_start", code="FIELD_REQUIRED")
    new = bookings.BookingData(
        vehicle_card_id=_uuid(data, "vehicle_card_id"),
        planned_start=start,
        planned_end=end,
        client_id=_optional_uuid(data, "client_id"),
        tariff_id=_optional_uuid(data, "tariff_id"),
        issue_location_id=location_id,
        place=_place_from(data),
        planned_return_location_id=_optional_uuid(data, "planned_return_location_id"),
        contact=get_str(data, "contact", max_length=200, required=False),
        comment=get_str(data, "comment", max_length=1000, required=False),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        rental = bookings.create(ctx, context.company.id, new)
        return _result(context.company.id, rental.id, 201)

    return run_and_respond(request, "rental.book", actor, _encode(new), handler)


@require_http_methods(["PATCH"])
def booking_update(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, BOOK)
    data = parse_json(request)
    changes = bookings.BookingChanges(
        planned_start=_moment(data, "planned_start", required=False),
        planned_end=_moment(data, "planned_end", required=False),
        client_id=_optional_uuid(data, "client_id"),
        tariff_id=_optional_uuid(data, "tariff_id"),
        issue_location_id=_optional_uuid(data, "issue_location_id"),
        place=_place_from(data) if "place_kind" in data else None,
        planned_return_location_id=_optional_uuid(data, "planned_return_location_id"),
        contact=_text(data, "contact", 200),
        comment=_text(data, "comment", 1000),
    )
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        bookings.update(ctx, context.company.id, rental_id, changes, expected_version)
        return _result(context.company.id, rental_id)

    payload = {
        "rental_id": str(rental_id),
        **_encode(changes),
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.booking_change", actor, payload, handler)


@require_POST
def booking_vehicle(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    """Another vehicle for an upcoming booking (§ 14.1.6)."""
    context, actor = _actor(request, cmp)
    _require(actor, BOOK)
    data = parse_json(request)
    vehicle_id = _uuid(data, "vehicle_card_id")
    tariff_id = _optional_uuid(data, "tariff_id")
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        bookings.replace_vehicle(
            ctx, context.company.id, rental_id, vehicle_id, tariff_id, expected_version
        )
        return _result(context.company.id, rental_id)

    payload = {
        "rental_id": str(rental_id),
        "vehicle_card_id": str(vehicle_id),
        "tariff_id": str(tariff_id) if tariff_id else None,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.booking_vehicle", actor, payload, handler)


@require_POST
def booking_cancel(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, BOOK)
    data = parse_json(request)
    reason = get_str(data, "reason", max_length=500)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        bookings.cancel(ctx, context.company.id, rental_id, reason, expected_version)
        return _result(context.company.id, rental_id)

    payload = {"rental_id": str(rental_id), "reason": reason, "expected_version": expected_version}
    return run_and_respond(request, "rental.booking_cancel", actor, payload, handler)


@require_POST
def booking_auto_cancel(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    """A new auto-cancel moment of this booking, or none (§ 14.8.1)."""
    context, actor = _actor(request, cmp)
    _require(actor, BOOK)
    data = parse_json(request)
    at = _moment(data, "auto_cancel_at", required=False)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        bookings.set_auto_cancel(ctx, context.company.id, rental_id, at, expected_version)
        return _result(context.company.id, rental_id)

    payload = {
        "rental_id": str(rental_id),
        "auto_cancel_at": at.isoformat() if at else None,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.booking_auto_cancel", actor, payload, handler)


@require_POST
def booking_deposit(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    """Changes or cancels the deposit of the booking with a reason (§ 14.7.3)."""
    context, actor = _actor(request, cmp)
    _require(actor, DEPOSIT)
    data = parse_json(request)
    required = data.get("required")
    if not isinstance(required, bool):
        raise errors.validation_error("required", code="CHOICE_REQUIRED")
    raw = data.get("amount")
    amount = None
    if required:
        try:
            amount = Decimal(str(raw))
        except Exception as exc:  # noqa: BLE001 — any malformed amount is one validation error
            raise errors.validation_error("amount", code="AMOUNT_INVALID") from exc
    reason = get_str(data, "reason", max_length=500)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        bookings.set_deposit(
            ctx, context.company.id, rental_id, required, amount, reason, expected_version
        )
        return _result(context.company.id, rental_id)

    payload = {
        "rental_id": str(rental_id),
        "required": required,
        "amount": str(amount) if amount is not None else None,
        "reason": reason,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.booking_deposit", actor, payload, handler)


@require_http_methods(["GET"])
def availability(request: HttpRequest, cmp: str) -> JsonResponse:
    """Operations of a vehicle overlapping a period, for the booking form (§ 14.6)."""
    context, actor = _actor(request, cmp)
    if not any(access.RightsResolver().has_permission(actor, p) for p in (BOOK, "rental.view")):
        raise errors.permission_denied(BOOK)
    query = dict(request.GET.items())
    card_id = _uuid(query, "vehicle_card_id")
    start = _moment(query, "start", required=True)
    end = _moment(query, "end", required=True)
    if start is None or end is None or end <= start:
        raise errors.validation_error("end", code="PERIOD_INVALID")
    fleet.get_card(context.company.id, card_id)  # the vehicle of this company
    found = bookings.conflicts(card_id, start, end, exclude=_optional_uuid(query, "exclude"))
    # § 14.6.3: a delivery of the vehicle is shown, but does not make it taken.
    return JsonResponse({"free": all(c.get("warning") for c in found), "conflicts": found})


def _policy_payload(company_id: UUID, location_id: UUID | None) -> dict[str, Any]:
    stored = (
        RentalPolicy.objects.filter(company_id=company_id, location_id=location_id).first()
        if location_id
        else RentalPolicy.objects.filter(company_id=company_id, location__isnull=True).first()
    )
    return {
        "location_id": str(location_id) if location_id else None,
        "own": stored is not None,
        "enabled": stored.auto_cancel_enabled if stored else False,
        "after_minutes": stored.auto_cancel_after_minutes if stored else None,
        "effective": bookings.policy_for(company_id, location_id),
        "version": stored.version if stored else 0,
    }


@require_http_methods(["GET", "PUT"])
def policy(request: HttpRequest, cmp: str) -> JsonResponse:
    """Auto-cancel at no-show for the company or a rental location (§ 14.8.1)."""
    context, actor = _actor(request, cmp)
    location_id = _optional_uuid(dict(request.GET.items()), "location_id")
    if request.method == "GET":
        return JsonResponse({"policy": _policy_payload(context.company.id, location_id)})
    _require(actor, SETTINGS)
    data = parse_json(request)
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        raise errors.validation_error("enabled", code="CHOICE_REQUIRED")
    after = data.get("after_minutes")
    if after is not None and (isinstance(after, bool) or not isinstance(after, int) or after < 0):
        raise errors.validation_error("after_minutes", code="INTEGER_REQUIRED")

    def handler(ctx: CommandContext) -> CommandResult:
        bookings.set_policy(ctx, context.company.id, location_id, enabled, after)
        return CommandResult(200, {"policy": _policy_payload(context.company.id, location_id)})

    payload = {
        "location_id": str(location_id) if location_id else None,
        "enabled": enabled,
        "after_minutes": after,
    }
    return run_and_respond(request, "rental.policy", actor, payload, handler)


# --- Issue policies and exceptions (§ 14.3.2, § 14.3.5–14.3.7, § 23.4.7) ----------------------

AUTHORIZE = "rental.exception.authorize"
DECIDE = "rental.exception.decide"


@require_http_methods(["GET", "PUT"])
def issue_policy(request: HttpRequest, cmp: str) -> JsonResponse:
    """Policies of the issue for the company or a rental location (customer's decisions В2–В3)."""
    context, actor = _actor(request, cmp)
    location_id = _optional_uuid(dict(request.GET.items()), "location_id")
    if request.method == "GET":
        return JsonResponse(
            {"policy": issue_policies.policy_payload(context.company.id, location_id)}
        )
    _require(actor, SETTINGS)
    data = parse_json(request)
    values = {name: str(data.get(name, "")) for name in issue_policies.FIELDS}

    def handler(ctx: CommandContext) -> CommandResult:
        issue_policies.set_policy(ctx, context.company.id, location_id, values)
        return CommandResult(
            200, {"policy": issue_policies.policy_payload(context.company.id, location_id)}
        )

    payload = {"location_id": str(location_id) if location_id else None, **values}
    return run_and_respond(request, "rental.issue_policy", actor, payload, handler)


@require_POST
def booking_exception(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    """Permits the issue of this booking in advance without the chosen requirements, with a
    reason (§ 14.3.7; customer's decision В6)."""
    context, actor = _actor(request, cmp)
    booking = rentals.get_rental(context.company.id, rental_id)
    _require(actor, AUTHORIZE, booking.issue_location_id)
    data = parse_json(request)
    raw = data.get("requirements")
    if not isinstance(raw, list) or not all(isinstance(r, str) for r in raw):
        raise errors.validation_error("requirements", code="CHOICE_REQUIRED")
    reason = get_str(data, "reason", max_length=500)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        issue_policies.grant(ctx, context.company.id, rental_id, raw, reason, expected_version)
        return _result(context.company.id, rental_id)

    payload = {
        "rental_id": str(rental_id),
        "requirements": raw,
        "reason": reason,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.issue_exception_grant", actor, payload, handler)


@require_POST
def booking_exception_revoke(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    booking = rentals.get_rental(context.company.id, rental_id)
    _require(actor, AUTHORIZE, booking.issue_location_id)
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        issue_policies.revoke(ctx, context.company.id, rental_id, expected_version)
        return _result(context.company.id, rental_id)

    payload = {"rental_id": str(rental_id), "expected_version": expected_version}
    return run_and_respond(request, "rental.issue_exception_revoke", actor, payload, handler)


@require_POST
def exception_decide(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    """Confirms or rejects an exception recorded after the transfer (§ 14.3.7)."""
    context, actor = _actor(request, cmp)
    rental = rentals.get_rental(context.company.id, rental_id)
    _require(actor, DECIDE, rental.issue_location_id)
    data = parse_json(request)
    decision = data.get("decision")
    if decision not in ("confirm", "reject"):
        raise errors.validation_error("decision", code="CHOICE_REQUIRED")
    reason = get_str(data, "reason", max_length=500)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        issue_policies.decide(
            ctx, context.company.id, rental_id, decision == "confirm", reason, expected_version
        )
        return _result(context.company.id, rental_id)

    payload = {
        "rental_id": str(rental_id),
        "decision": decision,
        "reason": reason,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.issue_exception_decide", actor, payload, handler)
