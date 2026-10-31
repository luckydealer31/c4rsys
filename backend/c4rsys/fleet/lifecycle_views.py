"""HTTP API of block 12, part Б: restrictions, archive and restore, relation and the end of the
management, write-off and disposal, the odometer unit and replacement, what is passed to the
owner, the global documents (ТЗ § 8.1.6, § 8.2.3–8.2.13, § 8.3.4–8.3.17, § 8.4.2, § 8.7.5–8.7.6,
§ 8.8, § 23.5)."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.companies import access
from c4rsys.fleet import (
    disclosure,
    lifecycle,
    odometer,
    owners,
    restrictions,
    services,
    vehicle_documents,
)
from c4rsys.fleet.models import VehicleCard
from c4rsys.fleet.views import _actor, _allowed, _payload
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json

REASON_MAX = 1000
COMMENT_MAX = 2000


def _card(request: HttpRequest, cmp: str, card_id: UUID) -> tuple[Any, Actor, VehicleCard]:
    context, actor = _actor(request, cmp)
    card = services.get_card(context.company.id, card_id)
    if not _allowed(actor, "vehicle.view", card):
        raise errors.not_found()
    return context, actor, card


def _require(actor: Actor, permission: str, card: VehicleCard) -> None:
    if not _allowed(actor, permission, card):
        raise errors.permission_denied(permission)


def _moment(data: dict[str, Any], key: str, *, required: bool = False) -> datetime | None:
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


def _uuid(value: Any, key: str, *, required: bool = True) -> UUID | None:
    if value in (None, ""):
        if required:
            raise errors.validation_error(key, code="FIELD_REQUIRED")
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(key) from exc


def _decimal(value: Any, key: str) -> Decimal:
    if value in (None, ""):
        raise errors.validation_error(key, code="FIELD_REQUIRED")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise errors.validation_error(key, code="ODOMETER_INVALID") from exc
    if not number.is_finite():
        raise errors.validation_error(key, code="ODOMETER_INVALID")
    return number.quantize(Decimal("0.1"))


def _refreshed(company_id: UUID, card_id: UUID) -> dict[str, Any]:
    return {"vehicle": _payload(services.get_card(company_id, card_id), detail=True)}


# --- Restrictions (§ 8.8, § 23.5.5) --------------------------------------------------------------


def _restriction_data(data: dict[str, Any]) -> restrictions.RestrictionData:
    contexts = data.get("forbidden_contexts") or []
    if not isinstance(contexts, list) or not all(isinstance(c, str) for c in contexts):
        raise errors.validation_error("forbidden_contexts")
    return restrictions.RestrictionData(
        reason=get_str(data, "reason", max_length=20),
        custom_reason_id=_uuid(data.get("custom_reason_id"), "custom_reason_id", required=False),
        comment=get_str(data, "comment", max_length=COMMENT_MAX, required=False),
        starts_at=_moment(data, "starts_at"),
        ends_at=_moment(data, "ends_at"),
        forbid_rental_issue=bool(data.get("forbid_rental_issue")),
        forbid_moves=bool(data.get("forbid_moves")),
        forbidden_contexts=tuple(contexts),
    )


@require_POST
def restriction_set(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.restriction.set", card)
    data = _restriction_data(parse_json(request))

    def handler(ctx: CommandContext) -> CommandResult:
        restrictions.set_restriction(ctx, context.company.id, card_id, data)
        return CommandResult(201, _refreshed(context.company.id, card_id))

    payload = {"card_id": str(card_id), **data.payload()}
    return services_run(request, "vehicle.restriction_set", actor, payload, handler)


@require_http_methods(["PUT"])
def restriction_change(
    request: HttpRequest, cmp: str, card_id: UUID, restriction_id: UUID
) -> JsonResponse:
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.restriction.change", card)
    body = parse_json(request)
    expected_version = get_int(body, "expected_version")
    data = _restriction_data(body)

    def handler(ctx: CommandContext) -> CommandResult:
        restrictions.change_restriction(
            ctx, context.company.id, card_id, restriction_id, data, expected_version
        )
        return CommandResult(200, _refreshed(context.company.id, card_id))

    payload = {
        "card_id": str(card_id),
        "restriction_id": str(restriction_id),
        "expected_version": expected_version,
        **data.payload(),
    }
    return services_run(request, "vehicle.restriction_change", actor, payload, handler)


@require_POST
def restriction_lift(
    request: HttpRequest, cmp: str, card_id: UUID, restriction_id: UUID
) -> JsonResponse:
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.restriction.lift", card)
    body = parse_json(request)
    expected_version = get_int(body, "expected_version")
    comment = get_str(body, "comment", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        restrictions.lift_restriction(
            ctx, context.company.id, card_id, restriction_id, comment, expected_version
        )
        return CommandResult(200, _refreshed(context.company.id, card_id))

    payload = {
        "card_id": str(card_id),
        "restriction_id": str(restriction_id),
        "expected_version": expected_version,
        "comment": comment,
    }
    return services_run(request, "vehicle.restriction_lift", actor, payload, handler)


@require_http_methods(["GET", "POST"])
def restriction_reasons(request: HttpRequest, cmp: str) -> JsonResponse:
    """The system list and the company's own reasons (§ 8.8.2, § 8.8.7)."""
    context, actor = _actor(request, cmp)
    resolver = access.RightsResolver()
    if request.method == "GET":
        if not resolver.has_permission(actor, "vehicle.view"):
            raise errors.permission_denied("vehicle.view")
        return JsonResponse(_reasons(context.company.id))
    # PROPOSAL: an employee who sets restrictions adds reasons.
    if not resolver.has_permission(actor, "vehicle.restriction.set"):
        raise errors.permission_denied("vehicle.restriction.set")
    name = get_str(parse_json(request), "name", max_length=200)

    def handler(ctx: CommandContext) -> CommandResult:
        restrictions.add_reason(ctx, context.company.id, name)
        return CommandResult(201, _reasons(context.company.id))

    return services_run(request, "vehicle.restriction_reason_add", actor, {"name": name}, handler)


def _reasons(company_id: UUID) -> dict[str, Any]:
    return {
        "system": restrictions.SYSTEM_REASONS,
        "company": [{"id": str(r.id), "name": r.name} for r in restrictions.reasons_of(company_id)],
        "contexts": sorted(services.USAGE_CONTEXT_TYPES),
    }


# --- Archive, restore, relation (§ 8.7.5–8.7.6, § 8.1.6, § 8.3.17) ------------------------------


@require_POST
def archive(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.archive", card)
    body = parse_json(request)
    expected_version = get_int(body, "expected_version")
    reason = get_str(body, "reason", max_length=REASON_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        lifecycle.archive(ctx, context.company.id, card_id, reason, expected_version)
        return CommandResult(200, _refreshed(context.company.id, card_id))

    payload = {"card_id": str(card_id), "reason": reason, "expected_version": expected_version}
    return services_run(request, "vehicle.archive", actor, payload, handler)


@require_POST
def restore(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """The right counts in the location the card comes back to (anywhere for a client card)."""
    context, actor, card = _card(request, cmp, card_id)
    body = parse_json(request)
    expected_version = get_int(body, "expected_version")
    relation = get_str(body, "relation", max_length=10)
    location_id = _uuid(body.get("location_id"), "location_id", required=relation != "client")
    if not access.RightsResolver().has_permission(
        actor, "vehicle.archive", location_id=location_id
    ):
        raise errors.permission_denied("vehicle.archive")
    owner = owners.parse(body, relation if relation in VehicleCard.Relation.values else "own")
    reason = get_str(body, "reason", max_length=REASON_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        lifecycle.restore(
            ctx,
            context.company,
            card_id,
            relation=relation,
            location_id=location_id,
            owner=owner,
            reason=reason,
            expected_version=expected_version,
        )
        return CommandResult(200, _refreshed(context.company.id, card_id))

    payload = {
        "card_id": str(card_id),
        "relation": relation,
        "location_id": str(location_id) if location_id else None,
        "owner": owner.payload(),
        "reason": reason,
        "expected_version": expected_version,
    }
    return services_run(request, "vehicle.restore", actor, payload, handler)


@require_POST
def end_management(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.relation", card)
    body = parse_json(request)
    expected_version = get_int(body, "expected_version")
    reason = get_str(body, "reason", max_length=REASON_MAX)
    then = get_str(body, "then", max_length=10, required=False) or "archive"

    def handler(ctx: CommandContext) -> CommandResult:
        lifecycle.end_management(
            ctx,
            context.company.id,
            card_id,
            reason=reason,
            then=then,
            expected_version=expected_version,
        )
        return CommandResult(200, _refreshed(context.company.id, card_id))

    payload = {
        "card_id": str(card_id),
        "reason": reason,
        "then": then,
        "expected_version": expected_version,
    }
    return services_run(request, "vehicle.end_management", actor, payload, handler)


@require_POST
def take_into_management(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, actor, card = _card(request, cmp, card_id)
    body = parse_json(request)
    location_id = _uuid(body.get("location_id"), "location_id")
    if location_id is None or not access.RightsResolver().has_permission(
        actor, "vehicle.relation", location_id=location_id
    ):
        raise errors.permission_denied("vehicle.relation")
    expected_version = get_int(body, "expected_version")
    reason = get_str(body, "reason", max_length=REASON_MAX)
    owner = owners.parse(body, VehicleCard.Relation.MANAGED)

    def handler(ctx: CommandContext) -> CommandResult:
        lifecycle.take_into_management(
            ctx,
            context.company,
            card_id,
            location_id=location_id,
            owner=owner,
            reason=reason,
            expected_version=expected_version,
        )
        return CommandResult(200, _refreshed(context.company.id, card_id))

    payload = {
        "card_id": str(card_id),
        "location_id": str(location_id),
        "owner": owner.payload(),
        "reason": reason,
        "expected_version": expected_version,
    }
    return services_run(request, "vehicle.take_into_management", actor, payload, handler)


@require_POST
def dispose(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """Write-off or disposal from the warehouse with a special right (§ 8.4.2)."""
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.dispose", card)
    body = parse_json(request)
    expected_version = get_int(body, "expected_version")
    kind = get_str(body, "kind", max_length=10)
    reason = get_str(body, "reason", max_length=REASON_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        lifecycle.dispose(
            ctx,
            context.company.id,
            card_id,
            kind=kind,
            reason=reason,
            expected_version=expected_version,
        )
        return CommandResult(200, _refreshed(context.company.id, card_id))

    payload = {
        "card_id": str(card_id),
        "kind": kind,
        "reason": reason,
        "expected_version": expected_version,
    }
    return services_run(request, "vehicle.dispose", actor, payload, handler)


@require_POST
def transfer_reversal(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """The seller's request to the new owner (§ 8.3.9)."""
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.ownership_reversal", card)
    reason = get_str(parse_json(request), "reason", max_length=REASON_MAX)

    def handler(ctx: CommandContext) -> CommandResult:
        lifecycle.request_reversal(ctx, context.company.id, card_id, reason)
        return CommandResult(201, _refreshed(context.company.id, card_id))

    payload = {"card_id": str(card_id), "reason": reason}
    return services_run(request, "vehicle.transfer_reversal", actor, payload, handler)


# --- Odometer (§ 8.2.10, § 8.2.13) ---------------------------------------------------------------


@require_GET
def unit_preview(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    _context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.odometer_unit", card)
    return JsonResponse(
        {
            "preview": odometer.preview_unit_change(
                card, request.GET.get("to_unit", ""), request.GET.get("method", "")
            )
        }
    )


@require_POST
def unit_change(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.odometer_unit", card)
    body = parse_json(request)
    expected_version = get_int(body, "expected_version")
    to_unit = get_str(body, "to_unit", max_length=2)
    method = get_str(body, "method", max_length=10)
    reason = get_str(body, "reason", max_length=COMMENT_MAX, required=False)

    def handler(ctx: CommandContext) -> CommandResult:
        odometer.change_unit(
            ctx,
            context.company.id,
            card_id,
            to_unit=to_unit,
            method=method,
            reason=reason,
            expected_version=expected_version,
        )
        return CommandResult(200, _refreshed(context.company.id, card_id))

    payload = {
        "card_id": str(card_id),
        "to_unit": to_unit,
        "method": method,
        "reason": reason,
        "expected_version": expected_version,
    }
    return services_run(request, "vehicle.odometer_unit", actor, payload, handler)


@require_GET
def replacement_preview(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    """The accumulated mileage the replacement gives, shown before it is saved (§ 8.2.13)."""
    _context, _actor_, card = _card(request, cmp, card_id)
    at = _moment(dict(request.GET.items()), "at", required=True)
    if at is None:
        raise errors.validation_error("at", code="FIELD_REQUIRED")
    return JsonResponse(
        {
            "preview": odometer.preview_replacement(
                card,
                at,
                _decimal(request.GET.get("old_final"), "old_final"),
                _decimal(request.GET.get("new_start"), "new_start"),
            )
        }
    )


# --- Disclosure to the owner (§ 8.3.7, § 8.3.15) -------------------------------------------------


@require_http_methods(["PUT"])
def disclosure_set(request: HttpRequest, cmp: str, card_id: UUID) -> JsonResponse:
    context, actor, card = _card(request, cmp, card_id)
    _require(actor, "vehicle.disclosure", card)
    body = parse_json(request)
    categories = body.get("categories")
    if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
        raise errors.validation_error("categories")
    effective_from = _moment(body, "effective_from")

    def handler(ctx: CommandContext) -> CommandResult:
        disclosure.set_categories(ctx, context.company.id, card_id, categories, effective_from)
        return CommandResult(200, _refreshed(context.company.id, card_id))

    payload = {
        "card_id": str(card_id),
        "categories": sorted(categories),
        "effective_from": effective_from.isoformat() if effective_from else None,
    }
    return services_run(request, "vehicle.disclosure", actor, payload, handler)


# --- Global documents (§ 8.2.3) ------------------------------------------------------------------


@require_GET
def global_document_file(
    request: HttpRequest, cmp: str, card_id: UUID, version_id: UUID
) -> HttpResponse:
    from c4rsys.files import services as files
    from c4rsys.files.models import StoredFile

    _context, _actor_, card = _card(request, cmp, card_id)
    file_id = vehicle_documents.version_file(card, version_id)
    item = StoredFile.objects.filter(pk=file_id, state__in=files.AVAILABLE).first()
    if item is None:
        raise errors.not_found()
    return files.file_response(item)


def services_run(
    request: HttpRequest,
    command: str,
    actor: Actor,
    payload: dict[str, Any],
    handler: Any,
) -> JsonResponse:
    from c4rsys.companies.views import run_and_respond

    return run_and_respond(request, command, actor, payload, handler)
