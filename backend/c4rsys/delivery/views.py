"""HTTP API of deliveries and internal moves (ТЗ гл. 15, § 23.2.5, § 23.6.6; decisions Д-1…Д-11).

Rights are checked in the location the move starts from; viewing is allowed also in the
destination. A driver with «Own trips» sees and runs only the trips assigned to them, with the
minimum about the vehicle and the route (§ 23.2.5). Choosing the destination does not open its
data (§ 15.1.4).
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, staff
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.delivery import money, services
from c4rsys.delivery.models import Delivery, DeliveryService
from c4rsys.finance import charges
from c4rsys.fleet import services as fleet
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json

TEXT_MAX = 2000
LIST_LIMIT = 300


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _can(actor: Actor, permission: str, location_id: UUID | None = None) -> bool:
    return access.RightsResolver().has_permission(actor, permission, location_id=location_id)


def _require(actor: Actor, permission: str, location_id: UUID | None = None) -> None:
    if not _can(actor, permission, location_id):
        raise errors.permission_denied(permission)


def _own(actor: Actor, d: Delivery) -> bool:
    return (
        d.driver_id is not None
        and d.driver_id == actor.employment_id
        and _can(actor, "delivery.drive")
    )


def _visible(actor: Actor, d: Delivery) -> bool:
    return (
        _can(actor, "delivery.view", d.from_location_id)
        or _can(actor, "delivery.view", d.to_location_id)
        or _own(actor, d)
    )


def _require_or_own(actor: Actor, permission: str, d: Delivery) -> None:
    """The action by its right in the start location, or by the driver on their own trip."""
    if not (_can(actor, permission, d.from_location_id) or _own(actor, d)):
        raise errors.permission_denied(permission)


# --- Parsing -------------------------------------------------------------------------------------


def _uuid(data: dict[str, Any], key: str, *, required: bool = False) -> UUID | None:
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise errors.validation_error(key, code="FIELD_REQUIRED")
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _required(data: dict[str, Any], key: str) -> UUID:
    value = _uuid(data, key, required=True)
    if value is None:
        raise errors.validation_error(key, code="FIELD_REQUIRED")
    return value


def _moment(data: dict[str, Any], key: str, *, required: bool) -> datetime | None:
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


def _required_moment(data: dict[str, Any], key: str) -> datetime:
    moment = _moment(data, key, required=True)
    if moment is None:
        raise errors.validation_error(key, code="FIELD_REQUIRED")
    return moment


def _coordinate(value: Any, key: str, limit: int) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise errors.validation_error(key, code="COORDINATE_INVALID")
    try:
        number = Decimal(str(value)).quantize(Decimal("0.000001"))
    except InvalidOperation as exc:
        raise errors.validation_error(key, code="COORDINATE_INVALID") from exc
    if abs(number) > limit:
        raise errors.validation_error(key, code="COORDINATE_INVALID")
    return number


def _point(data: dict[str, Any], key: str, *, required: bool) -> services.Point | None:
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise errors.validation_error(f"{key}.kind", code="FIELD_REQUIRED")
        return None
    if not isinstance(value, dict):
        raise errors.validation_error(key)
    return services.Point(
        kind=get_str(value, "kind", max_length=10),
        location_id=_uuid(value, "location_id"),
        name=get_str(value, "name", max_length=200, required=False).strip(),
        address=get_str(value, "address", max_length=500, required=False).strip(),
        comment=get_str(value, "comment", max_length=1000, required=False).strip(),
        lat=_coordinate(value.get("lat"), f"{key}.lat", 90),
        lng=_coordinate(value.get("lng"), f"{key}.lng", 180),
    )


def _text(data: dict[str, Any], key: str) -> str:
    return get_str(data, key, max_length=TEXT_MAX, required=False).strip()


def _payload(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: data.get(k) for k in keys}


# --- Payloads ------------------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _amount(data: dict[str, Any], key: str) -> Decimal | None:
    """A non-negative amount with at most four places, or empty."""
    value = data.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise errors.validation_error(key, code="AMOUNT_INVALID")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise errors.validation_error(key, code="AMOUNT_INVALID") from exc
    exponent = amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or amount < 0
        or amount >= Decimal(10) ** 15
        or (isinstance(exponent, int) and -exponent > 4)
    ):
        raise errors.validation_error(key, code="AMOUNT_INVALID")
    return amount


def _price(data: dict[str, Any]) -> money.Price:
    return money.Price(service_id=_uuid(data, "service_id"), price=_amount(data, "price"))


def _require_price(actor: Actor, d_location: UUID, price: money.Price, company_id: UUID) -> None:
    """Changing the price substituted from the catalog needs its right (decision Д-5)."""
    if price.given:
        _, _, override = price.resolve(company_id)
        if override:
            _require(actor, "delivery.price", d_location)


def _delivery_payload(
    d: Delivery, *, detail: bool = False, actor: Actor | None = None
) -> dict[str, Any]:
    order = orders.get_order(d.company_id, d.order_id)
    names = staff.employee_names(d.company_id, {d.driver_id} if d.driver_id else set())
    result: dict[str, Any] = {
        "id": str(d.id),
        "version": d.version,
        "number": d.number,
        "state": d.state,
        "order_id": str(d.order_id),
        "order_number": order.number,
        "is_internal": order.is_internal,
        "vehicle_card_id": str(d.vehicle_card_id),
        "vehicle_name": fleet.card_names(d.company_id, {d.vehicle_card_id}).get(d.vehicle_card_id),
        "from": services._point(d, "from"),
        "to": services._point(d, "to"),
        "planned_start": _iso(d.planned_start),
        "planned_end": _iso(d.planned_end),
        "driver_id": str(d.driver_id) if d.driver_id else None,
        "driver_name": names.get(d.driver_id) if d.driver_id else None,
        "actual_start": _iso(d.actual_start),
        "actual_end": _iso(d.actual_end),
        "result": d.result or None,
        "result_comment": d.result_comment,
        "end_reason": d.end_reason,
    }
    # The driver of their own trip does not see the client's price (§ 15.2.1, § 23.2.5).
    managed = actor is None or _can(actor, "delivery.view", d.from_location_id)
    if managed:
        result["service_id"] = str(d.service_id) if d.service_id else None
        result["price"] = money.amount_text(d.price)
        result["currency"] = d.currency_id
    if detail:
        result["driver_overlaps"] = services.driver_overlaps(d)
        if managed:
            result["expenses"] = money.expenses_of(d)
            # § 15.3.5: the linked charge is shown for checking a correction or an annulment.
            result["charged"] = money.amount_text(charges.current_charge(money.SOURCE, d.id))
            result["later_operations"] = (
                services.dependents(d)
                if d.state in (Delivery.State.COMPLETED, Delivery.State.TERMINATED)
                else []
            )
    return result


def _result(d: Delivery, status: int = 200, actor: Actor | None = None) -> CommandResult:
    return CommandResult(status, {"delivery": _delivery_payload(d, detail=True, actor=actor)})


# --- List and creation ---------------------------------------------------------------------------


def _new(data: dict[str, Any]) -> services.NewDelivery:
    to = _point(data, "to", required=True)
    if to is None:
        raise errors.validation_error("to.kind", code="FIELD_REQUIRED")
    return services.NewDelivery(
        vehicle_card_id=_required(data, "vehicle_card_id"),
        to=to,
        from_point=_point(data, "from", required=False),
        planned_start=_moment(data, "planned_start", required=False),
        planned_end=_moment(data, "planned_end", required=False),
        driver_id=_uuid(data, "driver_id"),
        order_id=_uuid(data, "order_id"),
    )


ROUTE_KEYS = ("vehicle_card_id", "from", "to", "planned_start", "planned_end")


@require_http_methods(["GET", "POST"])
def delivery_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        query = services.deliveries_of(context.company.id).order_by("-number")
        params = request.GET
        if params.get("active") == "1":
            query = query.exclude(state__in=services.FINAL)
        if params.get("free") == "1":
            query = query.filter(state=Delivery.State.PLANNED, driver__isnull=True)
        if params.get("mine") == "1":
            query = query.filter(driver_id=actor.employment_id)
        for key in ("vehicle_card_id", "order_id"):
            if params.get(key):
                query = query.filter(**{key: _required({key: params[key]}, key)})
        items = []
        for d in query[:LIST_LIMIT]:
            if _visible(actor, d) or (
                params.get("free") == "1" and _can(actor, "delivery.take", d.from_location_id)
            ):
                items.append(_delivery_payload(d, actor=actor))
        return JsonResponse({"deliveries": items})

    data = parse_json(request)
    new = _new(data)
    price = _price(data)
    card = fleet.get_card(context.company.id, new.vehicle_card_id)
    source = services.source_location(card, new.from_point)
    _require(actor, "delivery.create", source)
    _require_price(actor, source, price, context.company.id)
    if new.driver_id is not None:
        _require(actor, "delivery.assign", source)
    if new.order_id is not None:
        order = orders.get_order(context.company.id, new.order_id)
        if not orders.has_right(actor, order, "order.edit"):
            raise errors.permission_denied("order.edit")

    def handler(ctx: CommandContext) -> CommandResult:
        d = services.create(ctx, context.company, new)
        money.apply_price(ctx, d, price, "delivery_added")
        return _result(d, 201, actor)

    payload = _payload(data, *ROUTE_KEYS, "driver_id", "order_id", "service_id", "price")
    return run_and_respond(request, "delivery.create", actor, payload, handler)


@require_POST
def delivery_record(request: HttpRequest, cmp: str) -> JsonResponse:
    """A move that already happened in one action (decision Д-1): by «Move vehicles», as before,
    or by the rights to create, start and finish moves."""
    context, actor = _actor(request, cmp)
    data = parse_json(request)
    to = _point(data, "to", required=True)
    if to is None:
        raise errors.validation_error("to.kind", code="FIELD_REQUIRED")
    new = services.Record(
        vehicle_card_id=_required(data, "vehicle_card_id"),
        to=to,
        driver_id=_required(data, "driver_id"),
        actual_start=_required_moment(data, "actual_start"),
        actual_end=_required_moment(data, "actual_end"),
        result=get_str(data, "result", max_length=24),
        comment=_text(data, "comment"),
    )
    card = fleet.get_card(context.company.id, new.vehicle_card_id)
    source = services.source_location(card, None)
    if not _can(actor, "vehicle.move", source) and not all(
        _can(actor, p, source) for p in ("delivery.create", "delivery.start", "delivery.finish")
    ):
        raise errors.permission_denied("vehicle.move")

    def handler(ctx: CommandContext) -> CommandResult:
        return _result(services.record(ctx, context.company, new), 201, actor)

    payload = _payload(
        data, "vehicle_card_id", "to", "driver_id", "actual_start", "actual_end", "result"
    )
    payload["comment"] = new.comment
    return run_and_respond(request, "delivery.record", actor, payload, handler)


def _delivery_for(context: companies.CompanyContext, actor: Actor, delivery_id: UUID) -> Delivery:
    d = services.get(context.company.id, delivery_id)
    if not _visible(actor, d):
        raise errors.not_found()
    return d


@require_http_methods(["GET", "PATCH"])
def delivery_detail(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    d = _delivery_for(context, actor, delivery_id)
    if request.method == "GET":
        return JsonResponse({"delivery": _delivery_payload(d, detail=True, actor=actor)})
    _require(actor, "delivery.edit", d.from_location_id)
    data = parse_json(request)
    new = _new({**data, "vehicle_card_id": str(d.vehicle_card_id)})
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        locked = services.locked(context.company.id, delivery_id, expected_version)
        return _result(services.change(ctx, locked, new), actor=actor)

    payload = {"delivery_id": str(delivery_id), **_payload(data, *ROUTE_KEYS, "expected_version")}
    return run_and_respond(request, "delivery.change", actor, payload, handler)


def _command(
    request: HttpRequest,
    cmp: str,
    delivery_id: UUID,
    command: str,
    check: Any,
    action: Any,
    keys: tuple[str, ...] = (),
) -> JsonResponse:
    """Checks the right by `check(actor, delivery, data)`, then runs `action(ctx, locked, data)`."""
    context, actor = _actor(request, cmp)
    d = _delivery_for(context, actor, delivery_id)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    check(actor, d, data)

    def handler(ctx: CommandContext) -> CommandResult:
        locked = services.locked(context.company.id, delivery_id, expected_version)
        return _result(action(ctx, locked, data), actor=actor)

    payload = {
        "delivery_id": str(delivery_id),
        "expected_version": expected_version,
        **_payload(data, *keys),
    }
    return run_and_respond(request, command, actor, payload, handler)


@require_POST
def delivery_assign(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    def check(actor: Actor, d: Delivery, data: dict[str, Any]) -> None:
        _require(actor, "delivery.assign", d.from_location_id)
        _uuid(data, "driver_id")

    return _command(
        request,
        cmp,
        delivery_id,
        "delivery.assign",
        check,
        lambda ctx, d, data: services.assign(ctx, d, _uuid(data, "driver_id")),
        ("driver_id",),
    )


@require_POST
def delivery_take(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    """A free move is taken by an employee with access to free moves (§ 15.2.4); the
    visibility check is the right to take in the start location."""
    context, actor = _actor(request, cmp)
    d = services.get(context.company.id, delivery_id)
    _require(actor, "delivery.take", d.from_location_id)
    expected_version = get_int(parse_json(request), "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        locked = services.locked(context.company.id, delivery_id, expected_version)
        return _result(services.take(ctx, locked), actor=actor)

    payload = {"delivery_id": str(delivery_id), "expected_version": expected_version}
    return run_and_respond(request, "delivery.take", actor, payload, handler)


@require_POST
def delivery_start(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    def check(actor: Actor, d: Delivery, data: dict[str, Any]) -> None:
        _require_or_own(actor, "delivery.start", d)
        _required_moment(data, "actual_start")

    return _command(
        request,
        cmp,
        delivery_id,
        "delivery.start",
        check,
        lambda ctx, d, data: services.start(ctx, d, _required_moment(data, "actual_start")),
        ("actual_start",),
    )


@require_POST
def delivery_finish(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    def check(actor: Actor, d: Delivery, data: dict[str, Any]) -> None:
        _require_or_own(actor, "delivery.finish", d)
        _required_moment(data, "actual_end")
        get_str(data, "result", max_length=24)

    return _command(
        request,
        cmp,
        delivery_id,
        "delivery.finish",
        check,
        lambda ctx, d, data: services.finish(
            ctx,
            d,
            _required_moment(data, "actual_end"),
            get_str(data, "result", max_length=24),
            _text(data, "comment"),
        ),
        ("actual_end", "result", "comment"),
    )


@require_POST
def delivery_terminate(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    def check(actor: Actor, d: Delivery, data: dict[str, Any]) -> None:
        _require_or_own(actor, "delivery.finish", d)
        _required_moment(data, "actual_end")

    return _command(
        request,
        cmp,
        delivery_id,
        "delivery.terminate",
        check,
        lambda ctx, d, data: services.terminate(
            ctx, d, _required_moment(data, "actual_end"), _text(data, "reason")
        ),
        ("actual_end", "reason"),
    )


@require_POST
def delivery_cancel(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    def check(actor: Actor, d: Delivery, data: dict[str, Any]) -> None:
        _require(actor, "delivery.cancel", d.from_location_id)

    return _command(
        request,
        cmp,
        delivery_id,
        "delivery.cancel",
        check,
        lambda ctx, d, data: services.cancel(ctx, d, _text(data, "reason")),
        ("reason",),
    )


@require_POST
def delivery_attach(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    """§ 15.1.6 (decision Д-9): to a client order with «Change moves» and «Change orders»."""

    def check(actor: Actor, d: Delivery, data: dict[str, Any]) -> None:
        _require(actor, "delivery.edit", d.from_location_id)
        _require_price(actor, d.from_location_id, _price(data), d.company_id)
        order = orders.get_order(d.company_id, _required(data, "order_id"))
        if not orders.has_right(actor, order, "order.edit"):
            raise errors.permission_denied("order.edit")

    return _command(
        request,
        cmp,
        delivery_id,
        "delivery.attach",
        check,
        lambda ctx, d, data: _attached(ctx, d, data),
        ("order_id", "service_id", "price"),
    )


def _attached(ctx: CommandContext, d: Delivery, data: dict[str, Any]) -> Delivery:
    services.attach(ctx, d, _required(data, "order_id"))
    money.apply_price(ctx, d, _price(data), "delivery_attached")
    return d


@require_GET
def drivers(request: HttpRequest, cmp: str) -> JsonResponse:
    """Active employees of the company who may drive (§ 15.2.4)."""
    context, actor = _actor(request, cmp)
    if not any(_can(actor, p) for p in ("delivery.assign", "delivery.create", "vehicle.move")):
        raise errors.permission_denied("delivery.assign")
    names = staff.employee_names(context.company.id)
    return JsonResponse(
        {
            "drivers": sorted(
                ({"id": str(k), "name": v} for k, v in names.items()), key=lambda e: e["name"]
            )
        }
    )


# --- Price, catalog and expenses (§ 15.2.2–15.2.3, § 15.2.6) -------------------------------------


@require_POST
def delivery_price(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    """The paid position of a client delivery is changed with «Change the delivery price»."""

    def check(actor: Actor, d: Delivery, data: dict[str, Any]) -> None:
        _require(actor, "delivery.price", d.from_location_id)
        if not _price(data).given:
            raise errors.validation_error("price", code="FIELD_REQUIRED")

    def action(ctx: CommandContext, d: Delivery, data: dict[str, Any]) -> Delivery:
        orders.ensure_editable(d.company_id, d.order_id)
        before = services.snapshot(d)
        money.apply_price(ctx, d, _price(data), "delivery_price_changed")
        d.version += 1
        d.save(update_fields=["version", "updated_at"])
        after = services.snapshot(d)
        ctx.audit(
            "delivery.price_changed",
            object_type="delivery",
            object_id=d.id,
            before={k: v for k, v in before.items() if v != after[k]},
            after={k: v for k, v in after.items() if v != before[k]},
        )
        return d

    return _command(
        request, cmp, delivery_id, "delivery.price", check, action, ("service_id", "price")
    )


@require_POST
def delivery_expense(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    """An actual expense: a payout with an expense article from an account the employee may pay
    from (§ 15.2.3, § 23.6.6; decision Д-6)."""
    context, actor = _actor(request, cmp)
    d = _delivery_for(context, actor, delivery_id)
    data = parse_json(request)
    account_id = _required(data, "cash_account_id")
    if not access.RightsResolver().has_permission(actor, "finance.payout", resource_id=account_id):
        raise errors.permission_denied("finance.payout")
    amount = _amount(data, "amount")
    if amount is None or amount == 0:
        raise errors.validation_error("amount", code="AMOUNT_INVALID")
    new = money.NewExpense(
        cash_account_id=account_id,
        amount=amount,
        currency=get_str(data, "currency", max_length=3).upper(),
        article_id=_required(data, "article_id"),
        counterparty_id=_uuid(data, "counterparty_id"),
        note=_text(data, "note"),
    )

    def handler(ctx: CommandContext) -> CommandResult:
        locked = services.locked(context.company.id, delivery_id, None)
        money.add_expense(ctx, locked, new)
        return _result(locked, 201, actor)

    payload = {
        "delivery_id": str(d.id),
        **_payload(data, "cash_account_id", "amount", "currency", "article_id", "counterparty_id"),
        "note": new.note,
    }
    return run_and_respond(request, "delivery.expense", actor, payload, handler)


def _service_payload(service: DeliveryService) -> dict[str, Any]:
    return {"id": str(service.id), "version": service.version, **money.service_snapshot(service)}


@require_http_methods(["GET", "POST"])
def service_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        if not any(
            _can(actor, p) for p in ("delivery.view", "delivery.create", "delivery.catalog")
        ):
            raise errors.permission_denied("delivery.view")
        items = DeliveryService.objects.filter(company=context.company).order_by("name")
        return JsonResponse({"services": [_service_payload(x) for x in items]})
    _require(actor, "delivery.catalog")
    data = parse_json(request)
    name = get_str(data, "name", max_length=200).strip()
    price = _amount(data, "price")
    if price is None:
        raise errors.validation_error("price", code="FIELD_REQUIRED")

    def handler(ctx: CommandContext) -> CommandResult:
        service = money.create_service(ctx, context.company, name, price)
        return CommandResult(201, {"service": _service_payload(service)})

    return run_and_respond(
        request, "delivery_service.create", actor, {"name": name, "price": str(price)}, handler
    )


@require_http_methods(["PATCH"])
def service_detail(request: HttpRequest, cmp: str, service_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "delivery.catalog")
    data = parse_json(request)
    name = get_str(data, "name", max_length=200, required=False).strip() or None
    price = _amount(data, "price")
    archived = data.get("archived")
    if archived is not None and not isinstance(archived, bool):
        raise errors.validation_error("archived")
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        service = money.locked_service(context.company.id, service_id, expected_version)
        service = money.change_service(ctx, service, name=name, price=price, archived=archived)
        return CommandResult(200, {"service": _service_payload(service)})

    payload = {
        "service_id": str(service_id),
        "name": name,
        "price": str(price) if price is not None else None,
        "archived": archived,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "delivery_service.change", actor, payload, handler)


# --- Corrections (§ 15.3.4–15.3.8) ---------------------------------------------------------------


@require_POST
def delivery_correct(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    """Actual times and the result of a finished move, with «Correct completed trips»."""

    def check(actor: Actor, d: Delivery, data: dict[str, Any]) -> None:
        _require(actor, "delivery.correct", d.from_location_id)

    def action(ctx: CommandContext, d: Delivery, data: dict[str, Any]) -> Delivery:
        comment = data.get("comment")
        return services.correct(
            ctx,
            d,
            services.Correction(
                actual_start=_moment(data, "actual_start", required=False),
                actual_end=_moment(data, "actual_end", required=False),
                result=get_str(data, "result", max_length=24, required=False) or None,
                comment=_text(data, "comment") if comment is not None else None,
                reason=_text(data, "reason"),
            ),
        )

    return _command(
        request,
        cmp,
        delivery_id,
        "delivery.correct",
        check,
        action,
        ("actual_start", "actual_end", "result", "comment", "reason"),
    )


@require_POST
def delivery_annul(request: HttpRequest, cmp: str, delivery_id: UUID) -> JsonResponse:
    """A wrong finished move recognized invalid, with «Correct completed trips» (§ 15.3.4)."""

    def check(actor: Actor, d: Delivery, data: dict[str, Any]) -> None:
        _require(actor, "delivery.correct", d.from_location_id)

    return _command(
        request,
        cmp,
        delivery_id,
        "delivery.annul",
        check,
        lambda ctx, d, data: services.annul(ctx, d, _text(data, "reason")),
        ("reason",),
    )
