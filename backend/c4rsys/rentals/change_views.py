"""HTTP API of the extension, the change of the tariff and the reopening of a rental (ТЗ
§ 13.6.2–13.6.5, § 14.5.8; customer's decisions П1–П12 of 28.10.2026)."""

from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_POST

from c4rsys.companies.views import run_and_respond
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.rentals import changes, rentals
from c4rsys.rentals.models import Rental
from c4rsys.rentals.views import _actor, _moment, _optional_uuid, _rental_payload


def _respond(rental: Rental) -> CommandResult:
    return CommandResult(200, {"rental": _rental_payload(rental)})


@require_POST
def planned_end(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    changes.require_right(actor, rentals.get_rental(context.company.id, rental_id), changes.EXTEND)
    data = parse_json(request)
    end = _moment(data, "planned_end", required=True)
    if end is None:
        raise errors.validation_error("planned_end", code="FIELD_REQUIRED")
    consent = data.get("client_consent") is True
    comment = get_str(data, "consent_comment", max_length=500, required=False)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        return _respond(
            changes.change_planned_end(
                ctx,
                context.company.id,
                rental_id,
                planned_end=end,
                client_consent=consent,
                consent_comment=comment,
                expected_version=expected_version,
            )
        )

    payload = {
        "rental_id": str(rental_id),
        "planned_end": end.isoformat(),
        "client_consent": consent,
        "consent_comment": comment,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.change_planned_end", actor, payload, handler)


@require_POST
def tariff(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    rental = rentals.get_rental(context.company.id, rental_id)
    changes.require_right(actor, rental, changes.CHANGE_TERMS)
    data = parse_json(request)
    tariff_id = _optional_uuid(data, "tariff_id")
    if tariff_id is None:
        raise errors.validation_error("tariff_id", code="FIELD_REQUIRED")
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        return _respond(
            changes.change_tariff(
                ctx,
                context.company.id,
                rental_id,
                tariff_id=tariff_id,
                expected_version=expected_version,
            )
        )

    payload = {
        "rental_id": str(rental_id),
        "tariff_id": str(tariff_id),
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.change_tariff", actor, payload, handler)


@require_POST
def reopen(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    changes.require_right(actor, rentals.get_rental(context.company.id, rental_id), changes.REOPEN)
    data = parse_json(request)
    reason = get_str(data, "reason", max_length=500)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        return _respond(
            changes.reopen(
                ctx,
                context.company.id,
                rental_id,
                reason=reason,
                expected_version=expected_version,
            )
        )

    payload = {"rental_id": str(rental_id), "reason": reason, "expected_version": expected_version}
    return run_and_respond(request, "rental.reopen", actor, payload, handler)


@require_POST
def correct(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    """A reopened rental's actual period and return place (decision П11): rental.reopen."""
    context, actor = _actor(request, cmp)
    changes.require_right(actor, rentals.get_rental(context.company.id, rental_id), changes.REOPEN)
    data = parse_json(request)
    start = _moment(data, "actual_start", required=True)
    end = _moment(data, "actual_end", required=True)
    location_id = _optional_uuid(data, "return_location_id")
    if start is None or end is None:
        raise errors.validation_error("actual_end", code="FIELD_REQUIRED")
    if location_id is None:
        raise errors.validation_error("return_location_id", code="FIELD_REQUIRED")
    place_kind = get_str(data, "place_kind", max_length=10, required=False) or "location"
    if place_kind not in Rental.PlaceKind.values:
        raise errors.validation_error("place_kind")
    place_note = get_str(data, "place_note", max_length=500, required=False)
    if place_kind == Rental.PlaceKind.OTHER and not place_note:
        raise errors.validation_error("place_note", code="FIELD_REQUIRED")
    if place_kind != Rental.PlaceKind.OTHER:
        place_note = ""
    correction = changes.Correction(
        actual_start=start,
        actual_end=end,
        return_location_id=location_id,
        place_kind=place_kind,
        place_note=place_note,
    )
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        return _respond(
            changes.correct(ctx, context.company.id, rental_id, correction, expected_version)
        )

    payload = {
        "rental_id": str(rental_id),
        "actual_start": start.isoformat(),
        "actual_end": end.isoformat(),
        "return_location_id": str(location_id),
        "place_kind": place_kind,
        "place_note": place_note,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.correct", actor, payload, handler)
