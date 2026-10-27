"""HTTP API of additional rental services: the catalog and the lines in rentals and orders
(ТЗ § 13.4.5–13.4.15, § 23.4.4; customer's decisions Т8, У1–У12 of 27.10.2026).

Rights: the catalog has the tariff rights (§ 23.4.4); a linked service — the right of the issue
in the rental's location; a standalone one — «Edit orders» in the location of the service, a new
order of only a service — «Create orders» there (decision У11).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.companies import access
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.rentals import extra_lines, extras, rentals, tariffs
from c4rsys.rentals.charge_views import _encoded, _interval
from c4rsys.rentals.models import ExtraService, ExtraServiceLine
from c4rsys.rentals.views import NAME_MAX, _actor, _availability, _moment, _optional_uuid, _uuid

# --- Catalog --------------------------------------------------------------------------------------


def _service_payload(service: ExtraService) -> dict[str, Any]:
    return {"id": str(service.id), "version": service.version, **extras.snapshot(service)}


def _ids(data: dict[str, Any], key: str) -> frozenset[UUID]:
    value = data.get(key) or []
    if not isinstance(value, list):
        raise errors.validation_error(key, code="SCOPE_INVALID")
    try:
        return frozenset(UUID(str(x)) for x in value)
    except ValueError as exc:
        raise errors.validation_error(key, code="SCOPE_INVALID") from exc


def _scope(data: dict[str, Any]) -> tuple[extras.Scope, tariffs.Availability]:
    availability = _availability(data)
    scope = extras.Scope(
        location_ids=None if availability.all_locations else availability.location_ids,
        vehicle_ids=_ids(data, "vehicles"),
        tariff_ids=_ids(data, "tariffs"),
    )
    return scope, availability


def _require(actor: Actor, permission: str, availability: tariffs.Availability) -> None:
    if not tariffs.covers(actor, permission, availability):
        raise errors.permission_denied(permission)


def _visible_service(context: companies.CompanyContext, actor: Actor, sid: UUID) -> ExtraService:
    service = ExtraService.objects.filter(company=context.company, pk=sid).first()
    if service is None or not tariffs.visible(actor, extras.availability_of(service)):
        raise errors.not_found()
    return service


def _scope_payload(scope: extras.Scope) -> dict[str, Any]:
    return {
        "locations": "all" if scope.location_ids is None else sorted(map(str, scope.location_ids)),
        "vehicles": sorted(map(str, scope.vehicle_ids)),
        "tariffs": sorted(map(str, scope.tariff_ids)),
    }


@require_http_methods(["GET", "POST"])
def service_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        items = [
            _service_payload(s)
            for s in ExtraService.objects.filter(company=context.company).order_by("created_at")
            if tariffs.visible(actor, extras.availability_of(s))
        ]
        return JsonResponse({"services": items})

    data = parse_json(request)
    scope, availability = _scope(data)
    _require(actor, "tariff.create", availability)
    pricing, unit = extras.parse_pricing(data)
    new = extras.NewService(
        name=get_str(data, "name", max_length=NAME_MAX),
        pricing=pricing,
        unit=unit,
        price=extras.parse_price(data.get("price")),
        currency=get_str(data, "currency", max_length=3, required=False).upper(),
        scope=scope,
    )

    def handler(ctx: CommandContext) -> CommandResult:
        service = extras.create(ctx, context.company, new)
        return CommandResult(201, {"service": _service_payload(service)})

    payload = {
        "name": new.name,
        "pricing": pricing,
        "unit": unit,
        "price": str(new.price),
        "currency": new.currency,
        **_scope_payload(scope),
    }
    return run_and_respond(request, "extra_service.create", actor, payload, handler)


@require_http_methods(["GET", "PATCH"])
def service_detail(request: HttpRequest, cmp: str, service_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    service = _visible_service(context, actor, service_id)
    if request.method == "GET":
        return JsonResponse({"service": _service_payload(service)})

    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    if {"pricing", "unit", "currency"} & data.keys():
        raise errors.validation_error("pricing", code="SERVICE_FIELD_IMMUTABLE")
    scope, availability = _scope(data) if "locations" in data else (None, None)
    changes = extras.Changes(
        name=get_str(data, "name", max_length=NAME_MAX) if "name" in data else None,
        price=extras.parse_price(data["price"]) if "price" in data else None,
        scope=scope,
    )
    if changes == extras.Changes():
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")
    if availability is not None:
        _require(actor, "tariff.edit", availability)

    def handler(ctx: CommandContext) -> CommandResult:
        locked = extras.locked(context.company.id, service_id, expected_version)
        _require(actor, "tariff.edit", extras.availability_of(locked))
        return CommandResult(
            200, {"service": _service_payload(extras.change(ctx, locked, changes))}
        )

    payload: dict[str, Any] = {"service_id": str(service_id), "expected_version": expected_version}
    if changes.name is not None:
        payload["name"] = changes.name
    if changes.price is not None:
        payload["price"] = str(changes.price)
    if scope is not None:
        payload.update(_scope_payload(scope))
    return run_and_respond(request, "extra_service.change", actor, payload, handler)


def _set_archived(
    request: HttpRequest, cmp: str, service_id: UUID, *, archived: bool
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _visible_service(context, actor, service_id)
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        locked = extras.locked(context.company.id, service_id, expected_version)
        _require(actor, "tariff.archive", extras.availability_of(locked))
        service = extras.set_archived(ctx, locked, archived=archived)
        return CommandResult(200, {"service": _service_payload(service)})

    command = "extra_service.archive" if archived else "extra_service.restore"
    payload = {"service_id": str(service_id), "expected_version": expected_version}
    return run_and_respond(request, command, actor, payload, handler)


@require_POST
def service_archive(request: HttpRequest, cmp: str, service_id: UUID) -> JsonResponse:
    return _set_archived(request, cmp, service_id, archived=True)


@require_POST
def service_restore(request: HttpRequest, cmp: str, service_id: UUID) -> JsonResponse:
    return _set_archived(request, cmp, service_id, archived=False)


def _choice(service: ExtraService) -> dict[str, Any]:
    return {
        "id": str(service.id),
        "name": service.name,
        "pricing": service.pricing,
        "unit": service.unit or None,
        "price": extras.price_text(service.price),
        "currency": service.currency_id,
    }


@require_GET
def applicable(request: HttpRequest, cmp: str) -> JsonResponse:
    """Services to choose from: for a rental (`rental_id`) or for a standalone service in a
    location (`location_id`), decision У3."""
    context, actor = _actor(request, cmp)
    rental_id = _optional_uuid(dict(request.GET.items()), "rental_id")
    if rental_id is not None:
        rental = rentals.get_rental(context.company.id, rental_id)
        _require_rental(actor, rental)
        found = extras.for_rental(rental)
    else:
        location_id = _uuid(dict(request.GET.items()), "location_id")
        resolver = access.RightsResolver()
        if not any(
            resolver.has_permission(actor, p, location_id=location_id)
            for p in ("order.edit", "order.create")
        ):
            raise errors.permission_denied("order.edit")
        found = extras.at_location(context.company.id, location_id)
    return JsonResponse({"services": [_choice(s) for s in found.order_by("name")]})


# --- Lines ----------------------------------------------------------------------------------------


def _require_rental(actor: Actor, rental: Any) -> None:
    location_id = extras.rental_location(rental)
    if not access.RightsResolver().has_permission(actor, "rental.issue", location_id=location_id):
        raise errors.permission_denied("rental.issue")


def _require_line(actor: Actor, line: ExtraServiceLine) -> None:
    """A linked line — the rights of the rental; a standalone one — the rights of the order in
    the location of the service (decision У11)."""
    if line.rental_id is not None:
        _require_rental(actor, rentals.get_rental(line.company_id, line.rental_id))
        return
    if not access.RightsResolver().has_permission(
        actor, "order.edit", location_id=line.location_id
    ):
        raise errors.permission_denied("order.edit")


def _line_visible(actor: Actor, line: ExtraServiceLine) -> bool:
    resolver = access.RightsResolver()
    location_id = extra_lines.location_of(line)
    return resolver.has_permission(actor, "order.view", location_id=location_id) or (
        line.rental_id is not None
        and resolver.has_permission(actor, "rental.view", location_id=location_id)
    )


def _quantity(data: dict[str, Any]) -> int | None:
    value = data.get("quantity")
    if value in (None, ""):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise errors.validation_error("quantity", code="INTEGER_REQUIRED")
    return value


def _line_result(company_id: UUID, line_id: UUID, status: int = 200) -> CommandResult:
    return CommandResult(
        status, {"line": extra_lines.payload(extra_lines.get_line(company_id, line_id))}
    )


@require_POST
def rental_service_add(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    """A service linked to the rental, from its card (§ 13.4.5)."""
    from c4rsys.rentals.views import _rental_payload

    context, actor = _actor(request, cmp)
    _require_rental(actor, rentals.get_rental(context.company.id, rental_id))
    data = parse_json(request)
    service_id = _uuid(data, "service_id")
    quantity = _quantity(data)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        extra_lines.add_linked(
            ctx,
            context.company.id,
            rental_id,
            service_id=service_id,
            quantity=quantity,
            expected_version=expected_version,
        )
        rental = rentals.get_rental(context.company.id, rental_id)
        return CommandResult(201, {"rental": _rental_payload(rental)})

    payload = {
        "rental_id": str(rental_id),
        "service_id": str(service_id),
        "quantity": quantity,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "rental.service_add", actor, payload, handler)


@require_GET
def order_services(request: HttpRequest, cmp: str, order_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    orders.get_order(context.company.id, order_id)
    items = [
        extra_lines.payload(line)
        for line in extra_lines.lines_of_order(context.company.id, order_id)
        if _line_visible(actor, line)
    ]
    return JsonResponse({"lines": items})


def _period(data: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    return (
        _moment(data, "period_start", required=False),
        _moment(data, "period_end", required=False),
    )


@require_POST
def line_create(request: HttpRequest, cmp: str) -> JsonResponse:
    """A standalone service: into an open order of a client (`order_id`) or into a new order of
    the client with only this service (`client_id`), decision У1."""
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    location_id = _uuid(data, "location_id")
    order_id = _optional_uuid(data, "order_id")
    permission = "order.edit" if order_id is not None else "order.create"
    if not access.RightsResolver().has_permission(actor, permission, location_id=location_id):
        raise errors.permission_denied(permission)
    start, end = _period(data)
    request_data = extra_lines.Standalone(
        service_id=_uuid(data, "service_id"),
        location_id=location_id,
        quantity=_quantity(data),
        period_start=start,
        period_end=end,
        order_id=order_id,
        client_id=_optional_uuid(data, "client_id"),
        responsible_employment_id=actor.employment_id,
    )

    def handler(ctx: CommandContext) -> CommandResult:
        line = extra_lines.add_standalone(ctx, context.company.id, request_data)
        return _line_result(context.company.id, line.id, 201)

    payload = _encoded(
        {k: str(v) if isinstance(v, UUID) else v for k, v in request_data.__dict__.items()}
    )
    return run_and_respond(request, "service_line.create", actor, payload, handler)


@require_http_methods(["GET", "PATCH"])
def line_detail(request: HttpRequest, cmp: str, line_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    line = extra_lines.get_line(context.company.id, line_id)
    if not _line_visible(actor, line):
        raise errors.not_found()
    if request.method == "GET":
        return JsonResponse({"line": extra_lines.payload(line)})
    _require_line(actor, line)
    data = parse_json(request)
    quantity = _quantity(data)
    start, end = _period(data)
    if quantity is None and start is None and end is None:
        raise errors.validation_error("body", code="NOTHING_TO_CHANGE")
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        extra_lines.change(
            ctx,
            context.company.id,
            line_id,
            quantity=quantity,
            period_start=start,
            period_end=end,
            expected_version=expected_version,
        )
        return _line_result(context.company.id, line_id)

    payload = _encoded(
        {
            "line_id": str(line_id),
            "quantity": quantity,
            "period_start": start,
            "period_end": end,
            "expected_version": expected_version,
        }
    )
    return run_and_respond(request, "service_line.change", actor, payload, handler)


def _line_command(request: HttpRequest, cmp: str, line_id: UUID, command: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    line = extra_lines.get_line(context.company.id, line_id)
    if not _line_visible(actor, line):
        raise errors.not_found()
    _require_line(actor, line)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    at = _moment(data, "at", required=False)
    reason = get_str(data, "reason", max_length=500, required=False)
    company_id = context.company.id

    def handler(ctx: CommandContext) -> CommandResult:
        if command == "start":
            extra_lines.start(ctx, company_id, line_id, at, expected_version)
        elif command == "complete":
            extra_lines.complete(ctx, company_id, line_id, at, expected_version)
        elif command == "cancel":
            extra_lines.cancel(ctx, company_id, line_id, reason, expected_version)
        else:
            extra_lines.unlink(ctx, company_id, line_id, expected_version)
        return _line_result(company_id, line_id)

    payload = _encoded(
        {
            "line_id": str(line_id),
            "at": at,
            "reason": reason,
            "expected_version": expected_version,
        }
    )
    return run_and_respond(request, f"service_line.{command}", actor, payload, handler)


@require_POST
def line_start(request: HttpRequest, cmp: str, line_id: UUID) -> JsonResponse:
    return _line_command(request, cmp, line_id, "start")


@require_POST
def line_complete(request: HttpRequest, cmp: str, line_id: UUID) -> JsonResponse:
    return _line_command(request, cmp, line_id, "complete")


@require_POST
def line_cancel(request: HttpRequest, cmp: str, line_id: UUID) -> JsonResponse:
    return _line_command(request, cmp, line_id, "cancel")


@require_POST
def line_unlink(request: HttpRequest, cmp: str, line_id: UUID) -> JsonResponse:
    return _line_command(request, cmp, line_id, "unlink")


@require_POST
def line_free_add(request: HttpRequest, cmp: str, line_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    line = extra_lines.get_line(context.company.id, line_id)
    _require_line(actor, line)
    data = parse_json(request)
    values = _interval(data)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        extra_lines.add_free(
            ctx, context.company.id, line_id, expected_version=expected_version, **values
        )
        return _line_result(context.company.id, line_id, 201)

    payload = {"line_id": str(line_id), **_encoded(values), "expected_version": expected_version}
    return run_and_respond(request, "service_line.free_time_add", actor, payload, handler)


@require_POST
def line_free_remove(
    request: HttpRequest, cmp: str, line_id: UUID, interval_id: UUID
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    line = extra_lines.get_line(context.company.id, line_id)
    _require_line(actor, line)
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        extra_lines.remove_free(ctx, context.company.id, line_id, interval_id, expected_version)
        return _line_result(context.company.id, line_id)

    payload = {
        "line_id": str(line_id),
        "interval_id": str(interval_id),
        "expected_version": expected_version,
    }
    return run_and_respond(request, "service_line.free_time_remove", actor, payload, handler)
