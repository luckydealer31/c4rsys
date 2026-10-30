"""Internal API of the system staff, `/api/staff/…` on the staff subdomain only (ТЗ гл. 24;
decision В-11.1; block 11, parts П1–П4).

Reads that show data of a user or a company are journaled before the answer; commands go
through `run_staff` (one transaction, the system journal, the Idempotency-Key). A privileged
session only reads: the sections below call the read functions and payloads of the modules that
own the data; no endpoint here changes data of a company (§ 24.1.4).
"""

import csv
import io
from datetime import date, datetime
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from c4rsys.backoffice import blocking, catalog, directory, members, privileged, tickets
from c4rsys.backoffice.core import Staff, StaffContext, logged_read, require_staff, run_staff
from c4rsys.backoffice.models import (
    SupportTicket,
    SystemJournalEntry,
    SystemRole,
    TicketComment,
    TicketLink,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandResult
from c4rsys.platform.http import get_str, parse_json

REASON_MAX = 2000
TEXT_MAX = 10000


def _uuid(value: Any, field: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise errors.validation_error(field) from exc


def _version(data: dict[str, Any]) -> int | None:
    value = data.get("version")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise errors.validation_error("version", code="INTEGER_REQUIRED")
    return value


def _moment(data: dict[str, Any], key: str) -> datetime | None:
    text = get_str(data, key, max_length=40, required=False)
    if not text:
        return None
    found = parse_datetime(text)
    if found is None or found.tzinfo is None:
        raise errors.validation_error(key, code="DATETIME_INVALID")
    return found


# --- Who am I -------------------------------------------------------------------------------------


@require_GET
def me(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request)
    user = staff.user
    open_session = next(
        (s for s in privileged.sessions_of(staff) if s.ended_at is None),
        None,
    )
    return JsonResponse(
        {
            "user": {
                "public_id": user.public_id,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language": user.language,
                "theme": user.theme,
                "timezone": user.time_zone,
            },
            "member": members.member_payload(staff.member),
            "roles": list(staff.roles),
            "permissions": sorted(staff.permissions),
            "privileged_session": privileged.payload(open_session) if open_session else None,
            "catalog": {
                "permissions": list(catalog.PERMISSIONS),
                "session_reasons": list(catalog.SESSION_REASONS),
                "block_categories": list(catalog.BLOCK_CATEGORIES),
                "scopes": list(catalog.SCOPES),
            },
        }
    )


# --- Users and companies (§ 24.1.2, § 24.4.3) ----------------------------------------------------


@require_GET
def user_search(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, "directory.search")
    text = request.GET.get("q", "")[:100]
    found = [directory.user_summary(u) for u in directory.search_users(text)]
    logged_read(
        request,
        staff,
        "directory.users_searched",
        object_type="user_account",
        object_id="search",
        after={"query": text, "found": len(found)},
    )
    return JsonResponse({"users": found})


@require_GET
def user_detail(request: HttpRequest, usr: str) -> JsonResponse:
    staff = require_staff(request, "directory.search")
    user = directory.find_user(usr)
    logged_read(
        request,
        staff,
        "directory.user_viewed",
        object_type="user_account",
        object_id=user.public_id,
        target_user_id=user.id,
    )
    return JsonResponse({"user": directory.user_card(user)})


def _reason(request: HttpRequest) -> str:
    return get_str(parse_json(request), "reason", max_length=REASON_MAX)


@require_POST
def user_reveal(request: HttpRequest, usr: str) -> JsonResponse:
    """The full contacts after a reason, journaled (§ 24.4.3)."""
    staff = require_staff(request, "contacts.reveal")
    reason = _reason(request)
    user = directory.find_user(usr)
    logged_read(
        request,
        staff,
        "directory.user_contacts_revealed",
        object_type="user_account",
        object_id=user.public_id,
        target_user_id=user.id,
        reason=reason,
    )
    return JsonResponse({"contacts": directory.user_contacts(user)})


@require_GET
def company_search(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, "directory.search")
    text = request.GET.get("q", "")[:100]
    found = [directory.company_summary(c) for c in directory.search_companies(text)]
    logged_read(
        request,
        staff,
        "directory.companies_searched",
        object_type="company",
        object_id="search",
        after={"query": text, "found": len(found)},
    )
    return JsonResponse({"companies": found})


@require_GET
def company_detail(request: HttpRequest, cmp: str) -> JsonResponse:
    staff = require_staff(request, "directory.search")
    company = directory.find_company(cmp)
    logged_read(
        request,
        staff,
        "directory.company_viewed",
        object_type="company",
        object_id=company.public_id,
        target_company_id=company.id,
    )
    return JsonResponse({"company": directory.company_card(company)})


@require_POST
def company_reveal(request: HttpRequest, cmp: str) -> JsonResponse:
    staff = require_staff(request, "contacts.reveal")
    reason = _reason(request)
    company = directory.find_company(cmp)
    logged_read(
        request,
        staff,
        "directory.company_contacts_revealed",
        object_type="company",
        object_id=company.public_id,
        target_company_id=company.id,
        reason=reason,
    )
    return JsonResponse({"contacts": directory.company_contacts(company)})


# --- Tickets (§ 24.1.3, § 24.1.7) ----------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def ticket_list(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, "tickets.manage")
    if request.method == "GET":
        state = request.GET.get("state", "")
        if state and state not in SupportTicket.State.values:
            raise errors.validation_error("state")
        return JsonResponse({"tickets": [tickets.payload(t) for t in tickets.tickets(state)[:200]]})
    data = parse_json(request)
    received = get_str(data, "received_on", max_length=10, required=False)
    try:
        received_on = date.fromisoformat(received) if received else timezone.localdate()
    except ValueError as exc:
        raise errors.validation_error("received_on", code="DATE_INVALID") from exc
    new = tickets.NewTicket(
        received_on=received_on,
        channel=get_str(data, "channel", max_length=20, required=False) or "email",
        applicant_name=get_str(data, "applicant_name", max_length=200, required=False),
        applicant_contact=get_str(data, "applicant_contact", required=False),
        subject=get_str(data, "subject", max_length=300),
        description=get_str(data, "description", max_length=TEXT_MAX),
    )
    payload = {**new.__dict__, "received_on": received_on.isoformat()}

    def handler(ctx: StaffContext) -> CommandResult:
        t = tickets.create(ctx, new)
        return CommandResult(201, {"ticket": tickets.payload(t, detail=True)})

    return run_staff(request, "ticket.create", staff, payload, handler)


@require_GET
def ticket_detail(request: HttpRequest, ticket_id: UUID) -> JsonResponse:
    staff = require_staff(request, "tickets.manage")
    t = tickets.get(ticket_id)
    logged_read(
        request,
        staff,
        "ticket.viewed",
        object_type="support_ticket",
        object_id=t.id,
        ticket_id=t.id,
    )
    return JsonResponse({"ticket": tickets.payload(t, detail=True)})


def _ticket_command(
    request: HttpRequest, ticket_id: UUID, command: str, payload: dict[str, Any], change: Any
) -> JsonResponse:
    staff = require_staff(request, "tickets.manage")
    expected = _version(payload)

    def handler(ctx: StaffContext) -> CommandResult:
        t = tickets.locked(ticket_id, expected)
        change(ctx, t)
        return CommandResult(200, {"ticket": tickets.payload(tickets.get(t.id), detail=True)})

    return run_staff(request, command, staff, {"ticket": str(ticket_id), **payload}, handler)


@require_POST
def ticket_state(request: HttpRequest, ticket_id: UUID) -> JsonResponse:
    data = parse_json(request)
    state = get_str(data, "state", max_length=20)
    if state not in SupportTicket.State.values:
        raise errors.validation_error("state")
    result = get_str(data, "result", max_length=TEXT_MAX, required=False)
    payload = {"state": state, "result": result, "version": _version(data)}
    return _ticket_command(
        request,
        ticket_id,
        "ticket.state",
        payload,
        lambda ctx, t: tickets.set_state(ctx, t, state, result),
    )


@require_POST
def ticket_assign(request: HttpRequest, ticket_id: UUID) -> JsonResponse:
    data = parse_json(request)
    raw = data.get("member_id")
    member_id = _uuid(raw, "member_id") if raw else None
    payload = {"member_id": str(member_id) if member_id else None, "version": _version(data)}
    return _ticket_command(
        request,
        ticket_id,
        "ticket.assign",
        payload,
        lambda ctx, t: tickets.assign(ctx, t, member_id),
    )


@require_POST
def ticket_comment(request: HttpRequest, ticket_id: UUID) -> JsonResponse:
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=20, required=False) or TicketComment.Kind.COMMENT
    if kind not in TicketComment.Kind.values:
        raise errors.validation_error("kind")
    text = get_str(data, "text", max_length=TEXT_MAX)
    return _ticket_command(
        request,
        ticket_id,
        "ticket.comment",
        {"kind": kind, "text": text},
        lambda ctx, t: tickets.comment(ctx, t, kind, text),
    )


@require_POST
def ticket_link(request: HttpRequest, ticket_id: UUID) -> JsonResponse:
    data = parse_json(request)
    kind = get_str(data, "kind", max_length=10)
    if kind not in TicketLink.Kind.values:
        raise errors.validation_error("kind")
    target = get_str(data, "target", max_length=200)
    record_type = get_str(data, "record_type", max_length=60, required=False)
    company = directory.find_company(target) if kind == TicketLink.Kind.COMPANY else None
    user = directory.find_user(target) if kind == TicketLink.Kind.USER else None
    if kind == TicketLink.Kind.RECORD and not record_type:
        raise errors.validation_error("record_type", code="FIELD_REQUIRED")
    payload = {"kind": kind, "target": target, "record_type": record_type}
    return _ticket_command(
        request,
        ticket_id,
        "ticket.link",
        payload,
        lambda ctx, t: tickets.link(
            ctx,
            t,
            kind,
            company=company,
            user=user,
            record_type=record_type,
            record_ref=target if kind == TicketLink.Kind.RECORD else "",
        ),
    )


# --- Staff and roles (§ 24.1.5–24.1.6) -----------------------------------------------------------


@require_http_methods(["GET", "POST"])
def member_list(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        # The list of colleagues for assigning a ticket is seen by whoever keeps tickets.
        staff = require_staff(request)
        if not (staff.can("staff.manage") or staff.can("tickets.manage")):
            raise errors.permission_denied("staff.manage")
        return JsonResponse({"members": [members.member_payload(m) for m in members.members()]})
    staff = require_staff(request, "staff.manage")
    data = parse_json(request)
    user = directory.find_user(get_str(data, "user", max_length=40))
    unit = get_str(data, "unit", max_length=100, required=False)
    roles = _codes(data.get("roles"))

    def handler(ctx: StaffContext) -> CommandResult:
        member = members.add(ctx, user, unit, roles)
        return CommandResult(201, {"member": members.member_payload(member)})

    payload = {"user": user.public_id, "unit": unit, "roles": roles}
    return run_staff(request, "member.add", staff, payload, handler)


def _codes(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise errors.validation_error("roles")
    return sorted(set(value))


@require_POST
def member_change(request: HttpRequest, member_id: UUID) -> JsonResponse:
    staff = require_staff(request, "staff.manage")
    data = parse_json(request)
    unit = get_str(data, "unit", max_length=100, required=False) if "unit" in data else None
    roles = _codes(data["roles"]) if "roles" in data else None
    expected = _version(data)

    def handler(ctx: StaffContext) -> CommandResult:
        member = members.change(ctx, members.locked(member_id, expected), unit, roles)
        return CommandResult(200, {"member": members.member_payload(member)})

    payload = {"member": str(member_id), "unit": unit, "roles": roles, "version": expected}
    return run_staff(request, "member.change", staff, payload, handler)


@require_POST
def member_revoke(request: HttpRequest, member_id: UUID) -> JsonResponse:
    staff = require_staff(request, "staff.manage")
    data = parse_json(request)
    reason = get_str(data, "reason", max_length=REASON_MAX)
    expected = _version(data)

    def handler(ctx: StaffContext) -> CommandResult:
        member = members.revoke(ctx, members.locked(member_id, expected), reason)
        return CommandResult(200, {"member": members.member_payload(member)})

    payload = {"member": str(member_id), "reason": reason, "version": expected}
    return run_staff(request, "member.revoke", staff, payload, handler)


@require_http_methods(["GET", "POST"])
def role_list(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, "staff.manage")
    if request.method == "GET":
        roles = SystemRole.objects.order_by("-is_starter", "code")
        return JsonResponse(
            {
                "roles": [members.role_payload(r) for r in roles],
                "permissions": list(catalog.PERMISSIONS),
            }
        )
    data = parse_json(request)
    code = get_str(data, "code", max_length=60)
    name = get_str(data, "name", max_length=200, required=False)
    permissions = _codes(data.get("permissions"))

    def handler(ctx: StaffContext) -> CommandResult:
        role = members.create_role(ctx, code, name, permissions)
        return CommandResult(201, {"role": members.role_payload(role)})

    payload = {"code": code, "name": name, "permissions": permissions}
    return run_staff(request, "role.create", staff, payload, handler)


# --- System journal (§ 24.4.5) -------------------------------------------------------------------


def _journal_query(request: HttpRequest) -> Any:
    query = SystemJournalEntry.objects.order_by("-occurred_at")
    params = request.GET
    if params.get("action"):
        query = query.filter(action__startswith=params["action"][:100])
    if params.get("object_id"):
        query = query.filter(object_id=params["object_id"][:64])
    for key, field in (("from", "occurred_at__gte"), ("to", "occurred_at__lte")):
        if params.get(key):
            moment = parse_datetime(params[key])
            if moment is None:
                raise errors.validation_error(key, code="DATETIME_INVALID")
            query = query.filter(**{field: moment})
    return query


def _journal_payload(e: SystemJournalEntry, names: dict[UUID, str]) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "occurred_at": e.occurred_at.isoformat(),
        "actor": names.get(e.actor_user_id) if e.actor_user_id else None,
        "system_process": e.system_process or None,
        "roles": list(e.roles),
        "action": e.action,
        "object_type": e.object_type,
        "object_id": e.object_id,
        "privileged_session_id": str(e.privileged_session_id) if e.privileged_session_id else None,
        "ticket_id": str(e.ticket_id) if e.ticket_id else None,
        "target_company_id": str(e.target_company_id) if e.target_company_id else None,
        "target_user_id": str(e.target_user_id) if e.target_user_id else None,
        "before": e.before,
        "after": e.after,
        "reason": e.reason,
        "result": e.result,
    }


def _names(entries: list[SystemJournalEntry]) -> dict[UUID, str]:
    from c4rsys.accounts import services as accounts

    return accounts.names({e.actor_user_id for e in entries if e.actor_user_id})


@require_GET
def journal(request: HttpRequest) -> JsonResponse:
    require_staff(request, "journal.view")
    entries = list(_journal_query(request)[:300])
    names = _names(entries)
    return JsonResponse({"entries": [_journal_payload(e, names) for e in entries]})


@require_GET
def journal_export(request: HttpRequest) -> HttpResponse:
    staff = require_staff(request, "journal.view", "journal.export")
    entries = list(_journal_query(request)[:10000])
    logged_read(
        request,
        staff,
        "journal.exported",
        object_type="system_journal",
        object_id="export",
        after={"rows": len(entries), "filters": dict(request.GET)},
    )
    names = _names(entries)
    out = io.StringIO()
    writer = csv.writer(out)
    columns = [
        "occurred_at",
        "actor",
        "system_process",
        "roles",
        "action",
        "object_type",
        "object_id",
        "privileged_session_id",
        "ticket_id",
        "reason",
        "result",
    ]
    writer.writerow(columns)
    for e in entries:
        row = _journal_payload(e, names)
        writer.writerow([",".join(row[c]) if c == "roles" else row[c] or "" for c in columns])
    response = HttpResponse(out.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="system-journal.csv"'
    response["Cache-Control"] = "private, no-store"
    return response


# --- Privileged sessions (§ 24.3) ----------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def session_list(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, "privileged.open")
    if request.method == "GET":
        return JsonResponse(
            {"sessions": [privileged.payload(s) for s in privileged.sessions_of(staff)]}
        )
    data = parse_json(request)
    target_type = get_str(data, "target_type", max_length=10)
    target = get_str(data, "target", max_length=40)
    if target_type == "company":
        company, user = directory.find_company(target), None
    elif target_type == "user":
        company, user = None, directory.find_user(target)
    else:
        raise errors.validation_error("target_type")
    category = get_str(data, "reason_category", max_length=40)
    reason = get_str(data, "reason", max_length=REASON_MAX)
    number = data.get("ticket_number")
    if number is not None and (isinstance(number, bool) or not isinstance(number, int)):
        raise errors.validation_error("ticket_number", code="INTEGER_REQUIRED")
    ticket = tickets.by_number(number) if number is not None else None
    raw = data.get("scopes", [])
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise errors.validation_error("scopes")
    scopes = privileged.check_scopes(staff, raw)

    def handler(ctx: StaffContext) -> CommandResult:
        s = privileged.open_session(
            ctx,
            company=company,
            user=user,
            category=category,
            reason=reason,
            ticket=ticket,
            scopes=scopes,
        )
        return CommandResult(201, {"session": privileged.payload(s)})

    payload = {
        "target_type": target_type,
        "target": target,
        "reason_category": category,
        "reason": reason,
        "ticket_number": number,
        "scopes": scopes,
    }
    return run_staff(request, "privileged.open", staff, payload, handler)


@require_GET
def session_detail(request: HttpRequest, session_id: UUID) -> JsonResponse:
    staff = require_staff(request, "privileged.open")
    return JsonResponse({"session": privileged.payload(privileged.get(staff, session_id))})


def _session_command(
    request: HttpRequest, session_id: UUID, command: str, payload: dict[str, Any], change: Any
) -> JsonResponse:
    staff = require_staff(request)

    def handler(ctx: StaffContext) -> CommandResult:
        s = privileged.locked(staff, session_id)
        change(ctx, s)
        return CommandResult(200, {"session": privileged.payload(s)})

    return run_staff(request, command, staff, {"session": str(session_id), **payload}, handler)


@require_POST
def session_close(request: HttpRequest, session_id: UUID) -> JsonResponse:
    return _session_command(request, session_id, "privileged.close", {}, privileged.close)


@require_POST
def session_defer(request: HttpRequest, session_id: UUID) -> JsonResponse:
    reason = _reason(request)
    return _session_command(
        request,
        session_id,
        "privileged.notice_defer",
        {"reason": reason},
        lambda ctx, s: privileged.defer_notice(ctx, s, reason),
    )


@require_POST
def session_release(request: HttpRequest, session_id: UUID) -> JsonResponse:
    return _session_command(
        request, session_id, "privileged.notice_release", {}, privileged.release_notice
    )


# The read-only sections of a company inside a privileged session. Each uses the read functions
# and the payloads of the module that owns the data. Money in orders and rentals is shown only
# with the «finance» area.
FINANCIAL_KEYS = ("amounts", "estimated", "calculation", "deposit", "manual_charges")


def _without_money(body: dict[str, Any], s: Any) -> dict[str, Any]:
    if "finance" in s.scopes:
        return body
    return {k: v for k, v in body.items() if k not in FINANCIAL_KEYS}


def _section(
    request: HttpRequest, session_id: UUID, section: str, scope: str = "view", object_id: Any = ""
) -> tuple[Staff, Any]:
    staff = require_staff(request, "privileged.open")
    s = privileged.require_active(staff, session_id)
    if s.company_id is None and section != "user_profile":
        raise errors.not_found()
    privileged.record(request, staff, s, section, scope=scope, object_id=object_id)
    return staff, s


@require_GET
def pv_company(request: HttpRequest, session_id: UUID) -> JsonResponse:
    from c4rsys.companies.views import _company_payload

    _staff, s = _section(request, session_id, "company_profile")
    return JsonResponse({"company": _company_payload(privileged.company_of(s), None)})


@require_GET
def pv_employees(request: HttpRequest, session_id: UUID) -> JsonResponse:
    from c4rsys.companies import staff as company_staff
    from c4rsys.companies.staff_views import _employee_payload

    _staff, s = _section(request, session_id, "employees")
    items = [_employee_payload(e) for e in company_staff.employments_of(s.company_id)]
    return JsonResponse({"employees": items})


@require_GET
def pv_vehicles(request: HttpRequest, session_id: UUID) -> JsonResponse:
    from c4rsys.fleet import services as fleet
    from c4rsys.fleet.views import _payload

    _staff, s = _section(request, session_id, "vehicles")
    return JsonResponse({"vehicles": [_payload(c) for c in fleet.cards_of(s.company_id)[:200]]})


@require_GET
def pv_clients(request: HttpRequest, session_id: UUID) -> JsonResponse:
    from c4rsys.parties import services as parties
    from c4rsys.parties.views import _payload

    _staff, s = _section(request, session_id, "clients")
    text = request.GET.get("q", "")[:100]
    return JsonResponse(
        {"clients": [_payload(c) for c in parties.search(s.company_id, text)[:200]]}
    )


@require_GET
def pv_orders(request: HttpRequest, session_id: UUID) -> JsonResponse:
    from c4rsys.orders import services as orders
    from c4rsys.orders.views import _payload

    _staff, s = _section(request, session_id, "orders")
    items = [
        _without_money(_payload(o, orders.operations_of(o)), s)
        for o in orders.orders_of(s.company_id)[:100]
    ]
    return JsonResponse({"orders": items})


@require_GET
def pv_order(request: HttpRequest, session_id: UUID, order_id: UUID) -> JsonResponse:
    from c4rsys.orders import services as orders
    from c4rsys.orders.views import _payload

    _staff, s = _section(request, session_id, "orders", object_id=order_id)
    order = orders.get_order(s.company_id, order_id)
    return JsonResponse({"order": _without_money(_payload(order, orders.operations_of(order)), s)})


@require_GET
def pv_rentals(request: HttpRequest, session_id: UUID) -> JsonResponse:
    from c4rsys.rentals import rentals
    from c4rsys.rentals.views import _rental_payload

    _staff, s = _section(request, session_id, "rentals")
    items = [_without_money(_rental_payload(r), s) for r in rentals.rentals_of(s.company_id)[:100]]
    return JsonResponse({"rentals": items})


@require_GET
def pv_rental(request: HttpRequest, session_id: UUID, rental_id: UUID) -> JsonResponse:
    from c4rsys.rentals import rentals
    from c4rsys.rentals.views import _rental_payload

    _staff, s = _section(request, session_id, "rentals", object_id=rental_id)
    rental = rentals.get_rental(s.company_id, rental_id)
    return JsonResponse({"rental": _without_money(_rental_payload(rental), s)})


@require_GET
def pv_documents(request: HttpRequest, session_id: UUID) -> JsonResponse:
    from c4rsys.documents import services as documents
    from c4rsys.documents.views import _document_payload

    _staff, s = _section(request, session_id, "documents", scope="documents")
    items = documents.documents_of(s.company_id).order_by("-number")[:200]
    return JsonResponse({"documents": [_document_payload(d) for d in items]})


@require_GET
def pv_document_file(request: HttpRequest, session_id: UUID, document_id: UUID) -> HttpResponse:
    """The file of the current version; a download is journaled (§ 24.3.1)."""
    from c4rsys.documents import services as documents
    from c4rsys.files import services as files

    staff = require_staff(request, "privileged.open")
    s = privileged.require_active(staff, session_id)
    if s.company_id is None:
        raise errors.not_found()
    # Both the documents and the files areas are needed for a download.
    for scope in ("documents", "files"):
        if scope not in s.scopes:
            raise errors.ApiError("PRIVILEGED_SCOPE_DENIED", status=403, params={"scope": scope})
    d = documents.documents_of(s.company_id).filter(pk=document_id).first()
    version = documents.current_version(d) if d is not None else None
    item = (
        files.of_company(s.company_id, version.file_id)
        if version is not None and version.file_id
        else None
    )
    if item is None:
        raise errors.not_found()
    privileged.record(
        request, staff, s, "documents", scope="files", object_id=document_id, download=True
    )
    response = HttpResponse(files.content(item), content_type=item.content_type)
    response["Cache-Control"] = "private, no-store"
    response["Content-Disposition"] = "attachment"
    return response


@require_GET
def pv_finance(request: HttpRequest, session_id: UUID) -> JsonResponse:
    from c4rsys.finance import records
    from c4rsys.finance import services as finance
    from c4rsys.finance.views import _account_payload

    _staff, s = _section(request, session_id, "finance", scope="finance")
    balances = records.balances(s.company_id)
    items = [
        _account_payload(a, balances.get(a.id), with_balance=True)
        for a in finance.accounts_of(s.company_id)
    ]
    return JsonResponse({"cash_accounts": items})


@require_GET
def pv_user(request: HttpRequest, session_id: UUID) -> JsonResponse:
    """The profile of the user of the session with the full contacts."""
    _staff, s = _section(request, session_id, "user_profile")
    user = privileged.user_of(s)
    return JsonResponse(
        {"user": {**directory.user_card(user), "contacts": directory.user_contacts(user)}}
    )


# --- System blocks (§ 24.2) ----------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def block_list(request: HttpRequest) -> JsonResponse:
    staff = require_staff(request, "block.manage")
    if request.method == "GET":
        active = request.GET.get("active") == "1"
        return JsonResponse(
            {"blocks": [directory.block_payload(b) for b in blocking.blocks(active)]}
        )
    data = parse_json(request)
    target_type = get_str(data, "target_type", max_length=10)
    target = get_str(data, "target", max_length=40)
    if target_type == "company":
        company, user = directory.find_company(target), None
    elif target_type == "user":
        company, user = None, directory.find_user(target)
    else:
        raise errors.validation_error("target_type")
    category = get_str(data, "category", max_length=40)
    comment = get_str(data, "comment", max_length=REASON_MAX)
    disclosed = get_str(data, "disclosed_reason", max_length=REASON_MAX, required=False)
    ends_at = _moment(data, "ends_at")
    defer_reason = get_str(data, "defer_reason", max_length=REASON_MAX, required=False)

    def handler(ctx: StaffContext) -> CommandResult:
        found = blocking.block(
            ctx,
            company=company,
            user=user,
            category=category,
            comment=comment,
            disclosed_reason=disclosed,
            ends_at=ends_at,
            defer_reason=defer_reason,
        )
        return CommandResult(201, {"block": directory.block_payload(found)})

    payload = {
        "target_type": target_type,
        "target": target,
        "category": category,
        "comment": comment,
        "disclosed_reason": disclosed,
        "ends_at": ends_at.isoformat() if ends_at else None,
        "defer_reason": defer_reason,
    }
    return run_staff(request, "block.create", staff, payload, handler)


@require_POST
def block_restore(request: HttpRequest, block_id: UUID) -> JsonResponse:
    staff = require_staff(request, "block.manage")
    reason = _reason(request)

    def handler(ctx: StaffContext) -> CommandResult:
        found = blocking.restore(ctx, blocking.locked(block_id), reason)
        return CommandResult(200, {"block": directory.block_payload(found)})

    return run_staff(
        request, "block.restore", staff, {"block": str(block_id), "reason": reason}, handler
    )


@require_POST
def block_release(request: HttpRequest, block_id: UUID) -> JsonResponse:
    staff = require_staff(request, "block.manage")

    def handler(ctx: StaffContext) -> CommandResult:
        found = blocking.release_notice(ctx, blocking.locked(block_id))
        return CommandResult(200, {"block": directory.block_payload(found)})

    return run_staff(request, "block.notice_release", staff, {"block": str(block_id)}, handler)
