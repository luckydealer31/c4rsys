"""HTTP API of inspections, their templates and policies, damages (ТЗ гл. 10, § 23.4.1–23.4.2).

Rights are checked in the accounting location of the vehicle (§ 23.4.1: «in the assigned area,
whether the inspection is linked with a rental, a service or made on its own»):
* creating a draft or a planned inspection and changing it — «Create inspections»; starting and
  saving it — «Conduct inspections»; cancelling a planned one, terminating a started one and
  recognising a saved one invalid — their own rights (§ 23.4.1–23.4.2);
* the policies and the templates — «Manage inspection policies» (§ 23.3.7);
* TODO(spec): there is no separate right to view inspections. PROPOSAL: they are seen with any
  right of inspections or «View vehicles» in the vehicle's location, and those of a rental or a
  service also with «View rentals» or «View service» (§ 10.3.7: «within the rights of access to
  this history»).
* TODO(spec): who records the reason of a missing required inspection is not stated. PROPOSAL:
  «Complete the formalization of a rental», since the reason is needed to close the rental.
* TODO(spec): who gives the estimate of a damage is not stated. PROPOSAL: in a rental — «Manual
  charges of a rental» (it is a charge of the client, § 13.5.1); outside — «Conduct inspections».
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies import history
from c4rsys.companies import services as companies
from c4rsys.companies.views import run_and_respond
from c4rsys.fleet.models import VehicleCard
from c4rsys.inspections import damages, links, policy, services, templates
from c4rsys.inspections.access import VIEW_RIGHTS, _has, _location, can_view
from c4rsys.inspections.models import Damage, Inspection
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json

POLICY = "inspection.policy.manage"


def _actor(request: HttpRequest, cmp: str) -> tuple[companies.CompanyContext, Actor]:
    user, session = require_user(request)
    context = companies.resolve_company(user, cmp, session.id)
    return context, companies.company_actor(user, context, session.id)


def _require(actor: Actor, permission: str, location_id: UUID | None = None) -> None:
    if not _has(actor, permission, location_id):
        raise errors.permission_denied(permission)


def _card_visible(actor: Actor, card: VehicleCard) -> bool:
    location_id = card.accounting_location_id
    return any(_has(actor, p, location_id) for p in (*VIEW_RIGHTS, "rental.view", "service.view"))


def _inspection(company_id: UUID, inspection_id: UUID, actor: Actor) -> Inspection:
    found = (
        Inspection.objects.select_related("vehicle_card", "template_version")
        .filter(company_id=company_id, pk=inspection_id)
        .first()
    )
    if found is None or not can_view(actor, found):
        raise errors.not_found()
    return found


def _card(company_id: UUID, card_id: UUID, actor: Actor) -> VehicleCard:
    card = VehicleCard.objects.filter(company_id=company_id, pk=card_id).first()
    if card is None or not _card_visible(actor, card):
        raise errors.not_found()
    return card


# --- Parsing -------------------------------------------------------------------------------------


def _uuid(data: dict[str, Any], key: str) -> UUID | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _moment(data: dict[str, Any], key: str) -> datetime | None:
    value = data.get(key)
    if value in (None, ""):
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


def _number(
    data: dict[str, Any], key: str, places: int, low: Decimal | None = None
) -> Decimal | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise errors.validation_error(key, code="NUMBER_INVALID")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise errors.validation_error(key, code="NUMBER_INVALID") from exc
    exponent = number.as_tuple().exponent
    if (
        not number.is_finite()
        or (low is not None and number < low)
        or (isinstance(exponent, int) and -exponent > places)
        or abs(number) >= Decimal(10) ** 10
    ):
        raise errors.validation_error(key, code="NUMBER_INVALID", max_places=places)
    return number


def _files(data: dict[str, Any]) -> list[services.AttachedFile]:
    raw = data.get("files") or []
    if not isinstance(raw, list) or len(raw) > 200:
        raise errors.validation_error("files")
    result = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise errors.validation_error(f"files[{index}]")
        file_id = _uuid(item, "file_id")
        if file_id is None:
            raise errors.validation_error(f"files[{index}].file_id", code="FIELD_REQUIRED")
        result.append(
            services.AttachedFile(
                file_id=file_id,
                kind=get_str(item, "kind", max_length=10),
                position=get_str(item, "position", max_length=60, required=False),
            )
        )
    return result


def _content(data: dict[str, Any], application: str) -> services.Content:
    return services.Content(
        performed_at=_moment(data, "performed_at"),
        place=services.Place(
            kind=get_str(data, "place_kind", max_length=10, required=False)
            or Inspection.PlaceKind.LOCATION,
            location_id=_uuid(data, "place_location_id"),
            note=get_str(data, "place_note", max_length=500, required=False),
            address=get_str(data, "place_address", max_length=500, required=False),
            lat=_number(data, "place_lat", 6, Decimal(-90)),
            lng=_number(data, "place_lng", 6, Decimal(-180)),
        ),
        participant=services.Participant(
            kind=get_str(data, "participant_kind", max_length=12, required=False),
            client_id=_uuid(data, "participant_client_id"),
            counterparty_id=_uuid(data, "participant_counterparty_id"),
            employment_id=_uuid(data, "participant_employment_id"),
            text=get_str(data, "participant_text", max_length=300, required=False),
        ),
        performer_employment_id=_uuid(data, "performer_employment_id"),
        odometer=_number(data, "odometer", 1, Decimal(0)),
        fuel=_number(data, "fuel", 2, Decimal(0)),
        cleanliness=get_str(data, "cleanliness", max_length=200, required=False),
        damages=damages.parse_entries(data.get("damages"), application),
        files=_files(data),
    )


def _result(inspection: Inspection, status: int = 200) -> CommandResult:
    fresh = Inspection.objects.select_related("template_version").get(pk=inspection.pk)
    return CommandResult(status, {"inspection": services.payload(fresh)})


# --- Inspections ---------------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def inspection_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        query = dict(request.GET.items())
        items = Inspection.objects.select_related("vehicle_card").filter(
            company_id=context.company.id
        )
        for key, name in (
            ("vehicle_card_id", "vehicle_card_id"),
            ("rental_id", "rental_id"),
            ("service_operation_id", "service_operation_id"),
        ):
            value = _uuid(query, key)
            if value is not None:
                items = items.filter(**{name: value})
        if query.get("state"):
            items = items.filter(state=query["state"])
        found = [
            services.payload(i, detail=False)
            for i in items.order_by("-performed_at", "-entered_at")[:200]
            if can_view(actor, i)
        ]
        return JsonResponse({"inspections": found})
    data = parse_json(request)
    card_id = _uuid(data, "vehicle_card_id")
    if card_id is None:
        raise errors.validation_error("vehicle_card_id", code="FIELD_REQUIRED")
    card = VehicleCard.objects.filter(company_id=context.company.id, pk=card_id).first()
    if card is None:
        raise errors.not_found()
    _require(actor, "inspection.create", card.accounting_location_id)
    application = get_str(data, "application", max_length=10)
    new = services.NewInspection(
        vehicle_card_id=card.id,
        kind=get_str(data, "kind", max_length=20),
        application=application,
        rental_id=_uuid(data, "rental_id"),
        service_operation_id=_uuid(data, "service_operation_id"),
        template_id=_uuid(data, "template_id"),
        planned_at=_moment(data, "planned_at"),
        content=_content(data, application),
        client_draft_id=_uuid(data, "client_draft_id"),
        duplicates_confirmed=data.get("duplicates_confirmed") is True,
    )

    def handler(ctx: CommandContext) -> CommandResult:
        return _result(services.create(ctx, context.company.id, new), 201)

    return run_and_respond(request, "inspection.create", actor, data, handler)


@require_http_methods(["GET", "PATCH", "DELETE"])
def inspection_detail(request: HttpRequest, cmp: str, inspection_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    inspection = _inspection(context.company.id, inspection_id, actor)
    if request.method == "GET":
        return JsonResponse({"inspection": services.payload(inspection)})
    data = parse_json(request)
    expected = get_int(data, "expected_version")
    _require(actor, "inspection.create", _location(inspection))
    if request.method == "DELETE":

        def remove(ctx: CommandContext) -> CommandResult:
            services.delete_draft(ctx, services.locked(context.company.id, inspection_id, expected))
            return CommandResult(200, {"deleted": str(inspection_id)})

        payload = {"inspection_id": str(inspection_id), "expected_version": expected}
        return run_and_respond(request, "inspection.delete", actor, payload, remove)
    content = _content(data, inspection.application)

    def handler(ctx: CommandContext) -> CommandResult:
        locked = services.locked(context.company.id, inspection_id, expected)
        return _result(services.update(ctx, locked, content))

    return run_and_respond(
        request, "inspection.update", actor, {"inspection_id": str(inspection_id), **data}, handler
    )


def _action(
    request: HttpRequest,
    cmp: str,
    inspection_id: UUID,
    permission: str,
    command: str,
    run: Any,
) -> JsonResponse:
    context, actor = _actor(request, cmp)
    inspection = _inspection(context.company.id, inspection_id, actor)
    _require(actor, permission, _location(inspection))
    data = parse_json(request)
    expected = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        locked = services.locked(context.company.id, inspection_id, expected)
        return _result(run(ctx, locked, data))

    return run_and_respond(
        request, command, actor, {"inspection_id": str(inspection_id), **data}, handler
    )


def _reason(data: dict[str, Any]) -> str:
    return get_str(data, "reason", max_length=2000)


@require_POST
def inspection_start(request: HttpRequest, cmp: str, inspection_id: UUID) -> JsonResponse:
    return _action(
        request,
        cmp,
        inspection_id,
        "inspection.conduct",
        "inspection.start",
        lambda ctx, i, data: services.start(ctx, i),
    )


@require_POST
def inspection_complete(request: HttpRequest, cmp: str, inspection_id: UUID) -> JsonResponse:
    """Saves the inspection; `conducted` — the condition check was done (§ 10.1.5)."""

    def run(ctx: CommandContext, inspection: Inspection, data: dict[str, Any]) -> Inspection:
        conducted = data.get("conducted", False)
        if not isinstance(conducted, bool):
            raise errors.validation_error("conducted")
        return services.complete(ctx, inspection, conducted)

    return _action(request, cmp, inspection_id, "inspection.conduct", "inspection.complete", run)


@require_POST
def inspection_cancel(request: HttpRequest, cmp: str, inspection_id: UUID) -> JsonResponse:
    return _action(
        request,
        cmp,
        inspection_id,
        "inspection.cancel",
        "inspection.cancel",
        lambda ctx, i, data: services.cancel(ctx, i, _reason(data)),
    )


@require_POST
def inspection_terminate(request: HttpRequest, cmp: str, inspection_id: UUID) -> JsonResponse:
    return _action(
        request,
        cmp,
        inspection_id,
        "inspection.terminate",
        "inspection.terminate",
        lambda ctx, i, data: services.terminate(ctx, i, _reason(data)),
    )


@require_POST
def inspection_invalidate(request: HttpRequest, cmp: str, inspection_id: UUID) -> JsonResponse:
    return _action(
        request,
        cmp,
        inspection_id,
        "inspection.invalidate",
        "inspection.invalidate",
        lambda ctx, i, data: services.invalidate(ctx, i, _reason(data)),
    )


@require_GET
def inspection_history(request: HttpRequest, cmp: str, inspection_id: UUID) -> JsonResponse:
    """The journal of the inspection and of the damages it recorded (§ 10.6.3: all states,
    reasons, authors and moments are kept)."""
    context, actor = _actor(request, cmp)
    inspection = _inspection(context.company.id, inspection_id, actor)
    objects = [("inspection", str(inspection.id))] + [
        ("damage", str(d))
        for d in Damage.objects.filter(inspection=inspection).values_list("id", flat=True)
    ]
    events = history.object_history(context.company.id, objects)
    return JsonResponse({"history": history.payload(events)})


@require_GET
def vehicle_inspections(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """Previous inspections of the vehicle with their photos, of earlier rentals and services
    too, within the rights to see them (§ 10.3.7)."""
    context, actor = _actor(request, cmp)
    card = _card(context.company.id, card_id, actor)
    items = [
        services.payload(i, detail=False)
        for i in services.of_vehicle(context.company.id, card.id)
        if i.state in (Inspection.State.SAVED, Inspection.State.INVALID) and can_view(actor, i)
    ]
    return JsonResponse({"inspections": items})


# --- A required check of a rental (§ 10.3.3) ----------------------------------------------------


@require_http_methods(["GET", "POST"])
def rental_missing(request: HttpRequest, cmp: str, rental_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    rental = links.rental(context.company.id, rental_id)
    if rental is None:
        raise errors.not_found()
    location_id = rental.return_location_id or rental.issue_location_id
    if request.method == "GET":
        if not _has(actor, "rental.view", location_id):
            raise errors.not_found()
        return JsonResponse(
            {
                "conflicts": services.check_conflicts(rental.id),
                "reasons": services.missing_reasons(rental.id),
            }
        )
    _require(actor, "rental.complete_formalization", location_id)
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=20)
    reason = _reason(data)

    def handler(ctx: CommandContext) -> CommandResult:
        services.record_missing_reason(ctx, context.company.id, rental.id, kind, reason)
        return CommandResult(
            200,
            {
                "conflicts": services.check_conflicts(rental.id),
                "reasons": services.missing_reasons(rental.id),
            },
        )

    payload = {"rental_id": str(rental.id), "kind": kind, "reason": reason}
    return run_and_respond(request, "inspection.missing_reason", actor, payload, handler)


# --- Damages (§ 10.5) ----------------------------------------------------------------------------


def _damage(company_id: UUID, damage_id: UUID, actor: Actor) -> Damage:
    found = Damage.objects.select_related("vehicle_card").filter(
        company_id=company_id, pk=damage_id
    )
    damage = found.first()
    if damage is None or not _card_visible(actor, damage.vehicle_card):
        raise errors.not_found()
    return damage


@require_GET
def vehicle_damages(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """The damages of the vehicle with their states and history (§ 10.5.2)."""
    context, actor = _actor(request, cmp)
    card = _card(context.company.id, card_id, actor)
    items = [damages.payload(d) for d in damages.of_vehicle(context.company.id, card.id)]
    return JsonResponse({"damages": items})


@require_GET
def damage_detail(request: HttpRequest, cmp: str, damage_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    return JsonResponse({"damage": damages.payload(_damage(context.company.id, damage_id, actor))})


@require_POST
def damage_estimate(request: HttpRequest, cmp: str, damage_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    damage = _damage(context.company.id, damage_id, actor)
    permission = "rental.manual_charge" if damage.attributed_to_rental else "inspection.conduct"
    _require(actor, permission, damage.vehicle_card.accounting_location_id)
    data = parse_json(request)
    expected = get_int(data, "expected_version")
    estimate = damages.parse_estimate(data.get("estimate"), "estimate")

    def handler(ctx: CommandContext) -> CommandResult:
        locked = damages.locked(context.company.id, damage_id, expected)
        return CommandResult(
            200, {"damage": damages.payload(damages.set_estimate(ctx, locked, estimate))}
        )

    payload = {"damage_id": str(damage_id), **data}
    return run_and_respond(request, "damage.estimate", actor, payload, handler)


# --- Templates and policies (§ 10.3.1, § 10.3.5) -------------------------------------------------


@require_GET
def template_list(request: HttpRequest, cmp: str) -> JsonResponse:
    context, _actor_ = _actor(request, cmp)
    return JsonResponse(
        {"templates": [templates.payload(t) for t in templates.templates_of(context.company.id)]}
    )


@require_http_methods(["GET", "PUT"])
def template_detail(request: HttpRequest, cmp: str, template_id: UUID) -> JsonResponse:
    context, actor = _actor(request, cmp)
    found = next(
        (t for t in templates.templates_of(context.company.id) if t.id == template_id), None
    )
    if found is None:
        raise errors.not_found()
    if request.method == "GET":
        return JsonResponse({"template": templates.payload(found)})
    _require(actor, POLICY)
    data = parse_json(request)
    expected = get_int(data, "expected_version")

    def handler(ctx: CommandContext) -> CommandResult:
        changed = templates.change(
            ctx, context.company.id, template_id, data.get("positions"), expected
        )
        return CommandResult(200, {"template": templates.payload(changed)})

    payload = {"template_id": str(template_id), **data}
    return run_and_respond(request, "inspection.template", actor, payload, handler)


@require_http_methods(["GET", "PUT"])
def policy_detail(request: HttpRequest, cmp: str) -> JsonResponse:
    """Required readings and check by kind and application; the check of an issue and a return
    of a rental is set in the issue policy (§ 14.3.6)."""
    context, actor = _actor(request, cmp)
    if request.method == "GET":
        return JsonResponse({"policies": policy.payload(context.company.id)})
    _require(actor, POLICY)
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=20)
    application = get_str(data, "application", max_length=10)

    def handler(ctx: CommandContext) -> CommandResult:
        policy.set_policy(ctx, context.company.id, kind, application, data)
        return CommandResult(200, {"policies": policy.payload(context.company.id)})

    return run_and_respond(request, "inspection.policy", actor, data, handler)
