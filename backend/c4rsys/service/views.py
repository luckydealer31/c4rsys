"""HTTP API of service works (ТЗ гл. 16, § 23.6.4; customer's decisions С-1…С-7 of 29.10.2026).

Rights are checked in the service location and its context (§ 23.6.4): the catalog and rates by
«Service catalog», posts by the service policy right (§ 16.3.1, § 23.3.7; block 14). A new order
made by a service operation needs no «Create orders» (§ 23.6.5); an
existing one needs «Change orders».
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import access, locations, staff
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet import services as fleet
from c4rsys.inspections import damages
from c4rsys.orders import services as orders
from c4rsys.parties import counterparties
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json
from c4rsys.service import catalog, maintenance, materials, operations
from c4rsys.service.models import (
    ContractorMaterial,
    MaintenanceRule,
    ServiceHandover,
    ServiceJob,
    ServiceMaterial,
    ServiceOperation,
    ServicePost,
    ServiceWork,
    SystemWork,
)
from c4rsys.warehouse import parts as work_issues_parts
from c4rsys.warehouse import work_issues

NAME_MAX = 200
TEXT_MAX = 2000


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _can(actor: Actor, permission: str, location_id: UUID | None = None) -> bool:
    context_id = (
        locations.enabled_context_id(actor.company_id, location_id, "service")
        if location_id is not None and actor.company_id is not None
        else None
    )
    return access.RightsResolver().has_permission(
        actor, permission, location_id=location_id, context_id=context_id
    )


def _require(actor: Actor, permission: str, location_id: UUID | None = None) -> None:
    if not _can(actor, permission, location_id):
        raise errors.permission_denied(permission)


def _require_any(actor: Actor, *permissions: str) -> None:
    if not any(_can(actor, p) for p in permissions):
        raise errors.permission_denied(permissions[0])


# --- Parsing -------------------------------------------------------------------------------------


def _uuid(data: dict[str, Any], key: str, *, required: bool = True) -> UUID | None:
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise errors.validation_error(key, code="FIELD_REQUIRED")
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _required_uuid(data: dict[str, Any], key: str) -> UUID:
    value = _uuid(data, key)
    if value is None:
        raise errors.validation_error(key, code="FIELD_REQUIRED")
    return value


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


def _amount(
    data: dict[str, Any], key: str, *, places: int = 4, positive: bool = False
) -> Decimal | None:
    """A non-negative amount (positive for hours and rates) or empty — not known yet."""
    value = data.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise errors.validation_error(key, code="AMOUNT_INVALID")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise errors.validation_error(key, code="AMOUNT_INVALID") from exc
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise errors.validation_error(key, code="AMOUNT_INVALID")
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > places:
        raise errors.validation_error(key, code="AMOUNT_INVALID")
    if amount >= Decimal(10) ** 15:
        raise errors.validation_error(key, code="AMOUNT_INVALID")
    return amount


def _text(data: dict[str, Any], key: str, *, required: bool) -> str:
    return get_str(data, key, max_length=TEXT_MAX, required=required).strip()


def _payload(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: data.get(k) for k in keys}


# --- Catalog (§ 16.1) ----------------------------------------------------------------------------


@require_GET
def system_works(request: HttpRequest, cmp: str) -> JsonResponse:
    _actor(request, cmp)
    items = [
        {"code": w.code, "names": w.names} for w in SystemWork.objects.order_by("sort", "code")
    ]
    return JsonResponse({"system_works": items})


def _work_payload(work: ServiceWork) -> dict[str, Any]:
    return {
        "id": str(work.id),
        "version": work.version,
        "system_names": catalog.system_names(work.system_work_id),
        **catalog.snapshot(work),
    }


def _work_data(data: dict[str, Any]) -> catalog.WorkData:
    return catalog.WorkData(
        name=get_str(data, "name", max_length=NAME_MAX).strip(),
        system_work=get_str(data, "system_work", max_length=60, required=False) or None,
        pricing=get_str(data, "pricing", max_length=10),
        price=_amount(data, "price"),
        hours=_amount(data, "hours", places=2, positive=True),
        rate=_amount(data, "rate", positive=True),
    )


WORK_KEYS = ("name", "system_work", "pricing", "price", "hours", "rate")


@require_http_methods(["GET", "POST"])
def work_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require_any(actor, "service.view", "service.catalog", "service.create")
        works = ServiceWork.objects.filter(company=context.company).order_by("name")
        return JsonResponse({"works": [_work_payload(w) for w in works]})
    _require(actor, "service.catalog")
    data = parse_json(request)
    work_data = _work_data(data)

    def handler(ctx: CommandContext) -> CommandResult:
        work = catalog.create_work(ctx, context.company, work_data)
        return CommandResult(201, {"work": _work_payload(work)})

    return run_and_respond(
        request, "service_work.create", actor, _payload(data, *WORK_KEYS), handler
    )


@require_http_methods(["GET", "PATCH"])
def work_detail(request: HttpRequest, cmp: str, work_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require_any(actor, "service.view", "service.catalog", "service.create")
        work = ServiceWork.objects.filter(company=context.company, pk=work_id).first()
        if work is None:
            raise errors.not_found()
        return JsonResponse({"work": _work_payload(work)})
    _require(actor, "service.catalog")
    data = parse_json(request)
    work_data = _work_data(data)
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        work = catalog.locked_work(context.company.id, work_id, expected_version)
        return CommandResult(
            200, {"work": _work_payload(catalog.change_work(ctx, work, work_data))}
        )

    payload = {"work_id": str(work_id), **_payload(data, *WORK_KEYS, "expected_version")}
    return run_and_respond(request, "service_work.change", actor, payload, handler)


def _archive(request: HttpRequest, cmp: str, work_id: UUID, archived: bool) -> JsonResponse:
    context, actor = _actor(request, cmp)
    _require(actor, "service.catalog")
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    confirmed = data.get("confirm_unconfigured") is True  # § 4.3.2

    def handler(ctx: CommandContext) -> CommandResult:
        from c4rsys.companies import readiness

        before = readiness.ready_contexts(context.company.id, "service")
        work = catalog.locked_work(context.company.id, work_id, expected_version)
        work = catalog.set_work_archived(ctx, work, archived=archived)
        lost = readiness.check_still_ready(ctx, context.company.id, "service", before, confirmed)
        return CommandResult(200, {"work": _work_payload(work), "unconfigured": lost})

    command = "service_work.archive" if archived else "service_work.restore"
    payload = {
        "work_id": str(work_id),
        "expected_version": expected_version,
        "confirm_unconfigured": confirmed,
    }
    return run_and_respond(request, command, actor, payload, handler)


@require_POST
def work_archive(request: HttpRequest, cmp: str, work_id: UUID) -> JsonResponse:
    return _archive(request, cmp, work_id, True)


@require_POST
def work_restore(request: HttpRequest, cmp: str, work_id: UUID) -> JsonResponse:
    return _archive(request, cmp, work_id, False)


def _rates_payload(company_id: UUID) -> list[dict[str, Any]]:
    return [
        {
            "location_id": str(r.location_id) if r.location_id else None,
            "rate": catalog.amount_text(r.rate),
            "currency": r.currency_id,
        }
        for r in catalog.rates_of(company_id)
    ]


@require_http_methods(["GET", "PUT"])
def rates(request: HttpRequest, cmp: str) -> JsonResponse:
    """The hourly rate of the company and of service locations (§ 16.1.2); an empty rate
    removes it."""
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require_any(actor, "service.view", "service.catalog", "service.create")
        return JsonResponse({"rates": _rates_payload(context.company.id)})
    data = parse_json(request)
    location_id = _uuid(data, "location_id", required=False)
    rate = _amount(data, "rate", positive=True)
    _require(actor, "service.catalog", location_id)

    def handler(ctx: CommandContext) -> CommandResult:
        catalog.set_rate(ctx, context.company, location_id, rate)
        return CommandResult(200, {"rates": _rates_payload(context.company.id)})

    payload = {"location_id": str(location_id) if location_id else None, "rate": str(rate)}
    return run_and_respond(request, "service_rate.set", actor, payload, handler)


# --- Posts (§ 16.3) ------------------------------------------------------------------------------


def _post_payload(post: ServicePost) -> dict[str, Any]:
    return {
        "id": str(post.id),
        "version": post.version,
        "location_id": str(post.location_id),
        "name": post.name,
        "state": post.state,
    }


@require_http_methods(["GET", "POST"])
def post_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require_any(actor, "service.view", "service.policy.manage")
        posts = catalog.posts_of(context.company.id)
        return JsonResponse({"posts": [_post_payload(p) for p in posts]})
    data = parse_json(request)
    location_id = _required_uuid(data, "location_id")
    name = get_str(data, "name", max_length=100).strip()
    _require(actor, "service.policy.manage", location_id)

    def handler(ctx: CommandContext) -> CommandResult:
        post = catalog.create_post(ctx, context.company, location_id, name)
        return CommandResult(201, {"post": _post_payload(post)})

    payload = {"location_id": str(location_id), "name": name}
    return run_and_respond(request, "service_post.create", actor, payload, handler)


@require_http_methods(["PATCH"])
def post_detail(request: HttpRequest, cmp: str, post_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    post = ServicePost.objects.filter(company=context.company, pk=post_id).first()
    if post is None:
        raise errors.not_found()
    _require(actor, "service.policy.manage", post.location_id)
    data = parse_json(request)
    name = get_str(data, "name", max_length=100, required=False).strip() or None
    archived = data.get("archived")
    if archived is not None and not isinstance(archived, bool):
        raise errors.validation_error("archived")
    expected_version = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        locked = catalog.locked_post(context.company.id, post_id, expected_version)
        locked = catalog.change_post(ctx, locked, name=name, archived=archived)
        return CommandResult(200, {"post": _post_payload(locked)})

    payload = {
        "post_id": str(post_id),
        "name": name,
        "archived": archived,
        "expected_version": expected_version,
    }
    return run_and_respond(request, "service_post.change", actor, payload, handler)


# --- Operations (§ 16.1.6, § 16.2–16.4) ----------------------------------------------------------


def _job_payload(
    job: ServiceJob, names: dict[UUID, str], parties: dict[UUID, str] | None = None
) -> dict[str, Any]:
    snap = job.snapshot
    return {
        "id": str(job.id),
        "version": job.version,
        "operation_id": str(job.operation_id),
        "work_id": str(job.work_id),
        "name": snap.get("name"),
        "system_work": snap.get("system_work"),
        "pricing": snap.get("pricing"),
        "hours": snap.get("hours"),
        "rate": snap.get("rate"),
        "rate_source": snap.get("rate_source"),
        "price": catalog.amount_text(job.price),
        "currency": job.currency_id,
        "executor_id": str(job.executor_id) if job.executor_id else None,
        "executor_name": names.get(job.executor_id) if job.executor_id else None,
        "contractor_id": str(job.contractor_id) if job.contractor_id else None,
        "contractor_name": (parties or {}).get(job.contractor_id) if job.contractor_id else None,
        "contractor_cost": catalog.amount_text(job.contractor_cost),
        "state": job.state,
        "actual_start": job.actual_start.isoformat() if job.actual_start else None,
        "actual_end": job.actual_end.isoformat() if job.actual_end else None,
        "odometer": catalog.amount_text(job.odometer),
        "comment": job.comment,
        "end_reason": job.end_reason,
        "editing_since": job.editing_since.isoformat() if job.editing_since else None,
    }


def _materials_payload(job_ids: list[UUID]) -> list[dict[str, Any]]:
    result = []
    found = materials.of_jobs(job_ids)
    costs = materials.costs_of(found)
    installed = {
        i.operation_line_id: i
        for i in (work_issues_parts.of_jobs(found[0].company_id, job_ids) if found else [])
    }
    for state in materials.states_of(found):
        m = state.material
        amount = materials.amount_of(state)
        cost = costs.get(m.id)
        installation = installed.get(m.warehouse_line_id) if m.warehouse_line_id else None
        result.append(
            {
                "id": str(m.id),
                "version": m.version,
                "job_id": str(m.job_id),
                # Block 15: planned (added to the work, maybe reserved), issued or taken off.
                "state": state.state,
                "reserve_id": str(m.reserve_id) if m.reserve_id else None,
                "reserve_state": work_issues.reserve_state(m.reserve_id) if m.reserve_id else None,
                "context_id": m.planned.get("context_id"),
                "individual": m.unit == "piece",
                "replaces_id": str(m.replaces_id) if m.replaces_id else None,
                "installation_id": str(installation.id) if installation else None,
                # § 11.2.9, § 11.4.4: the purchase cost of the consumed; unknown — incomplete.
                "cost": catalog.amount_text(cost.cost) if cost else None,
                "cost_known": cost.known if cost else True,
                "item_name": m.item_name,
                "label": m.label,
                "unit": m.unit,
                "quantity": catalog.amount_text(m.quantity),
                "returned": catalog.amount_text(state.returned),
                "consumed": catalog.amount_text(state.consumed),
                "outstanding": catalog.amount_text(state.outstanding),
                "price": catalog.amount_text(m.price),
                "price_per": catalog.amount_text(m.price_per),
                "amount": catalog.amount_text(amount),
                "currency": m.currency_id,
            }
        )
    return result


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _operation_payload(op: ServiceOperation, *, detail: bool = True) -> dict[str, Any]:
    order = orders.get_order(op.company_id, op.order_id)
    jobs = operations.jobs_of(op)
    names = staff.employee_names(op.company_id, {j.executor_id for j in jobs if j.executor_id})
    handovers = list(ServiceHandover.objects.filter(operation=op).order_by("handed_at", "id"))
    parties = counterparties.names(
        op.company_id,
        {j.contractor_id for j in jobs if j.contractor_id}
        | {h.counterparty_id for h in handovers}
        | ({op.counterparty_id} if op.counterparty_id else set()),
    )
    post_name = (
        ServicePost.objects.filter(pk=op.post_id).values_list("name", flat=True).first()
        if op.post_id
        else None
    )
    result: dict[str, Any] = {
        "id": str(op.id),
        "version": op.version,
        "number": op.number,
        "state": op.state,
        "order_id": str(op.order_id),
        "order_number": order.number,
        "order_state": order.state,
        "is_internal": order.is_internal,
        "client_id": str(order.client_id) if order.client_id else None,
        "vehicle_card_id": str(op.vehicle_card_id),
        "vehicle_name": fleet.card_names(op.company_id, {op.vehicle_card_id}).get(
            op.vehicle_card_id
        ),
        "location_id": str(op.location_id) if op.location_id else None,
        "counterparty_id": str(op.counterparty_id) if op.counterparty_id else None,
        "counterparty_name": parties.get(op.counterparty_id) if op.counterparty_id else None,
        "reason": op.reason,
        "planned_start": _iso(op.planned_start),
        "planned_end": _iso(op.planned_end),
        "post_id": str(op.post_id) if op.post_id else None,
        # § 16.3.5: the post is free from this moment, the vehicle stays in the service.
        "post_released_at": op.post_released_at.isoformat() if op.post_released_at else None,
        "post_name": post_name,
        "accepted_at": _iso(op.accepted_at),
        "finished_at": _iso(op.finished_at),
        "end_reason": op.end_reason,
        "created_at": op.created_at.isoformat(),
        "jobs": [_job_payload(j, names, parties) for j in jobs],
        "handovers": [
            {
                "id": str(h.id),
                "version": h.version,
                "kind": h.kind,
                "description": h.description,
                "counterparty_id": str(h.counterparty_id),
                "counterparty_name": parties.get(h.counterparty_id),
                "handed_at": h.handed_at.isoformat(),
                "returned_at": _iso(h.returned_at),
            }
            for h in handovers
        ],
        "materials": _materials_payload([j.id for j in jobs]),
        **_parts_payload(op, [j.id for j in jobs]),
    }
    if detail:
        result["blockers"] = list(operations.blockers(op, order.is_internal))
    return result


def _visible(actor: Actor, op: ServiceOperation) -> bool:
    return _can(actor, "service.view", op.location_id)


@require_http_methods(["GET", "POST"])
def operation_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        query = operations.operations_of(context.company.id).order_by("-number")
        location = request.GET.get("location_id")
        if location:
            query = query.filter(location_id=_required_uuid({"l": location}, "l"))
        if request.GET.get("active") == "1":
            query = query.exclude(state__in=operations.FINAL_OPERATION)
        vehicle = request.GET.get("vehicle_card_id")
        if vehicle:
            query = query.filter(vehicle_card_id=_required_uuid({"v": vehicle}, "v"))
        order_param = request.GET.get("order_id")
        if order_param:
            query = query.filter(order_id=_required_uuid({"o": order_param}, "o"))
        allowed: dict[UUID | None, bool] = {}
        items = []
        for op in query[:300]:
            if op.location_id not in allowed:
                allowed[op.location_id] = _visible(actor, op)
            if allowed[op.location_id]:
                items.append(_operation_payload(op, detail=False))
        return JsonResponse({"operations": items})

    data = parse_json(request)
    location_id = _uuid(data, "location_id", required=False)
    counterparty_id = _uuid(data, "counterparty_id", required=False)
    _require(actor, "service.create", location_id)
    if counterparty_id is not None or location_id is None:
        # § 16.6.1, § 23.6.4: a service at an external counterparty.
        _require(actor, "service.external")
    order_id = _uuid(data, "order_id", required=False)
    client_id = _uuid(data, "client_id", required=False)
    internal = data.get("internal") is True
    if order_id is not None:
        order = orders.get_order(context.company.id, order_id)
        if not orders.has_right(actor, order, "order.edit"):
            raise errors.permission_denied("order.edit")
    elif client_id is not None:
        from c4rsys.parties import services as clients

        clients.get_client(context.company.id, client_id)
    works = data.get("work_ids") or []
    if not isinstance(works, list):
        raise errors.validation_error("work_ids")
    new = operations.NewOperation(
        vehicle_card_id=_required_uuid(data, "vehicle_card_id"),
        location_id=location_id,
        counterparty_id=counterparty_id,
        reason=_text(data, "reason", required=True),
        order_id=order_id,
        client_id=client_id,
        internal=internal,
        plan=operations.Plan(
            _moment(data, "planned_start", required=False),
            _moment(data, "planned_end", required=False),
            _uuid(data, "post_id", required=False),
        ),
        work_ids=tuple(_required_uuid({"w": w}, "w") for w in works),
    )
    if not new.reason:
        raise errors.validation_error("reason", code="FIELD_REQUIRED")
    fleet.get_card(context.company.id, new.vehicle_card_id)

    def handler(ctx: CommandContext) -> CommandResult:
        op = operations.create_operation(ctx, context.company, new)
        return CommandResult(201, {"operation": _operation_payload(op)})

    payload = _payload(
        data,
        "vehicle_card_id",
        "location_id",
        "counterparty_id",
        "reason",
        "order_id",
        "client_id",
        "internal",
        "planned_start",
        "planned_end",
        "post_id",
        "work_ids",
    )
    return run_and_respond(request, "service_operation.create", actor, payload, handler)


def _operation_for(context: companies.CompanyContext, actor: Actor, op_id: UUID) -> Any:
    op = operations.get_operation(context.company.id, op_id)
    if not _visible(actor, op):
        raise errors.not_found()
    return op


@require_GET
def operation_detail(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    op = _operation_for(context, actor, operation_id)
    return JsonResponse({"operation": _operation_payload(op)})


def _operation_command(
    request: HttpRequest,
    cmp: str,
    operation_id: UUID,
    permission: str,
    command: str,
    action: Any,
    keys: tuple[str, ...] = (),
) -> JsonResponse:
    """Runs `action(ctx, op, data)` on the locked operation with the permission in its place."""
    context, actor = _actor(request, cmp)
    op = _operation_for(context, actor, operation_id)
    _require(actor, permission, op.location_id)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    action(None, op, {**data, "_actor": actor})  # validates the input before the command runs

    def handler(ctx: CommandContext) -> CommandResult:
        locked = operations.locked_operation(context.company.id, operation_id, expected_version)
        action(ctx, locked, data)
        locked.refresh_from_db()
        return CommandResult(200, {"operation": _operation_payload(locked)})

    payload = {
        "operation_id": str(operation_id),
        "expected_version": expected_version,
        **_payload(data, *keys),
    }
    return run_and_respond(request, command, actor, payload, handler)


@require_POST
def operation_plan(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, op: Any, data: dict[str, Any]) -> None:
        plan = operations.Plan(
            _moment(data, "planned_start", required=False),
            _moment(data, "planned_end", required=False),
            _uuid(data, "post_id", required=False),
        )
        if ctx is not None:
            operations.change_plan(ctx, op, plan)

    keys = ("planned_start", "planned_end", "post_id")
    return _operation_command(
        request, cmp, operation_id, "service.create", "service_operation.plan", action, keys
    )


@require_POST
def operation_accept(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, op: Any, data: dict[str, Any]) -> None:
        at = _moment(data, "accepted_at", required=True)
        post_id = _uuid(data, "post_id", required=False)
        if ctx is not None and at is not None:
            operations.accept(ctx, op, at, post_id)

    return _operation_command(
        request,
        cmp,
        operation_id,
        "service.accept",
        "service_operation.accept",
        action,
        ("accepted_at", "post_id"),
    )


@require_POST
def operation_place(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, op: Any, data: dict[str, Any]) -> None:
        post_id = _uuid(data, "post_id", required=False)
        if ctx is not None:
            operations.place(ctx, op, post_id)

    return _operation_command(
        request,
        cmp,
        operation_id,
        "service.place",
        "service_operation.place",
        action,
        ("post_id",),
    )


@require_POST
def operation_add_job(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, op: Any, data: dict[str, Any]) -> None:
        work_id = _required_uuid(data, "work_id")
        executor_id = _uuid(data, "executor_id", required=False)
        contractor_id = _uuid(data, "contractor_id", required=False)
        if ctx is None and contractor_id is not None:
            _require(data["_actor"], "service.external")
        if ctx is not None:
            operations.add_job(ctx, op, work_id, executor_id, contractor_id)

    return _operation_command(
        request,
        cmp,
        operation_id,
        "service.create",
        "service_operation.add_job",
        action,
        ("work_id", "executor_id", "contractor_id"),
    )


@require_POST
def operation_finish(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, op: Any, data: dict[str, Any]) -> None:
        if ctx is not None:
            operations.finish(ctx, op)

    return _operation_command(
        request, cmp, operation_id, "service.complete", "service_operation.finish", action
    )


@require_POST
def operation_reopen(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    """Opening a finished service again for corrections (§ 16.8.2): a separate right."""

    def action(ctx: CommandContext | None, op: Any, data: dict[str, Any]) -> None:
        reason = _text(data, "reason", required=True)
        if ctx is not None:
            operations.reopen_operation(ctx, op, reason)

    return _operation_command(
        request,
        cmp,
        operation_id,
        "service.reopen",
        "service_operation.reopen",
        action,
        ("reason",),
    )


@require_POST
def job_open(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    """Opening a finished work for editing (§ 16.8.2): a separate right and a reason."""

    def action(ctx: CommandContext | None, job: Any, data: dict[str, Any]) -> None:
        reason = _text(data, "reason", required=True)
        if ctx is not None:
            operations.open_job(ctx, job, reason)

    return _job_command(
        request, cmp, job_id, "service.reopen", "service_job.open", action, ("reason",)
    )


@require_POST
def job_close(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, job: Any, data: dict[str, Any]) -> None:
        if ctx is not None:
            operations.close_job(ctx, job)

    return _job_command(request, cmp, job_id, "service.reopen", "service_job.close", action, ())


@require_POST
def job_correct(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    """Correcting the facts of a done or terminated work (§ 16.8.2): a separate right."""

    def action(ctx: CommandContext | None, job: Any, data: dict[str, Any]) -> None:
        reason = _text(data, "reason", required=True)
        start = _moment(data, "actual_start", required=False)
        end = _moment(data, "actual_end", required=False)
        odometer = _amount(data, "odometer", places=1)
        comment = _text(data, "comment", required=False) if "comment" in data else None
        if ctx is not None:
            operations.correct_job(
                ctx,
                job,
                reason=reason,
                actual_start=start,
                actual_end=end,
                odometer=odometer,
                comment=comment,
            )

    return _job_command(
        request,
        cmp,
        job_id,
        "service.reopen",
        "service_job.correct",
        action,
        ("reason", "actual_start", "actual_end", "odometer", "comment"),
    )


@require_POST
def operation_cancel(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, op: Any, data: dict[str, Any]) -> None:
        reason = _text(data, "reason", required=True)
        if ctx is not None:
            operations.cancel_operation(ctx, op, reason)

    return _operation_command(
        request,
        cmp,
        operation_id,
        "service.cancel",
        "service_operation.cancel",
        action,
        ("reason",),
    )


@require_POST
def operation_terminate(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, op: Any, data: dict[str, Any]) -> None:
        reason = _text(data, "reason", required=True)
        if ctx is not None:
            operations.terminate_operation(ctx, op, reason)

    return _operation_command(
        request,
        cmp,
        operation_id,
        "service.cancel",
        "service_operation.terminate",
        action,
        ("reason",),
    )


# --- Works (§ 16.4) ------------------------------------------------------------------------------


def _job_command(
    request: HttpRequest,
    cmp: str,
    job_id: UUID,
    permission: str,
    command: str,
    action: Any,
    keys: tuple[str, ...] = (),
    *,
    own_or_others: bool = False,
) -> JsonResponse:
    """Runs `action(ctx, job, data)` on the locked work. With `own_or_others` a work of another
    executor needs «Enter the result for another executor» (§ 16.4.2)."""
    context, actor = _actor(request, cmp)
    job = operations.get_job(context.company.id, job_id)
    if not _visible(actor, job.operation):
        raise errors.not_found()
    location_id = job.operation.location_id
    _require(actor, permission, location_id)
    if own_or_others and job.executor_id is not None and job.executor_id != actor.employment_id:
        _require(actor, "service.work.others", location_id)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    action(None, None, data)

    def handler(ctx: CommandContext) -> CommandResult:
        locked = operations.locked_job(context.company.id, job_id, expected_version)
        action(ctx, locked, data)
        op = operations.get_operation(context.company.id, locked.operation_id)
        return CommandResult(200, {"operation": _operation_payload(op)})

    payload = {"job_id": str(job_id), "expected_version": expected_version, **_payload(data, *keys)}
    return run_and_respond(request, command, actor, payload, handler)


@require_POST
def job_assign(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, job: Any, data: dict[str, Any]) -> None:
        executor_id = _uuid(data, "executor_id", required=False)
        if ctx is not None:
            operations.assign(ctx, job, executor_id)

    return _job_command(
        request, cmp, job_id, "service.assign", "service_job.assign", action, ("executor_id",)
    )


@require_POST
def job_take(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, job: Any, data: dict[str, Any]) -> None:
        if ctx is not None:
            operations.take(ctx, job)

    return _job_command(request, cmp, job_id, "service.work", "service_job.take", action)


@require_POST
def job_start(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, job: Any, data: dict[str, Any]) -> None:
        at = _moment(data, "actual_start", required=True)
        if ctx is not None and at is not None:
            operations.start(ctx, job, at)

    return _job_command(
        request,
        cmp,
        job_id,
        "service.work",
        "service_job.start",
        action,
        ("actual_start",),
        own_or_others=True,
    )


@require_POST
def job_complete(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, job: Any, data: dict[str, Any]) -> None:
        at = _moment(data, "actual_end", required=True)
        odometer = _amount(data, "odometer", places=1)
        comment = _text(data, "comment", required=False)
        cost = _amount(data, "contractor_cost")
        # § 16.5.4: the damages fixed by this work, chosen explicitly, with result photos.
        fixed = damages.parse_fixes(data.get("fixed_damages"))
        # § 8.2.13: the system work «Odometer replacement».
        replacement = _replacement(data.get("odometer_replacement"))
        if ctx is not None and at is not None:
            operations.complete(ctx, job, at, odometer, comment, cost, fixed, replacement)

    return _job_command(
        request,
        cmp,
        job_id,
        "service.work",
        "service_job.complete",
        action,
        (
            "actual_end",
            "odometer",
            "comment",
            "contractor_cost",
            "fixed_damages",
            "odometer_replacement",
        ),
        own_or_others=True,
    )


def _replacement(value: Any) -> tuple[Decimal, Decimal] | None:
    """{"old_final", "new_start"}: the last reading of the old odometer, the first of the new."""
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise errors.validation_error("odometer_replacement")
    old = _amount(value, "old_final", places=1)
    new = _amount(value, "new_start", places=1)
    if old is None:
        raise errors.validation_error("old_final", code="FIELD_REQUIRED")
    if new is None:
        raise errors.validation_error("new_start", code="FIELD_REQUIRED")
    return old, new


@require_POST
def job_terminate(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, job: Any, data: dict[str, Any]) -> None:
        at = _moment(data, "actual_end", required=True)
        reason = _text(data, "reason", required=True)
        price = _amount(data, "price")
        comment = _text(data, "comment", required=False)
        cost = _amount(data, "contractor_cost")
        if ctx is not None and at is not None:
            operations.terminate(ctx, job, at, reason, price, comment, cost)

    return _job_command(
        request,
        cmp,
        job_id,
        "service.work",
        "service_job.terminate",
        action,
        ("actual_end", "reason", "price", "comment", "contractor_cost"),
        own_or_others=True,
    )


@require_POST
def job_cancel(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, job: Any, data: dict[str, Any]) -> None:
        reason = _text(data, "reason", required=False)
        if ctx is not None:
            operations.cancel(ctx, job, reason)

    return _job_command(
        request, cmp, job_id, "service.cancel", "service_job.cancel", action, ("reason",)
    )


@require_POST
def job_price(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, job: Any, data: dict[str, Any]) -> None:
        price = _amount(data, "price")
        if price is None:
            raise errors.validation_error("price", code="FIELD_REQUIRED")
        if ctx is not None:
            operations.set_price(ctx, job, price)

    return _job_command(
        request, cmp, job_id, "service.price", "service_job.price", action, ("price",)
    )


@require_GET
def queue(request: HttpRequest, cmp: str) -> JsonResponse:
    """Free works of the locations where the employee may work (§ 23.2.4; decision С-7)."""
    context, actor = _actor(request, cmp)
    jobs = list(
        operations.unassigned_jobs(context.company.id, None)
        .select_related("operation")
        .order_by("created_at", "id")[:300]
    )
    allowed: dict[UUID | None, bool] = {}
    items = []
    for job in jobs:
        location_id = job.operation.location_id
        if location_id not in allowed:
            allowed[location_id] = _can(actor, "service.work", location_id)
        if allowed[location_id]:
            items.append(
                {
                    **_job_payload(job, {}),
                    "operation_number": job.operation.number,
                    "location_id": str(location_id),
                    "vehicle_name": fleet.card_names(
                        context.company.id, {job.operation.vehicle_card_id}
                    ).get(job.operation.vehicle_card_id),
                }
            )
    return JsonResponse({"jobs": items})


@require_GET
def executors(request: HttpRequest, cmp: str) -> JsonResponse:
    """Active employees to appoint as executors (§ 16.4.1)."""
    context, actor = _actor(request, cmp)
    _require_any(actor, "service.assign", "service.create")
    names = staff.employee_names(context.company.id)
    return JsonResponse(
        {
            "employees": sorted(
                ({"id": str(k), "name": v} for k, v in names.items()), key=lambda e: e["name"]
            )
        }
    )


# --- Materials (§ 16.5.1, § 16.7.3) --------------------------------------------------------------


def _in_warehouse(actor: Actor, company_id: UUID, context_id: UUID, *permissions: str) -> None:
    location = work_issues.warehouse_location(company_id, context_id)
    if not any(
        access.RightsResolver().has_permission(
            actor, permission, location_id=location, context_id=context_id
        )
        for permission in permissions
    ):
        raise errors.permission_denied(permissions[0])


@require_POST
def job_materials(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    """A part from a warehouse to the work: «Change service operations» in the service location
    and the issue right in the warehouse's location (PROPOSAL). With `plan` the material is only
    added to the work — reserved by the company's mode (§ 11.7.7); PROPOSAL: «Issue» or «Reserve»
    in the warehouse."""
    context, actor = _actor(request, cmp)
    job = operations.get_job(context.company.id, job_id)
    if not _visible(actor, job.operation):
        raise errors.not_found()
    _require(actor, "service.create", job.operation.location_id)
    data = parse_json(request)
    context_id = _required_uuid(data, "context_id")
    take = work_issues.parse_take(data)
    planning = data.get("plan") is True
    replaces_id = _uuid(data, "replaces_id", required=False)
    if planning:
        _in_warehouse(actor, context.company.id, context_id, "warehouse.issue", "warehouse.reserve")
    else:
        _in_warehouse(actor, context.company.id, context_id, "warehouse.issue")

    def handler(ctx: CommandContext) -> CommandResult:
        locked = operations.locked_job(
            context.company.id, job_id, get_int(data, "expected_version")
        )
        if planning:
            materials.plan(ctx, context.company, locked, context_id, take, replaces_id)
        else:
            added = materials.add(ctx, context.company, locked, context_id, take)
            if replaces_id is not None:
                for m in added:
                    materials.set_replaces(ctx, m, replaces_id)
        op = operations.get_operation(context.company.id, locked.operation_id)
        return CommandResult(201, {"operation": _operation_payload(op)})

    payload = {
        "job_id": str(job_id),
        "context_id": str(context_id),
        "plan": planning,
        "replaces_id": str(replaces_id) if replaces_id else None,
        **take.as_dict(),
    }
    payload["expected_version"] = data.get("expected_version")
    return run_and_respond(request, "service_material.add", actor, payload, handler)


def _material_command(
    request: HttpRequest, cmp: str, material_id: UUID, command: str, action: Any
) -> JsonResponse:
    """Runs `action(ctx, material, data)`; the right is checked by `action(None, material,
    data)` with the actor in `data["_actor"]`."""
    context, actor = _actor(request, cmp)
    found = (
        ServiceMaterial.objects.select_related("job__operation")
        .filter(company=context.company, pk=material_id)
        .first()
    )
    if found is None or not _visible(actor, found.job.operation):
        raise errors.not_found()
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    action(None, found, {**data, "_actor": actor})

    def handler(ctx: CommandContext) -> CommandResult:
        operations.locked_operation(context.company.id, found.job.operation_id)
        locked = materials.locked(context.company.id, material_id, expected_version)
        action(ctx, locked, data)
        op = operations.get_operation(context.company.id, found.job.operation_id)
        return CommandResult(200, {"operation": _operation_payload(op)})

    payload = {
        "material_id": str(material_id),
        "expected_version": expected_version,
        **{k: v for k, v in data.items() if k != "expected_version"},
    }
    return run_and_respond(request, command, actor, payload, handler)


def _quantity(data: dict[str, Any]) -> Decimal | None:
    return _amount(data, "quantity", places=2, positive=True)


@require_POST
def material_return(request: HttpRequest, cmp: str, material_id: UUID) -> JsonResponse:
    """Unused back to the warehouse: the issue right in the warehouse's location."""

    def action(ctx: CommandContext | None, m: Any, data: dict[str, Any]) -> None:
        quantity = _quantity(data)
        target_id = _uuid(data, "target_context_id", required=False)
        if ctx is None:
            if m.warehouse_line_id is None:
                raise errors.business_rule("SERVICE_MATERIAL_NOT_ISSUED")
            location = work_issues.line_location(m.company_id, m.warehouse_line_id)
            if not access.RightsResolver().has_permission(
                data["_actor"], "warehouse.issue", location_id=location
            ):
                raise errors.permission_denied("warehouse.issue")
            if target_id is not None:
                # § 11.4.5: a return into another warehouse is a return and a move (PROPOSAL:
                # «Move» in both warehouses).
                for place in (location, work_issues.warehouse_location(m.company_id, target_id)):
                    if not access.RightsResolver().has_permission(
                        data["_actor"], "warehouse.move", location_id=place
                    ):
                        raise errors.permission_denied("warehouse.move")
            return
        materials.return_unused(ctx, m, quantity, target_id)

    return _material_command(request, cmp, material_id, "service_material.return", action)


@require_POST
def material_consume(request: HttpRequest, cmp: str, material_id: UUID) -> JsonResponse:
    """The use of an issued part confirmed by whoever does the works (§ 23.2.4)."""

    def action(ctx: CommandContext | None, m: Any, data: dict[str, Any]) -> None:
        quantity = _quantity(data)
        if ctx is None:
            _require(data["_actor"], "service.work", m.job.operation.location_id)
            return
        materials.consume(ctx, m, quantity, "service_material_consumed")

    return _material_command(request, cmp, material_id, "service_material.consume", action)


@require_POST
def material_price(request: HttpRequest, cmp: str, material_id: UUID) -> JsonResponse:
    def action(ctx: CommandContext | None, m: Any, data: dict[str, Any]) -> None:
        price = _amount(data, "price")
        if price is None:
            raise errors.validation_error("price", code="FIELD_REQUIRED")
        if ctx is None:
            _require(data["_actor"], "service.price", m.job.operation.location_id)
            return
        materials.set_price(ctx, m, price)

    return _material_command(request, cmp, material_id, "service_material.price", action)


# --- External contractors (§ 16.6) ---------------------------------------------------------------


@require_POST
def job_contractor_cost(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    """The contractor's cost for the company, apart from the client's price (§ 16.6.3)."""

    def action(ctx: CommandContext | None, job: Any, data: dict[str, Any]) -> None:
        cost = _amount(data, "contractor_cost")
        if cost is None:
            raise errors.validation_error("contractor_cost", code="FIELD_REQUIRED")
        if ctx is not None:
            operations.set_contractor_cost(ctx, job, cost)

    return _job_command(
        request,
        cmp,
        job_id,
        "service.external",
        "service_job.contractor_cost",
        action,
        ("contractor_cost",),
    )


@require_POST
def operation_hand_over(request: HttpRequest, cmp: str, operation_id: UUID) -> JsonResponse:
    """The vehicle or a part handed over to a contractor (§ 16.6.6; § 23.6.4)."""

    def action(ctx: CommandContext | None, op: Any, data: dict[str, Any]) -> None:
        at = _moment(data, "handed_at", required=True)
        kind = get_str(data, "kind", max_length=10)
        description = _text(data, "description", required=False)
        counterparty_id = _uuid(data, "counterparty_id", required=False)
        if ctx is not None and at is not None:
            operations.hand_over(
                ctx,
                op,
                kind=kind,
                description=description,
                counterparty_id=counterparty_id,
                at=at,
            )

    return _operation_command(
        request,
        cmp,
        operation_id,
        "service.handover",
        "service_operation.hand_over",
        action,
        ("handed_at", "kind", "description", "counterparty_id"),
    )


@require_POST
def handover_return(request: HttpRequest, cmp: str, handover_id: UUID) -> JsonResponse:
    """The actual return from the contractor, apart from the acceptance of the works."""
    context, actor = _actor(request, cmp)
    handover = (
        ServiceHandover.objects.select_related("operation")
        .filter(company=context.company, pk=handover_id)
        .first()
    )
    if handover is None or not _visible(actor, handover.operation):
        raise errors.not_found()
    _require(actor, "service.handover", handover.operation.location_id)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    at = _moment(data, "returned_at", required=True)

    def handler(ctx: CommandContext) -> CommandResult:
        operations.locked_operation(context.company.id, handover.operation_id)
        locked = ServiceHandover.objects.select_for_update().get(pk=handover_id)
        if locked.version != expected_version:
            raise errors.version_conflict()
        if at is not None:
            operations.mark_returned(ctx, locked, at)
        op = operations.get_operation(context.company.id, handover.operation_id)
        return CommandResult(200, {"operation": _operation_payload(op)})

    payload = {
        "handover_id": str(handover_id),
        "expected_version": expected_version,
        "returned_at": data.get("returned_at"),
    }
    return run_and_respond(request, "service_handover.return", actor, payload, handler)


# --- Maintenance of vehicles (ch. 9) -------------------------------------------------------------


def _iso_date(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _rule_payload(rule: MaintenanceRule, status: maintenance.Status) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "version": rule.version,
        "vehicle_card_id": str(rule.vehicle_card_id),
        "name": rule.name,
        "system_work": rule.system_work_id,
        "work_id": str(rule.work_id) if rule.work_id else None,
        "interval_distance": rule.interval_distance,
        "interval_days": rule.interval_days,
        "remind_distance": rule.remind_distance,
        "remind_days": rule.remind_days,
        "last_done_on": _iso_date(rule.last_done_on),
        "last_done_odometer": catalog.amount_text(rule.last_done_odometer),
        "next_due_on": _iso_date(rule.next_due_on),
        "next_due_odometer": catalog.amount_text(rule.next_due_odometer),
        "state": rule.state,
        "status": {
            "state": status.state,
            "base": status.base,
            "base_on": _iso_date(status.base_on),
            "base_odometer": catalog.amount_text(status.base_odometer),
            "due_on": _iso_date(status.due_on),
            "due_odometer": catalog.amount_text(status.due_odometer),
        },
    }


def _whole(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise errors.validation_error(key)
    try:
        number = int(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc
    if number <= 0 or number > 10**7:
        raise errors.validation_error(key)
    return number


def _day(data: dict[str, Any], key: str) -> date | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise errors.validation_error(key, code="DATE_INVALID") from exc


def _rule_data(data: dict[str, Any]) -> maintenance.RuleData:
    return maintenance.RuleData(
        name=get_str(data, "name", max_length=NAME_MAX).strip(),
        system_work=get_str(data, "system_work", max_length=60, required=False) or None,
        work_id=_uuid(data, "work_id", required=False),
        interval_distance=_whole(data, "interval_distance"),
        interval_days=_whole(data, "interval_days"),
        remind_distance=_whole(data, "remind_distance"),
        remind_days=_whole(data, "remind_days"),
        last_done_on=_day(data, "last_done_on"),
        last_done_odometer=_amount(data, "last_done_odometer", places=1),
        next_due_on=_day(data, "next_due_on"),
        next_due_odometer=_amount(data, "next_due_odometer", places=1),
    )


RULE_KEYS = (
    "name",
    "system_work",
    "work_id",
    "interval_distance",
    "interval_days",
    "remind_distance",
    "remind_days",
    "last_done_on",
    "last_done_odometer",
    "next_due_on",
    "next_due_odometer",
)


def _rules_response(company_id: UUID, card_id: UUID) -> dict[str, Any]:
    card = fleet.get_card(company_id, card_id)
    return {
        "unit": card.odometer_unit,
        "odometer": catalog.amount_text(maintenance.odometer_of(card.id)),
        "rules": [_rule_payload(r, st) for r, st in maintenance.statuses(company_id, card)],
    }


@require_http_methods(["GET", "POST"])
def vehicle_rules(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """Reminders belong to the vehicle and are seen with the right to view vehicles, without the
    internal data of the service (9.1.13); rules are kept with «Maintenance rules» (§ 23.5.1,
    block 14). The initial last work of a new rule goes with the rule (PROPOSAL)."""
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        _require(actor, "vehicle.view")
        return JsonResponse(_rules_response(context.company.id, card_id))
    _require(actor, RULES)
    data = parse_json(request)
    rule_data = _rule_data(data)

    def handler(ctx: CommandContext) -> CommandResult:
        maintenance.create_rule(ctx, context.company.id, card_id, rule_data)
        return CommandResult(201, _rules_response(context.company.id, card_id))

    payload = {"vehicle_card_id": str(card_id), **_payload(data, *RULE_KEYS)}
    return run_and_respond(request, "maintenance_rule.create", actor, payload, handler)


RULES = "vehicle.maintenance.rules"
RECORD = "vehicle.maintenance.record"
# The fields of a rule that only «Record a done maintenance» changes (§ 23.5.1).
DONE_FIELDS = ("last_done_on", "last_done_odometer")


def _require_rule_change(actor: Actor, rule: Any, data: maintenance.RuleData | None) -> None:
    """§ 23.5.1 (block 14): the rule (intervals, warnings, archive) needs «Maintenance rules»;
    a new last done work, from which the countdown starts again, needs «Record a done
    maintenance». Checked on the locked rule."""
    if data is None:
        _require(actor, RULES)
        return
    stored = {"system_work": "system_work_id"}
    changed = {
        name for name in RULE_KEYS if getattr(data, name) != getattr(rule, stored.get(name, name))
    }
    if changed & set(DONE_FIELDS):
        _require(actor, RECORD)
    if changed - set(DONE_FIELDS):
        _require(actor, RULES)


@require_http_methods(["PATCH"])
def rule_detail(request: HttpRequest, cmp: str, rule_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if not any(access.RightsResolver().has_permission(actor, p) for p in (RULES, RECORD)):
        raise errors.permission_denied(RULES)
    data = parse_json(request)
    expected_version = get_int(data, "expected_version")
    archived = data.get("archived")
    if archived is not None and not isinstance(archived, bool):
        raise errors.validation_error("archived")
    rule_data = None if archived is not None else _rule_data(data)

    def handler(ctx: CommandContext) -> CommandResult:
        rule = maintenance.locked_rule(context.company.id, rule_id, expected_version)
        _require_rule_change(actor, rule, rule_data)
        if rule_data is not None:
            maintenance.change_rule(ctx, rule, rule_data)
        else:
            maintenance.set_archived(ctx, rule, bool(archived))
        return CommandResult(200, _rules_response(context.company.id, rule.vehicle_card_id))

    payload = {
        "rule_id": str(rule_id),
        "archived": archived,
        "expected_version": expected_version,
        **_payload(data, *RULE_KEYS),
    }
    return run_and_respond(request, "maintenance_rule.change", actor, payload, handler)


@require_POST
def vehicle_rules_copy(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """9.1.12: `choices` maps a matching source rule to "keep" or "replace"."""
    context, actor = _actor(request, cmp)
    _require(actor, RULES)
    data = parse_json(request)
    source = _required_uuid(data, "from_vehicle_id")
    raw = data.get("choices") or {}
    if not isinstance(raw, dict):
        raise errors.validation_error("choices")
    choices = {_required_uuid({"r": k}, "r"): str(v) for k, v in raw.items()}

    def handler(ctx: CommandContext) -> CommandResult:
        maintenance.copy_rules(ctx, context.company.id, source, card_id, choices)
        return CommandResult(200, _rules_response(context.company.id, card_id))

    payload = {
        "vehicle_card_id": str(card_id),
        "from_vehicle_id": str(source),
        "choices": {str(k): v for k, v in choices.items()},
    }
    return run_and_respond(request, "maintenance_rule.copy", actor, payload, handler)


@require_GET
def vehicle_history(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """What was done, when, at which odometer and by whom (9.1.2), with the right to view the
    service."""
    context, actor = _actor(request, cmp)
    _require(actor, "service.view")
    fleet.get_card(context.company.id, card_id)
    jobs = maintenance.done_jobs(context.company.id, card_id)
    names = staff.employee_names(context.company.id, {j.executor_id for j in jobs if j.executor_id})
    parties = counterparties.names(
        context.company.id, {j.contractor_id for j in jobs if j.contractor_id}
    )
    return JsonResponse(
        {
            "jobs": [
                {**_job_payload(j, names, parties), "operation_number": j.operation.number}
                for j in jobs
            ]
        }
    )


@require_GET
def maintenance_due(request: HttpRequest, cmp: str) -> JsonResponse:
    """Vehicles whose service is required or soon (9.1.10, 9.1.13)."""
    context, actor = _actor(request, cmp)
    _require(actor, "vehicle.view")
    items = [
        {
            **_rule_payload(rule, status),
            "vehicle_name": card.name,
            "unit": card.odometer_unit,
        }
        for card, rule, status in maintenance.due(context.company.id)
    ]
    return JsonResponse({"due": items})


# --- Block 15: planned materials, installed parts, contractor's materials ------------------------


def _parts_payload(op: ServiceOperation, job_ids: list[UUID]) -> dict[str, Any]:
    names = fleet.card_names(op.company_id, {op.vehicle_card_id})
    on_vehicle = work_issues_parts.of_vehicle(op.company_id, op.vehicle_card_id)
    own = materials.own_of_jobs(job_ids)
    return {
        # Parts installed on the vehicle now — for «Replaces» and for a removal (§ 11.4.3).
        "installed_parts": [
            work_issues_parts.payload(i, names) for i in on_vehicle if i.removed_at is None
        ],
        # Installations and removals made by the works of this operation.
        "installations": [
            work_issues_parts.payload(i, names)
            for i in on_vehicle
            if i.install_job_id in job_ids or i.removal_job_id in job_ids
        ],
        "own_materials": [
            {
                "id": str(m.id),
                "version": m.version,
                "job_id": str(m.job_id),
                "name": m.name,
                "quantity": catalog.amount_text(m.quantity),
                "unit": m.unit,
                "cost": catalog.amount_text(m.cost),
                "currency": m.currency_id,
                "comment": m.comment,
                "removed_at": _iso(m.removed_at),
                "remove_reason": m.remove_reason,
            }
            for m in own
        ],
    }


@require_POST
def material_reserve(request: HttpRequest, cmp: str, material_id: UUID) -> JsonResponse:
    """The manual reserve of a planned material (§ 11.7.7): «Reserve» or «Issue» there."""
    context, _ = _actor(request, cmp)

    def action(ctx: CommandContext | None, m: Any, data: dict[str, Any]) -> None:
        if ctx is None:
            if not m.planned:
                raise errors.business_rule("SERVICE_MATERIAL_NOT_PLANNED")
            _in_warehouse(
                data["_actor"],
                m.company_id,
                UUID(m.planned["context_id"]),
                "warehouse.reserve",
                "warehouse.issue",
            )
            return
        materials.reserve(ctx, context.company, m)

    return _material_command(request, cmp, material_id, "service_material.reserve", action)


@require_POST
def material_issue(request: HttpRequest, cmp: str, material_id: UUID) -> JsonResponse:
    """A planned material is issued to the work by «Issue» in the warehouse."""
    context, _ = _actor(request, cmp)

    def action(ctx: CommandContext | None, m: Any, data: dict[str, Any]) -> None:
        if ctx is None:
            if not m.planned:
                raise errors.business_rule("SERVICE_MATERIAL_NOT_PLANNED")
            _in_warehouse(
                data["_actor"], m.company_id, UUID(m.planned["context_id"]), "warehouse.issue"
            )
            return
        materials.issue(ctx, context.company, m)

    return _material_command(request, cmp, material_id, "service_material.issue", action)


@require_POST
def material_take_off(request: HttpRequest, cmp: str, material_id: UUID) -> JsonResponse:
    """A planned material off the work: its reserve is released (§ 11.7.1)."""

    def action(ctx: CommandContext | None, m: Any, data: dict[str, Any]) -> None:
        reason = get_str(data, "reason", max_length=TEXT_MAX, required=False)
        if ctx is None:
            _require(data["_actor"], "service.create", m.job.operation.location_id)
            return
        materials.take_off(ctx, m, reason)

    return _material_command(request, cmp, material_id, "service_material.take_off", action)


@require_POST
def material_replaces(request: HttpRequest, cmp: str, material_id: UUID) -> JsonResponse:
    """«Replaces the installed part» (§ 11.4.3); empty — no replacement."""

    def action(ctx: CommandContext | None, m: Any, data: dict[str, Any]) -> None:
        installation_id = _uuid(data, "replaces_id", required=False)
        if ctx is None:
            _require(data["_actor"], "service.create", m.job.operation.location_id)
            return
        materials.set_replaces(ctx, m, installation_id)

    return _material_command(request, cmp, material_id, "service_material.replaces", action)


@require_POST
def material_install(request: HttpRequest, cmp: str, material_id: UUID) -> JsonResponse:
    """The issued part is installed on the vehicle (§ 11.4.3): the moment, the odometer and
    optionally the part it replaces. PROPOSAL: by the right of doing works."""

    def action(ctx: CommandContext | None, m: Any, data: dict[str, Any]) -> None:
        at = _moment(data, "installed_at", required=True)
        odometer = _amount(data, "odometer", places=1)
        replaces_id = _uuid(data, "replaces_id", required=False)
        if ctx is None:
            _require(data["_actor"], "service.work", m.job.operation.location_id)
            return
        if at is None:
            raise errors.validation_error("installed_at", code="FIELD_REQUIRED")
        materials.install(ctx, m, at, odometer, replaces_id)

    return _material_command(request, cmp, material_id, "service_material.install", action)


@require_POST
def job_remove_part(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    """A part installed on the vehicle is removed by this work: the actual moment, the odometer
    and a comment (§ 11.4.3). PROPOSAL: by the right of doing works."""
    context, actor = _actor(request, cmp)
    job = operations.get_job(context.company.id, job_id)
    if not _visible(actor, job.operation):
        raise errors.not_found()
    _require(actor, "service.work", job.operation.location_id)
    data = parse_json(request)
    installation_id = _required_uuid(data, "installation_id")
    at = _moment(data, "removed_at", required=True)
    odometer = _amount(data, "odometer", places=1)
    comment = get_str(data, "comment", max_length=TEXT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        locked = operations.locked_job(
            context.company.id, job_id, get_int(data, "expected_version")
        )
        if at is None:
            raise errors.validation_error("removed_at", code="FIELD_REQUIRED")
        materials.remove_part(ctx, locked, installation_id, at, odometer, comment)
        op = operations.get_operation(context.company.id, locked.operation_id)
        return CommandResult(200, {"operation": _operation_payload(op)})

    payload = {
        "job_id": str(job_id),
        "installation_id": str(installation_id),
        "removed_at": at.isoformat() if at else None,
        "odometer": str(odometer) if odometer is not None else None,
        "comment": comment,
        "expected_version": data.get("expected_version"),
    }
    return run_and_respond(request, "service_job.remove_part", actor, payload, handler)


@require_POST
def job_own_materials(request: HttpRequest, cmp: str, job_id: UUID) -> JsonResponse:
    """§ 16.5.3: parts and materials of an external service from its own stock with their cost,
    without a receipt into the warehouse. PROPOSAL: «Change service operations»."""
    context, actor = _actor(request, cmp)
    job = operations.get_job(context.company.id, job_id)
    if not _visible(actor, job.operation):
        raise errors.not_found()
    _require(actor, "service.create", job.operation.location_id)
    data = parse_json(request)
    name = get_str(data, "name", max_length=NAME_MAX)
    quantity = _amount(data, "quantity", places=2, positive=True)
    if quantity is None:
        raise errors.validation_error("quantity", code="FIELD_REQUIRED")
    unit = get_str(data, "unit", max_length=20)
    cost = _amount(data, "cost")
    currency = get_str(data, "currency", max_length=3, required=False) or job.currency_id
    comment = get_str(data, "comment", max_length=TEXT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        locked = operations.locked_job(
            context.company.id, job_id, get_int(data, "expected_version")
        )
        materials.add_own(
            ctx,
            locked,
            name=name,
            quantity=quantity,
            unit=unit,
            cost=cost,
            currency=currency,
            comment=comment,
        )
        op = operations.get_operation(context.company.id, locked.operation_id)
        return CommandResult(201, {"operation": _operation_payload(op)})

    payload = {
        "job_id": str(job_id),
        "name": name,
        "quantity": str(quantity),
        "unit": unit,
        "cost": str(cost) if cost is not None else None,
        "currency": currency,
        "comment": comment,
        "expected_version": data.get("expected_version"),
    }
    return run_and_respond(request, "service_contractor_material.add", actor, payload, handler)


@require_POST
def own_material_remove(request: HttpRequest, cmp: str, material_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    found = (
        ContractorMaterial.objects.select_related("job__operation")
        .filter(company=context.company, pk=material_id)
        .first()
    )
    if found is None or not _visible(actor, found.job.operation):
        raise errors.not_found()
    _require(actor, "service.create", found.job.operation.location_id)
    data = parse_json(request)
    expected = get_int(data, "expected_version")
    reason = get_str(data, "reason", max_length=TEXT_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        operations.locked_operation(context.company.id, found.job.operation_id)
        materials.remove_own(ctx, context.company.id, material_id, expected, reason)
        op = operations.get_operation(context.company.id, found.job.operation_id)
        return CommandResult(200, {"operation": _operation_payload(op)})

    payload = {"material_id": str(material_id), "expected_version": expected, "reason": reason}
    return run_and_respond(request, "service_contractor_material.remove", actor, payload, handler)
