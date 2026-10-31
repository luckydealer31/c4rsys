"""Internal API of block 11 part П5: the merge and split of accounts (ТЗ § 24.1.8), the
verification of links and disputed records (§ 24.1.5, § 24.1.7) and the recovery of access
through the support (§ 2.3.1; decision В-11.2).

Reads that show data of a user or a company are journaled before the answer; commands go through
`run_staff` (one transaction, the system journal, the Idempotency-Key).
"""

from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.accounts import services as accounts
from c4rsys.backoffice import directory, merging, recovery, tickets
from c4rsys.backoffice.core import StaffContext, logged_read, require_staff, run_staff
from c4rsys.backoffice.models import AccountSplitAssignment, RecoveryCase, SupportTicket, TicketLink
from c4rsys.backoffice.views import REASON_MAX, TEXT_MAX, _moment, _uuid, _version
from c4rsys.fleet import disputes, identity
from c4rsys.parties import links as profile_links
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandResult
from c4rsys.platform.http import get_str, parse_json


def _ticket(data: dict[str, Any]) -> SupportTicket | None:
    number = data.get("ticket_number")
    if number is None:
        return None
    if isinstance(number, bool) or not isinstance(number, int):
        raise errors.validation_error("ticket_number", code="INTEGER_REQUIRED")
    return tickets.by_number(number)


# --- Merge and split (§ 24.1.8) ------------------------------------------------------------------


@require_POST
def merge_preview(request: HttpRequest) -> JsonResponse:
    """What moves and the conflicts, before the merge. A read, journaled."""
    staff = require_staff(request, "accounts.merge")
    data = parse_json(request)
    main = directory.find_user(get_str(data, "main", max_length=40))
    secondary = directory.find_user(get_str(data, "secondary", max_length=40))
    shown = merging.preview(main, secondary)
    logged_read(
        request,
        staff,
        "account.merge_previewed",
        object_type="user_account",
        object_id=secondary.public_id,
        target_user_id=main.id,
        after={"main": main.public_id, "conflicts": len(shown["conflicts"])},
    )
    return JsonResponse({"preview": shown})


@require_http_methods(["GET", "POST"])
def merge_list(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, "accounts.merge")
    if request.method == "GET":
        return JsonResponse({"merges": [merging.payload(m) for m in merging.merges()]})
    data = parse_json(request)
    main = directory.find_user(get_str(data, "main", max_length=40))
    secondary = directory.find_user(get_str(data, "secondary", max_length=40))
    reason = get_str(data, "reason", max_length=REASON_MAX)
    ticket = _ticket(data)
    raw = data.get("expected")
    if raw is not None and (not isinstance(raw, list) or not all(isinstance(x, str) for x in raw)):
        raise errors.validation_error("expected")
    expected = list(raw) if raw is not None else None

    def handler(ctx: StaffContext) -> CommandResult:
        found = merging.merge(ctx, main, secondary, reason=reason, ticket=ticket, expected=expected)
        return CommandResult(201, {"merge": merging.payload(merging.get(found.id), detail=True)})

    payload = {
        "main": main.public_id,
        "secondary": secondary.public_id,
        "reason": reason,
        "ticket_number": data.get("ticket_number"),
        "expected": sorted(expected) if expected is not None else None,
    }
    return run_staff(request, "account.merge", staff, payload, handler)


@require_GET
def merge_detail(request: HttpRequest, merge_id: UUID) -> JsonResponse:
    staff = require_staff(request, "accounts.merge")
    found = merging.get(merge_id)
    logged_read(
        request,
        staff,
        "account.merge_viewed",
        object_type="account_merge",
        object_id=found.id,
        target_user_id=found.main_user_id,
    )
    return JsonResponse({"merge": merging.payload(found, detail=True)})


def _merge_command(
    request: HttpRequest, merge_id: UUID, command: str, payload: dict[str, Any], change: Any
) -> JsonResponse:
    staff = require_staff(request, "accounts.merge")
    expected = _version(payload)

    def handler(ctx: StaffContext) -> CommandResult:
        found = merging.locked(merge_id, expected)
        change(ctx, found)
        return CommandResult(200, {"merge": merging.payload(merging.get(found.id), detail=True)})

    return run_staff(request, command, staff, {"merge": str(merge_id), **payload}, handler)


@require_POST
def split_start(request: HttpRequest, merge_id: UUID) -> JsonResponse:
    data = parse_json(request)
    reason = get_str(data, "reason", max_length=REASON_MAX)
    return _merge_command(
        request,
        merge_id,
        "account.split_start",
        {"reason": reason, "version": _version(data)},
        lambda ctx, m: merging.start_split(ctx, m, reason),
    )


@require_POST
def split_assign(request: HttpRequest, merge_id: UUID) -> JsonResponse:
    data = parse_json(request)
    module = get_str(data, "module", max_length=30)
    kind = get_str(data, "kind", max_length=30)
    record_id = _uuid(data.get("record_id"), "record_id")
    target = get_str(data, "target", max_length=10)
    if target not in AccountSplitAssignment.Target.values:
        raise errors.validation_error("target")
    payload = {
        "module": module,
        "kind": kind,
        "record_id": str(record_id),
        "target": target,
        "version": _version(data),
    }
    return _merge_command(
        request,
        merge_id,
        "account.split_assign",
        payload,
        lambda ctx, m: merging.assign(
            ctx, m, module=module, kind=kind, record_id=record_id, target=target
        ),
    )


@require_POST
def split_complete(request: HttpRequest, merge_id: UUID) -> JsonResponse:
    data = parse_json(request)
    return _merge_command(
        request,
        merge_id,
        "account.split_complete",
        {"version": _version(data)},
        lambda ctx, m: merging.complete_split(ctx, m),
    )


# --- Verification of links and disputed records (§ 24.1.5, § 24.1.7) ----------------------------


@require_GET
def verification_queue(request: HttpRequest) -> JsonResponse:
    """The queue of the verification: disputes of vehicle ownership (§ 8.3.12), the disputed
    records linked to tickets, and the place of the conflicts of identifiers (block 12)."""
    staff = require_staff(request, "verification.resolve")
    open_disputes = disputes.open_disputes()
    open_conflicts = identity.conflicts("open")
    logged_read(
        request,
        staff,
        "verification.queue_viewed",
        object_type="verification",
        object_id="queue",
        after={
            "ownership_disputes": len(open_disputes),
            "identifier_conflicts": len(open_conflicts),
        },
    )
    records = (
        TicketLink.objects.select_related("ticket")
        .filter(kind=TicketLink.Kind.RECORD)
        .exclude(ticket__state=SupportTicket.State.CLOSED)
        .order_by("-created_at")[:100]
    )
    return JsonResponse(
        {
            "ownership_disputes": [disputes.dispute_payload(r) for r in open_disputes],
            "decided_disputes": [
                disputes.dispute_payload(d.request) for d in disputes.decided_disputes()
            ],
            "ticket_records": [
                {
                    "ticket_id": str(link.ticket_id),
                    "ticket_number": link.ticket.number,
                    "record_type": link.record_type,
                    "record_ref": link.record_ref,
                }
                for link in records
            ],
            # Conflicts of VIN and other identifiers (§ 8.6.1–8.6.2, block 12): the open ones.
            "identifier_conflicts": [identity.staff_payload(c) for c in open_conflicts],
        }
    )


@require_POST
def ownership_decide(request: HttpRequest, request_id: UUID) -> JsonResponse:
    staff = require_staff(request, "verification.resolve")
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=10)
    reason = get_str(data, "reason", max_length=REASON_MAX)
    effective_at = _moment(data, "effective_at")
    ticket = _ticket(data)

    def handler(ctx: StaffContext) -> CommandResult:
        decision, change = disputes.decide(
            request_id,
            kind=kind,
            reason=reason,
            effective_at=effective_at,
            by_user_id=ctx.staff.user.id,
            ticket_id=ticket.id if ticket else None,
            now=timezone.now(),
        )
        ctx.journal(
            "verification.ownership_decided",
            object_type="vehicle_owner_request",
            object_id=request_id,
            ticket_id=ticket.id if ticket else None,
            reason=reason,
            before=change["before"],
            after={**change["after"], "vehicle": change["vehicle"]},
        )
        return CommandResult(200, {"dispute": disputes.dispute_payload(decision.request)})

    payload = {
        "request": str(request_id),
        "kind": kind,
        "reason": reason,
        "effective_at": effective_at.isoformat() if effective_at else None,
        "ticket_number": data.get("ticket_number"),
    }
    return run_staff(request, "verification.ownership", staff, payload, handler)


@require_GET
def profile_link_detail(request: HttpRequest, link_id: UUID) -> JsonResponse:
    staff = require_staff(request, "verification.resolve")
    link = profile_links.find(link_id)
    if link is None:
        raise errors.not_found()
    logged_read(
        request,
        staff,
        "verification.profile_link_viewed",
        object_type="profile_link",
        object_id=link.id,
        target_company_id=link.company_id,
    )
    return JsonResponse({"link": profile_links.verification_payload(link)})


@require_POST
def profile_link_break(request: HttpRequest, link_id: UUID) -> JsonResponse:
    """A correction of a system link after the check: the disputed link ends (§ 24.1.7)."""
    staff = require_staff(request, "verification.resolve")
    data = parse_json(request)
    reason = get_str(data, "reason", max_length=REASON_MAX)
    ticket = _ticket(data)

    def handler(ctx: StaffContext) -> CommandResult:
        link = profile_links.break_by_platform(ctx.ctx, link_id, reason)
        ctx.journal(
            "verification.profile_link_ended",
            object_type="profile_link",
            object_id=link.id,
            target_company_id=link.company_id,
            target_user_id=link.target_user_id,
            ticket_id=ticket.id if ticket else None,
            reason=reason,
            after={"state": link.state},
        )
        return CommandResult(200, {"link": profile_links.verification_payload(link)})

    payload = {"link": str(link_id), "reason": reason, "ticket_number": data.get("ticket_number")}
    return run_staff(request, "verification.profile_link", staff, payload, handler)


@require_GET
def user_login_contacts(request: HttpRequest, usr: str) -> JsonResponse:
    """The login contacts of a profile, masked, with their ids for the unlinking."""
    staff = require_staff(request, "verification.resolve")
    user = directory.find_user(usr)
    logged_read(
        request,
        staff,
        "verification.contacts_viewed",
        object_type="user_account",
        object_id=user.public_id,
        target_user_id=user.id,
    )
    return JsonResponse(
        {
            "contacts": [
                {
                    "id": str(c.id),
                    "kind": c.kind,
                    "value": directory.mask_email(c.value)
                    if c.kind == "email"
                    else directory.mask_phone(c.value),
                    "unavailable": c.unavailable_at is not None,
                    "verified_at": c.verified_at.isoformat(),
                }
                for c in accounts.contacts_of(user.id)
            ]
        }
    )


@require_POST
def user_contact_unlink(request: HttpRequest, usr: str, contact_id: UUID) -> JsonResponse:
    """Unlinking a disputed contact after the check (§ 24.1.7, § 2.3.4)."""
    staff = require_staff(request, "verification.resolve")
    data = parse_json(request)
    reason = get_str(data, "reason", max_length=REASON_MAX)
    ticket = _ticket(data)
    user = directory.find_user(usr)

    def handler(ctx: StaffContext) -> CommandResult:
        kind, value, ended = accounts.unlink_contact(user.id, contact_id, timezone.now())
        ctx.journal(
            "verification.contact_unlinked",
            object_type="user_account",
            object_id=user.public_id,
            target_user_id=user.id,
            ticket_id=ticket.id if ticket else None,
            reason=reason,
            before={"contact_kind": kind, "contact": value},
            after={"sessions_ended": ended},
        )
        return CommandResult(200, {"unlinked": True, "sessions_ended": ended})

    payload = {
        "user": user.public_id,
        "contact": str(contact_id),
        "reason": reason,
        "ticket_number": data.get("ticket_number"),
    }
    return run_staff(request, "verification.contact_unlink", staff, payload, handler)


# --- Recovery through the support (§ 2.3.1; decision В-11.2) -------------------------------------


@require_GET
def recovery_list(request: HttpRequest) -> JsonResponse:
    require_staff(request, "recovery.manage")
    recovery.require_enabled()
    state = request.GET.get("state", "")
    if state and state not in RecoveryCase.State.values:
        raise errors.validation_error("state")
    return JsonResponse({"cases": [recovery.payload(c) for c in recovery.cases(state)]})


@require_GET
def recovery_detail(request: HttpRequest, case_id: UUID) -> JsonResponse:
    staff = require_staff(request, "recovery.manage")
    recovery.require_enabled()
    case = recovery.get(case_id)
    logged_read(
        request,
        staff,
        "recovery.viewed",
        object_type="recovery_case",
        object_id=case.id,
        target_user_id=case.user_id,
    )
    return JsonResponse({"case": recovery.payload(case, detail=True)})


def _recovery_command(
    request: HttpRequest,
    case_id: UUID,
    command: str,
    payload: dict[str, Any],
    change: Any,
    permission: str = "recovery.manage",
) -> JsonResponse:
    staff = require_staff(request, permission)
    recovery.require_enabled()
    expected = _version(payload)

    def handler(ctx: StaffContext) -> CommandResult:
        case = recovery.locked(case_id, expected)
        change(ctx, case)
        return CommandResult(200, {"case": recovery.payload(recovery.get(case.id), detail=True)})

    return run_staff(request, command, staff, {"case": str(case_id), **payload}, handler)


@require_POST
def recovery_account(request: HttpRequest, case_id: UUID) -> JsonResponse:
    data = parse_json(request)
    user = directory.find_user(get_str(data, "user", max_length=40))
    return _recovery_command(
        request,
        case_id,
        "recovery.account",
        {"user": user.public_id, "version": _version(data)},
        lambda ctx, c: recovery.set_account(ctx, c, user),
    )


@require_POST
def recovery_ticket(request: HttpRequest, case_id: UUID) -> JsonResponse:
    data = parse_json(request)
    ticket = _ticket(data)
    if ticket is None:
        raise errors.validation_error("ticket_number", code="FIELD_REQUIRED")
    return _recovery_command(
        request,
        case_id,
        "recovery.ticket",
        {"ticket_number": ticket.number, "version": _version(data)},
        lambda ctx, c: recovery.set_ticket(ctx, c, ticket),
    )


@require_POST
def recovery_info(request: HttpRequest, case_id: UUID) -> JsonResponse:
    data = parse_json(request)
    comment = get_str(data, "comment", max_length=TEXT_MAX)
    return _recovery_command(
        request,
        case_id,
        "recovery.info",
        {"comment": comment, "version": _version(data)},
        lambda ctx, c: recovery.request_info(ctx, c, comment),
    )


@require_POST
def recovery_propose(request: HttpRequest, case_id: UUID) -> JsonResponse:
    data = parse_json(request)
    assessment = get_str(data, "assessment", max_length=TEXT_MAX)
    raw = data.get("unavailable_contacts") or []
    if not isinstance(raw, list):
        raise errors.validation_error("unavailable_contacts")
    unavailable = [_uuid(x, "unavailable_contacts") for x in raw]
    urgent = data.get("urgent") is True
    urgent_basis = get_str(data, "urgent_basis", max_length=TEXT_MAX, required=False)
    payload = {
        "assessment": assessment,
        "unavailable_contacts": sorted(str(x) for x in unavailable),
        "urgent": urgent,
        "urgent_basis": urgent_basis,
        "version": _version(data),
    }
    return _recovery_command(
        request,
        case_id,
        "recovery.propose",
        payload,
        lambda ctx, c: recovery.propose(
            ctx,
            c,
            assessment=assessment,
            unavailable=unavailable,
            urgent=urgent,
            urgent_basis=urgent_basis,
        ),
    )


@require_POST
def recovery_approve(request: HttpRequest, case_id: UUID) -> JsonResponse:
    data = parse_json(request)
    comment = get_str(data, "comment", max_length=TEXT_MAX, required=False)
    return _recovery_command(
        request,
        case_id,
        "recovery.approve",
        {"comment": comment, "version": _version(data)},
        lambda ctx, c: recovery.approve(ctx, c, comment),
        permission="recovery.approve",
    )


@require_POST
def recovery_refuse(request: HttpRequest, case_id: UUID) -> JsonResponse:
    data = parse_json(request)
    reason = get_str(data, "reason", max_length=REASON_MAX)
    return _recovery_command(
        request,
        case_id,
        "recovery.refuse",
        {"reason": reason, "version": _version(data)},
        lambda ctx, c: recovery.refuse(ctx, c, reason),
    )
