"""HTTP API of owners, share transfers, representatives and deactivation of the company.

ТЗ § 3.3.4, § 3.3.6, § 3.4, § 3.5.4, § 3.6.

Who may do what:
* a current owner (user) sees the owners, shares, history and policies of the company and starts a
  transfer of his own share (§ 3.3.4); the top-authority director sees them too;
* the top authority sets the approval policies (PROPOSAL) and the limit of rights of an owner
  company's representative (§ 3.4.1);
* in an owner company its top authority or an employee with full rights sees the held shares,
  appoints, replaces or removes the representative and starts a transfer of the company's share
  (§ 3.4.1; for the transfer a PROPOSAL);
* `company.deactivate` starts deactivation (with a one-time code) and restoration (§ 3.6.1);
* each person answers the confirmations addressed to him or to a company where he has full rights
  in the personal area.
"""

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts import services as accounts
from c4rsys.accounts.middleware import require_user
from c4rsys.accounts.models import UserAccount
from c4rsys.companies import access, deactivation, ownership, services
from c4rsys.companies.models import (
    CompanyStateRequest,
    OwnerSeat,
    OwnershipPolicy,
    ShareTransfer,
)
from c4rsys.companies.views import run_and_respond
from c4rsys.platform import errors
from c4rsys.platform.access import require_permission
from c4rsys.platform.commands import Actor, CommandContext, CommandResult
from c4rsys.platform.http import get_int, get_str, parse_json

Mode = OwnershipPolicy.Mode
DEACTIVATE = "company.deactivate"


def _context(request: HttpRequest, cmp: str) -> tuple[UserAccount, services.CompanyContext, Actor]:
    user, session = require_user(request)
    context = services.resolve_company(user, cmp, session.id)
    return user, context, services.company_actor(user, context, session.id)


def _require_owner_view(user: UserAccount, context: services.CompanyContext) -> None:
    if not (context.is_top_authority or ownership.is_owner(context.company.id, user.id)):
        raise errors.permission_denied("ownership.owner")


def _require_top(context: services.CompanyContext) -> None:
    if not context.is_top_authority:
        raise errors.permission_denied("ownership.top_authority")


def _require_full(context: services.CompanyContext) -> None:
    if not access.has_full_rights(context.employment):
        raise errors.permission_denied("ownership.full_rights")


def _percent(data: dict[str, Any], key: str, *, required: bool = True) -> Decimal | None:
    raw = data.get(key)
    if raw in (None, "") and not required:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise errors.validation_error(key, code="PERCENT_INVALID") from exc
    if not value.is_finite() or value <= 0 or value > 100:
        raise errors.validation_error(key, code="PERCENT_INVALID")
    if value != value.quantize(Decimal("0.0001")):
        raise errors.validation_error(key, code="PERCENT_TOO_PRECISE")
    return value


def _confirm(user: UserAccount, data: dict[str, Any], action: str) -> None:
    accounts.confirm_critical(
        user, action, get_str(data, "flow"), get_str(data, "code", max_length=20)
    )


# --- Owners of the company (§ 3.3.4, § 3.3.6) -----------------------------------------------------


def _owners_body(user: UserAccount, context: services.CompanyContext) -> dict[str, Any]:
    company_id = context.company.id
    shares = ownership.current_shares(company_id)
    top = ownership.top_seat(company_id)
    top_key = ownership.party_key(top.owner_user, top.owner_company) if top else None
    owner_keys = {ownership.party_of_share(s).key for s in shares}
    me = ownership.Party(user=user, company=None)
    mine = ownership.share_of(company_id, me)
    seats = []
    if context.is_top_authority:
        seats = [
            {
                **ownership.seat_payload(seat),
                "owner": ownership.search_card(
                    ownership.Party(user=seat.owner_user, company=seat.owner_company)
                )
                if seat.state == OwnerSeat.State.PREPARED
                else ownership.owner_card(
                    ownership.Party(user=seat.owner_user, company=seat.owner_company)
                ),
            }
            for seat in OwnerSeat.objects.select_related(
                "position", "holder", "holder__user", "owner_user", "owner_company"
            )
            .filter(company_id=company_id, owner_company__isnull=False)
            .exclude(state=OwnerSeat.State.ENDED)
            .order_by("created_at")
        ]
    return {
        "owners": [
            {
                "owner": ownership.owner_card(ownership.party_of_share(s)),
                "percent": str(s.percent),
                "since": s.valid_from.isoformat(),
                "is_top_authority": ownership.party_of_share(s).key == top_key,
            }
            for s in shares
        ],
        "history": ownership.history(company_id),
        "transfers": [
            ownership.transfer_payload(
                t,
                revealed=t.state == ShareTransfer.State.COMPLETED
                or ownership.recipient_of(t).key in owner_keys,
            )
            for t in ownership.transfers_of(company_id)[:100]
        ],
        "policy": ownership.policy_payload(ownership.policy_of(company_id)),
        "seats": seats,
        "me": {
            "is_owner": mine is not None,
            "percent": str(mine.percent) if mine else None,
            "is_top_authority": context.is_top_authority,
            "public_id": user.public_id,
        },
    }


@require_GET
def owners(request: HttpRequest, cmp: str) -> JsonResponse:
    user, context, _actor = _context(request, cmp)
    _require_owner_view(user, context)
    return JsonResponse(_owners_body(user, context))


def _new_transfer(data: dict[str, Any], sender: ownership.Party) -> ownership.NewTransfer:
    recipient = ownership.resolve_party(get_str(data, "recipient", max_length=60))
    successor_text = get_str(data, "successor", max_length=60, required=False)
    successor = ownership.resolve_party(successor_text, "successor") if successor_text else None
    percent = _percent(data, "percent")
    if percent is None:
        raise ValueError("percent")
    return ownership.NewTransfer(
        sender=sender, recipient=recipient, percent=percent, successor=successor
    )


def _transfer_payload_of(new: ownership.NewTransfer, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "sender": new.sender.key,
        "recipient": new.recipient.key,
        "percent": str(new.percent),
        "successor": new.successor.key if new.successor else None,
        "flow": get_str(data, "flow"),
    }


@require_POST
def transfer_start(request: HttpRequest, cmp: str) -> JsonResponse:
    """§ 3.4.3: the owner transfers a part of his own share; confirmed by a one-time code."""
    user, context, actor = _context(request, cmp)
    if not ownership.is_owner(context.company.id, user.id):
        raise errors.permission_denied("ownership.owner")
    data = parse_json(request)
    new = _new_transfer(data, ownership.Party(user=user, company=None))
    _confirm(user, data, "transfer_share")
    company_id = context.company.id

    def handler(ctx: CommandContext) -> CommandResult:
        transfer = ownership.start_transfer(ctx, company_id, new)
        return CommandResult(
            201, {"transfer": ownership.transfer_payload(transfer, revealed=False)}
        )

    return run_and_respond(
        request, "ownership.transfer_start", actor, _transfer_payload_of(new, data), handler
    )


@require_http_methods(["GET", "PUT"])
def policy(request: HttpRequest, cmp: str) -> JsonResponse:
    """§ 3.4.2, § 3.6.2. Changed by the top authority (PROPOSAL)."""
    user, context, actor = _context(request, cmp)
    if request.method == "GET":
        if not (
            context.is_top_authority
            or ownership.is_owner(context.company.id, user.id)
            or access.RightsResolver().has_permission(actor, DEACTIVATE)
        ):
            raise errors.permission_denied("ownership.owner")
        return JsonResponse(
            {"policy": ownership.policy_payload(ownership.policy_of(context.company.id))}
        )
    _require_top(context)
    data = parse_json(request)
    transfer_mode = get_str(data, "transfer_mode", max_length=10)
    state_mode = get_str(data, "state_mode", max_length=10)
    if transfer_mode not in (Mode.NONE, Mode.ALL, Mode.PERCENT):
        raise errors.validation_error("transfer_mode")
    if state_mode not in Mode.values:
        raise errors.validation_error("state_mode")
    change = ownership.PolicyChange(
        transfer_mode=transfer_mode,
        transfer_percent=_percent(data, "transfer_percent")
        if transfer_mode == Mode.PERCENT
        else None,
        state_mode=state_mode,
        state_percent=_percent(data, "state_percent") if state_mode == Mode.PERCENT else None,
    )
    expected = get_int(data, "expected_version")
    company_id = context.company.id

    def handler(ctx: CommandContext) -> CommandResult:
        found = ownership.set_policy(ctx, company_id, change, expected)
        return CommandResult(200, {"policy": ownership.policy_payload(found)})

    payload = {
        "transfer_mode": change.transfer_mode,
        "transfer_percent": str(change.transfer_percent) if change.transfer_percent else None,
        "state_mode": change.state_mode,
        "state_percent": str(change.state_percent) if change.state_percent else None,
        "expected_version": expected,
    }
    return run_and_respond(request, "ownership.policy", actor, payload, handler)


@require_http_methods(["PUT"])
def seat_limit(request: HttpRequest, cmp: str, seat_id: UUID) -> JsonResponse:
    """§ 3.4.1: the top authority sets the limit of an owner company's representative."""
    _user, context, actor = _context(request, cmp)
    _require_top(context)
    data = parse_json(request)
    specs = access.parse_rights(data.get("rights"))
    company_id = context.company.id

    def handler(ctx: CommandContext) -> CommandResult:
        seat = ownership.set_limit(ctx, company_id, seat_id, specs)
        return CommandResult(200, {"seat": ownership.seat_payload(seat)})

    payload = {"seat_id": str(seat_id), "rights": [s.as_dict() for s in specs]}
    return run_and_respond(request, "ownership.seat_limit", actor, payload, handler)


# --- Shares held by an owner company (§ 3.4.1) ----------------------------------------------------


@require_GET
def holdings(request: HttpRequest, cmp: str) -> JsonResponse:
    _user, context, _actor = _context(request, cmp)
    _require_full(context)
    items = []
    for seat in (
        OwnerSeat.objects.select_related("company", "position", "holder", "holder__user")
        .filter(owner_company_id=context.company.id, state=OwnerSeat.State.ACTIVE)
        .order_by("created_at")
    ):
        held = seat.company
        share = ownership.share_of(held.id, ownership.Party(user=None, company=context.company))
        items.append(
            {
                "seat": ownership.seat_payload(seat),
                "company": ownership.search_card(ownership.Party(user=None, company=held)),
                "percent": str(share.percent) if share else None,
                # Owner rights belong to the owner company (§ 3.3.4).
                "owners": [
                    {
                        "owner": ownership.owner_card(ownership.party_of_share(s)),
                        "percent": str(s.percent),
                    }
                    for s in ownership.current_shares(held.id)
                ],
                "is_top_authority": seat.position.is_top_authority,
            }
        )
    return JsonResponse({"holdings": items})


def _target_user(data: dict[str, Any]) -> UserAccount:
    party = ownership.resolve_party(get_str(data, "target", max_length=60), "target")
    if party.user is None:
        raise errors.business_rule("OWNERSHIP_PARTY_NOT_FOUND", field="target")
    return party.user


@require_POST
def representative_assign(request: HttpRequest, cmp: str, seat_id: UUID) -> JsonResponse:
    _user, context, actor = _context(request, cmp)
    _require_full(context)
    data = parse_json(request)
    target = _target_user(data)
    specs = access.parse_rights(data.get("rights", []))
    company_id = context.company.id

    def handler(ctx: CommandContext) -> CommandResult:
        seat = ownership.assign_representative(ctx, company_id, seat_id, target, specs)
        return CommandResult(200, {"seat": ownership.seat_payload(seat)})

    payload = {
        "seat_id": str(seat_id),
        "target": target.public_id,
        "rights": [s.as_dict() for s in specs],
    }
    return run_and_respond(request, "ownership.representative_assign", actor, payload, handler)


@require_POST
def representative_remove(request: HttpRequest, cmp: str, seat_id: UUID) -> JsonResponse:
    _user, context, actor = _context(request, cmp)
    _require_full(context)
    company_id = context.company.id

    def handler(ctx: CommandContext) -> CommandResult:
        seat = ownership.remove_representative(ctx, company_id, seat_id)
        return CommandResult(200, {"seat": ownership.seat_payload(seat)})

    return run_and_respond(
        request, "ownership.representative_remove", actor, {"seat_id": str(seat_id)}, handler
    )


@require_POST
def holding_transfer_start(request: HttpRequest, cmp: str, seat_id: UUID) -> JsonResponse:
    """A transfer of the owner company's share: started by its top authority or an employee with
    full rights with a code (PROPOSAL), confirmed by each of its owners (§ 3.4.1)."""
    user, context, actor = _context(request, cmp)
    _require_full(context)
    seat = (
        OwnerSeat.objects.filter(
            pk=seat_id, owner_company_id=context.company.id, state=OwnerSeat.State.ACTIVE
        )
        .select_related("company")
        .first()
    )
    if seat is None:
        raise errors.not_found()
    data = parse_json(request)
    new = _new_transfer(data, ownership.Party(user=None, company=context.company))
    _confirm(user, data, "transfer_share")
    held_id = seat.company_id

    def handler(ctx: CommandContext) -> CommandResult:
        transfer = ownership.start_transfer(ctx, held_id, new)
        ctx.audit(
            "ownership.transfer_started",
            object_type="share_transfer",
            object_id=transfer.id,
            after={"company": seat.company.public_id, "percent": str(new.percent)},
        )
        return CommandResult(
            201, {"transfer": ownership.transfer_payload(transfer, revealed=False)}
        )

    payload = {"seat_id": str(seat_id), **_transfer_payload_of(new, data)}
    return run_and_respond(request, "ownership.transfer_start", actor, payload, handler)


# --- Personal area: confirmations addressed to the user -----------------------------------------


def _personal(request: HttpRequest) -> tuple[UserAccount, Actor]:
    user, session = require_user(request)
    return user, Actor(user_id=user.id, session_id=session.id)


@require_GET
def my_requests(request: HttpRequest) -> JsonResponse:
    user, _actor = _personal(request)
    transfers = []
    for approval in ownership.pending_approvals_for(user):
        t = approval.transfer
        party = ownership.Party(user=approval.party_user, company=approval.party_company)
        transfers.append(
            {
                "approval_id": str(approval.id),
                "role": approval.role,
                "party": ownership.search_card(party),
                "company": ownership.search_card(ownership.Party(user=None, company=t.company)),
                "transfer": ownership.transfer_payload(t, revealed=False),
            }
        )
    states = []
    for state_request, party in deactivation.pending_for(user.id):
        states.append(
            {
                "request_id": str(state_request.id),
                "kind": state_request.kind,
                "party": ownership.search_card(party),
                "party_key": party.key,
                "company": ownership.search_card(
                    ownership.Party(user=None, company=state_request.company)
                ),
                "request": deactivation.request_payload(state_request),
                "blockers": deactivation.blockers(state_request.company_id)
                if state_request.kind == CompanyStateRequest.Kind.DEACTIVATE
                else [],
            }
        )
    outgoing = [
        ownership.transfer_payload(t, revealed=False) for t in ownership.transfers_of_user(user)
    ]
    return JsonResponse({"transfers": transfers, "states": states, "outgoing": outgoing})


def _transfer_answer(request: HttpRequest, approval_id: UUID, approve: bool) -> JsonResponse:
    user, actor = _personal(request)

    def handler(ctx: CommandContext) -> CommandResult:
        transfer = ownership.decide_transfer(ctx, user, approval_id, approve=approve)
        return CommandResult(200, {"state": transfer.state})

    payload = {"approval_id": str(approval_id), "approve": approve}
    return run_and_respond(request, "ownership.approval_answer", actor, payload, handler)


@require_POST
def transfer_approve(request: HttpRequest, approval_id: UUID) -> JsonResponse:
    return _transfer_answer(request, approval_id, True)


@require_POST
def transfer_decline(request: HttpRequest, approval_id: UUID) -> JsonResponse:
    return _transfer_answer(request, approval_id, False)


@require_POST
def transfer_cancel(request: HttpRequest, transfer_id: UUID) -> JsonResponse:
    user, actor = _personal(request)

    def handler(ctx: CommandContext) -> CommandResult:
        transfer = ownership.cancel_transfer(ctx, transfer_id, user.id)
        return CommandResult(200, {"state": transfer.state})

    return run_and_respond(
        request, "ownership.transfer_cancel", actor, {"transfer_id": str(transfer_id)}, handler
    )


def _state_answer(request: HttpRequest, request_id: UUID, approve: bool) -> JsonResponse:
    user, actor = _personal(request)
    party = get_str(parse_json(request), "party", max_length=60)

    def handler(ctx: CommandContext) -> CommandResult:
        found = deactivation.decide(ctx, user.id, request_id, party, approve=approve)
        return CommandResult(200, {"state": found.state})

    payload = {"request_id": str(request_id), "party": party, "approve": approve}
    return run_and_respond(request, "company.state_approval_answer", actor, payload, handler)


@require_POST
def state_approve(request: HttpRequest, request_id: UUID) -> JsonResponse:
    return _state_answer(request, request_id, True)


@require_POST
def state_decline(request: HttpRequest, request_id: UUID) -> JsonResponse:
    return _state_answer(request, request_id, False)


# --- Deactivation and restoration (§ 3.6) ------------------------------------------------------


def _state_body(context: services.CompanyContext, can_manage: bool) -> dict[str, Any]:
    company = context.company
    pending = deactivation.pending_request(company.id)
    need = deactivation.required(company.id)
    return {
        "deactivated_at": company.deactivated_at.isoformat() if company.deactivated_at else None,
        "blockers": deactivation.blockers(company.id) if company.deactivated_at is None else [],
        "policy": {
            "configured": need.configured,
            "mode": need.mode,
            "percent": str(need.percent) if need.percent is not None else None,
        },
        "request": deactivation.request_payload(pending) if pending else None,
        "can_manage": can_manage,
    }


@require_GET
def state(request: HttpRequest, cmp: str) -> JsonResponse:
    user, context, actor = _context(request, cmp)
    can_manage = access.RightsResolver().has_permission(actor, DEACTIVATE, whole_company=True)
    if not (can_manage or ownership.is_owner(context.company.id, user.id)):
        raise errors.permission_denied(DEACTIVATE)
    return JsonResponse(_state_body(context, can_manage))


def _state_command(request: HttpRequest, cmp: str, kind: str) -> JsonResponse:
    user, context, actor = _context(request, cmp)
    require_permission(access.RightsResolver(), actor, DEACTIVATE, whole_company=True)
    data = parse_json(request)
    payload: dict[str, Any] = {"kind": kind}
    if kind == CompanyStateRequest.Kind.DEACTIVATE:
        _confirm(user, data, "deactivate_company")
        payload["flow"] = get_str(data, "flow")
    company_id = context.company.id

    def handler(ctx: CommandContext) -> CommandResult:
        deactivation.start(ctx, company_id, kind)
        fresh = services.get_company(company_id)
        if fresh is None:
            raise ValueError("fresh")
        return CommandResult(
            200, _state_body(services.CompanyContext(fresh, context.employment), True)
        )

    name = (
        "company.deactivate" if kind == CompanyStateRequest.Kind.DEACTIVATE else "company.restore"
    )
    return run_and_respond(request, name, actor, payload, handler)


@require_POST
def deactivate(request: HttpRequest, cmp: str) -> JsonResponse:
    return _state_command(request, cmp, CompanyStateRequest.Kind.DEACTIVATE)


@require_POST
def restore(request: HttpRequest, cmp: str) -> JsonResponse:
    """§ 3.6.1: a separate action. The ТЗ asks no code for it (TODO(spec))."""
    return _state_command(request, cmp, CompanyStateRequest.Kind.RESTORE)


def _request_command(request: HttpRequest, cmp: str, request_id: UUID, name: str) -> JsonResponse:
    _user, context, actor = _context(request, cmp)
    require_permission(access.RightsResolver(), actor, DEACTIVATE, whole_company=True)
    company_id = context.company.id
    run = deactivation.cancel if name.endswith("cancel") else deactivation.complete

    def handler(ctx: CommandContext) -> CommandResult:
        run(ctx, company_id, request_id)
        fresh = services.get_company(company_id)
        if fresh is None:
            raise ValueError("fresh")
        return CommandResult(
            200, _state_body(services.CompanyContext(fresh, context.employment), True)
        )

    return run_and_respond(request, name, actor, {"request_id": str(request_id)}, handler)


@require_POST
def state_request_cancel(request: HttpRequest, cmp: str, request_id: UUID) -> JsonResponse:
    return _request_command(request, cmp, request_id, "company.state_request_cancel")


@require_POST
def state_request_complete(request: HttpRequest, cmp: str, request_id: UUID) -> JsonResponse:
    return _request_command(request, cmp, request_id, "company.state_request_complete")
