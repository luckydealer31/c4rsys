"""Internal API of block 12 part А: conflicts of vehicle identifiers (ТЗ § 8.6.1–8.6.2,
§ 24.1.2, § 24.1.5), confirmed characteristics and the decisions on companies' proposals
(§ 8.2.1), the catalog of makes and models (§ 8.1.11) and the correction of a confirmed period
of ownership (§ 8.3.16).

Reads are journaled before the answer; commands go through `run_staff` (one transaction, the
system journal, the Idempotency-Key). The staff never edits business records of a company
(§ 24.1.4): a decision changes system links and global values only.
"""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.backoffice.account_views import _ticket
from c4rsys.backoffice.core import StaffContext, logged_read, require_staff, run_staff
from c4rsys.backoffice.views import REASON_MAX, TEXT_MAX, _moment
from c4rsys.fleet import confirmations, identity
from c4rsys.fleet.models import VehicleMake, VehicleOwnership
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandResult
from c4rsys.platform.http import get_str, parse_json

VERIFY = "verification.resolve"
CHARACTERISTICS = "vehicles.characteristics"
CATALOG = "vehicles.catalog"


# --- Conflicts of identifiers (§ 8.6.1–8.6.2) ---------------------------------------------------


@require_GET
def conflict_list(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, VERIFY)
    state = "closed" if request.GET.get("state") == "closed" else "open"
    found = identity.conflicts(state)
    logged_read(
        request,
        staff,
        "verification.conflicts_viewed",
        object_type="vehicle_identifier_conflict",
        object_id=state,
        after={"count": len(found)},
    )
    return JsonResponse({"conflicts": [identity.staff_payload(c) for c in found]})


@require_GET
def conflict_detail(request: HttpRequest, conflict_id: UUID) -> JsonResponse:
    staff = require_staff(request, VERIFY)
    conflict = identity.get(conflict_id)
    logged_read(
        request,
        staff,
        "verification.conflict_viewed",
        object_type="vehicle_identifier_conflict",
        object_id=conflict.id,
        after={"number": conflict.number},
    )
    return JsonResponse({"conflict": identity.staff_payload(conflict, detail=True)})


@require_POST
def conflict_decide(request: HttpRequest, conflict_id: UUID) -> JsonResponse:
    staff = require_staff(request, VERIFY)
    data = parse_json(request)
    outcome = get_str(data, "outcome", max_length=20)
    reason = get_str(data, "reason", max_length=REASON_MAX)
    evidence = get_str(data, "evidence", max_length=TEXT_MAX, required=False)
    primary = get_str(data, "primary", max_length=40, required=False)
    ticket = _ticket(data)

    def handler(ctx: StaffContext) -> CommandResult:
        decision, change = identity.decide(
            conflict_id,
            outcome=outcome,
            reason=reason,
            evidence=evidence,
            primary=primary or None,
            by_user_id=ctx.staff.user.id,
            ticket_id=ticket.id if ticket else None,
            now=timezone.now(),
        )
        ctx.journal(
            "verification.identifier_conflict_decided",
            object_type="vehicle_identifier_conflict",
            object_id=conflict_id,
            ticket_id=ticket.id if ticket else None,
            reason=reason,
            before=change["before"],
            after=change["after"],
        )
        return CommandResult(
            200, {"conflict": identity.staff_payload(decision.conflict, detail=True)}
        )

    payload = {
        "conflict": str(conflict_id),
        "outcome": outcome,
        "reason": reason,
        "evidence": evidence,
        "primary": primary,
        "ticket_number": data.get("ticket_number"),
    }
    return run_staff(request, "verification.identifier_conflict", staff, payload, handler)


# --- The global vehicle (§ 8.2.1, § 8.3.16) -----------------------------------------------------


def _vehicle_reader(request: HttpRequest) -> Any:
    staff = require_staff(request)
    if not (staff.can(VERIFY) or staff.can(CHARACTERISTICS)):
        raise errors.permission_denied(CHARACTERISTICS)
    return staff


@require_GET
def vehicle_search(request: HttpRequest) -> JsonResponse:
    staff = _vehicle_reader(request)
    query = request.GET.get("q", "")[:60]
    found = confirmations.search(query)
    logged_read(
        request,
        staff,
        "vehicle.searched",
        object_type="vehicle",
        object_id="search",
        after={"query": query, "count": len(found)},
    )
    return JsonResponse(
        {
            "vehicles": [
                {
                    "vehicle_id": v.public_id,
                    "vin": v.vin,
                    "merged": v.merged_into_id is not None,
                }
                for v in found
            ]
        }
    )


@require_GET
def vehicle_detail(request: HttpRequest, vhl: str) -> JsonResponse:
    staff = _vehicle_reader(request)
    vehicle = confirmations.find_vehicle(vhl)
    logged_read(
        request, staff, "vehicle.viewed", object_type="vehicle", object_id=vehicle.public_id
    )
    return JsonResponse({"vehicle": confirmations.vehicle_payload(vehicle)})


@require_POST
def vehicle_confirm(request: HttpRequest, vhl: str) -> JsonResponse:
    """Confirms factory characteristics, setting the value where given (§ 8.2.1)."""
    staff = require_staff(request, CHARACTERISTICS)
    data = parse_json(request)
    items = data.get("items")
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise errors.validation_error("items")
    reason = get_str(data, "reason", max_length=REASON_MAX)
    ticket = _ticket(data)

    def handler(ctx: StaffContext) -> CommandResult:
        vehicle, changes = confirmations.confirm(
            vhl,
            items=items,
            reason=reason,
            by_user_id=ctx.staff.user.id,
            ticket_id=ticket.id if ticket else None,
            now=timezone.now(),
        )
        ctx.journal(
            "vehicle.characteristics_confirmed",
            object_type="vehicle",
            object_id=vehicle.public_id,
            ticket_id=ticket.id if ticket else None,
            reason=reason,
            before={c["field"]: c["before"] for c in changes},
            after={c["field"]: c["after"] for c in changes},
        )
        return CommandResult(200, {"vehicle": confirmations.vehicle_payload(vehicle)})

    payload = {
        "vehicle": vhl,
        "items": items,
        "reason": reason,
        "ticket": data.get("ticket_number"),
    }
    return run_staff(request, "vehicle.confirm", staff, payload, handler)


@require_GET
def proposal_list(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, CHARACTERISTICS)
    found = confirmations.open_proposals()
    logged_read(
        request,
        staff,
        "vehicle.proposals_viewed",
        object_type="vehicle_proposal",
        object_id="open",
        after={"count": len(found)},
    )
    return JsonResponse(
        {"proposals": [confirmations.proposal_payload(p, staff=True) for p in found]}
    )


@require_POST
def proposal_decide(request: HttpRequest, proposal_id: UUID) -> JsonResponse:
    staff = require_staff(request, CHARACTERISTICS)
    data = parse_json(request)
    accept = data.get("accept")
    if not isinstance(accept, bool):
        raise errors.validation_error("accept")
    reason = get_str(data, "reason", max_length=REASON_MAX)
    ticket = _ticket(data)

    def handler(ctx: StaffContext) -> CommandResult:
        proposal, change = confirmations.decide_proposal(
            proposal_id,
            accept=accept,
            reason=reason,
            by_user_id=ctx.staff.user.id,
            ticket_id=ticket.id if ticket else None,
            now=timezone.now(),
        )
        ctx.journal(
            "vehicle.proposal_decided",
            object_type="vehicle",
            object_id=change["vehicle"],
            ticket_id=ticket.id if ticket else None,
            reason=reason,
            before={"field": proposal.field, **change["before"]},
            after={"field": proposal.field, **change["after"]},
        )
        return CommandResult(
            200, {"proposal": confirmations.proposal_payload(proposal, staff=True)}
        )

    payload = {"proposal": str(proposal_id), "accept": accept, "reason": reason}
    return run_staff(request, "vehicle.proposal_decide", staff, payload, handler)


@require_POST
def ownership_correct(request: HttpRequest, vhl: str, ownership_id: UUID) -> JsonResponse:
    """A new version of a confirmed period of ownership (§ 8.3.16)."""
    staff = require_staff(request, VERIFY)
    vehicle = confirmations.find_vehicle(vhl)
    if not VehicleOwnership.objects.filter(pk=ownership_id, vehicle=vehicle).exists():
        raise errors.not_found()
    data = parse_json(request)
    started_at = _moment(data, "started_at")
    if started_at is None:
        raise errors.validation_error("started_at", code="FIELD_REQUIRED")
    ended_at = _moment(data, "ended_at")
    initiator = get_str(data, "initiator", max_length=300)
    reason = get_str(data, "reason", max_length=REASON_MAX)
    evidence = get_str(data, "evidence", max_length=TEXT_MAX, required=False)
    started_approx = data.get("started_approx") is True
    ended_approx = data.get("ended_approx") is True
    ticket = _ticket(data)

    def handler(ctx: StaffContext) -> CommandResult:
        revision, change = confirmations.correct_ownership(
            ownership_id,
            started_at=started_at,
            ended_at=ended_at,
            started_approx=started_approx,
            ended_approx=ended_approx,
            initiator=initiator,
            reason=reason,
            evidence=evidence,
            by_user_id=ctx.staff.user.id,
            ticket_id=ticket.id if ticket else None,
            now=timezone.now(),
        )
        ctx.journal(
            "verification.ownership_corrected",
            object_type="vehicle_ownership",
            object_id=ownership_id,
            ticket_id=ticket.id if ticket else None,
            reason=reason,
            before=change["before"],
            after={**change["after"], "revision": str(revision.id)},
        )
        return CommandResult(200, {"vehicle": confirmations.vehicle_payload(vehicle)})

    payload = {
        "vehicle": vehicle.public_id,
        "ownership": str(ownership_id),
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat() if ended_at else None,
        "started_approx": started_approx,
        "ended_approx": ended_approx,
        "initiator": initiator,
        "reason": reason,
        "evidence": evidence,
    }
    return run_staff(request, "verification.ownership_correct", staff, payload, handler)


# --- The catalog of makes and models (§ 8.1.11) -------------------------------------------------


@require_GET
def catalog_other(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, CATALOG)
    found = confirmations.other_values()
    logged_read(
        request,
        staff,
        "vehicle.catalog_viewed",
        object_type="vehicle_catalog",
        object_id="other",
        after={"groups": len(found)},
    )
    return JsonResponse({"other": found})


@require_http_methods(["GET", "POST"])
def catalog_makes(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, CATALOG)
    if request.method == "GET":
        vehicle_type = request.GET.get("type", "")
        makes = VehicleMake.objects.filter(vehicle_type=vehicle_type).order_by("name")
        return JsonResponse({"makes": [{"code": m.code, "name": m.name} for m in makes]})
    data = parse_json(request)
    name = get_str(data, "name", max_length=100)
    vehicle_type = get_str(data, "vehicle_type", max_length=30)
    country = get_str(data, "country", max_length=100, required=False)
    reason = get_str(data, "reason", max_length=REASON_MAX)

    def handler(ctx: StaffContext) -> CommandResult:
        make = confirmations.add_make(name=name, vehicle_type=vehicle_type, country=country)
        ctx.journal(
            "vehicle.catalog_make_added",
            object_type="vehicle_make",
            object_id=make.code,
            reason=reason,
            after={"name": make.name, "vehicle_type": make.vehicle_type},
        )
        return CommandResult(201, {"make": {"code": make.code, "name": make.name}})

    payload = {"name": name, "vehicle_type": vehicle_type, "country": country, "reason": reason}
    return run_staff(request, "vehicle.catalog_make", staff, payload, handler)


@require_POST
def catalog_models(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, CATALOG)
    data = parse_json(request)
    make = get_str(data, "make", max_length=60)
    name = get_str(data, "name", max_length=150)
    reason = get_str(data, "reason", max_length=REASON_MAX)

    def handler(ctx: StaffContext) -> CommandResult:
        model = confirmations.add_model(make_code=make, name=name)
        ctx.journal(
            "vehicle.catalog_model_added",
            object_type="vehicle_model",
            object_id=model.code,
            reason=reason,
            after={"name": model.name, "make": make},
        )
        return CommandResult(201, {"model": {"code": model.code, "name": model.name}})

    payload = {"make": make, "name": name, "reason": reason}
    return run_staff(request, "vehicle.catalog_model", staff, payload, handler)


@require_POST
def catalog_link(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, CATALOG)
    data = parse_json(request)
    vehicles = data.get("vehicles")
    if not isinstance(vehicles, list) or not all(isinstance(v, str) for v in vehicles):
        raise errors.validation_error("vehicles")
    make = get_str(data, "make", max_length=60)
    model = get_str(data, "model", max_length=120, required=False)
    reason = get_str(data, "reason", max_length=REASON_MAX)

    def handler(ctx: StaffContext) -> CommandResult:
        changes = confirmations.link_to_catalog(
            vehicles=vehicles, make_code=make, model_code=model, now=timezone.now()
        )
        for change in changes:
            ctx.journal(
                "vehicle.catalog_linked",
                object_type="vehicle",
                object_id=change["vehicle"],
                reason=reason,
                before=change["before"],
                after=change["after"],
            )
        return CommandResult(200, {"linked": [c["vehicle"] for c in changes]})

    payload = {"vehicles": sorted(vehicles), "make": make, "model": model, "reason": reason}
    return run_staff(request, "vehicle.catalog_link", staff, payload, handler)
