"""HTTP API of the tariff catalog (ТЗ § 13.1–13.2, § 22.1.8, § 23.4.4)."""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.rentals import tariffs
from c4rsys.rentals.models import Tariff

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
