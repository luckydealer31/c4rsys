"""Support tickets registered by hand (ТЗ § 24.1.3, § 24.1.7).

States: new, in progress, waiting for an answer, closed. A closed ticket keeps its result; it is
opened again by a new change of state (PROPOSAL: closed → in progress). Comments and recorded
confirmations are added, never changed.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.accounts.models import UserAccount
from c4rsys.backoffice.core import StaffContext, next_number
from c4rsys.backoffice.models import SupportTicket, SystemStaffMember, TicketComment, TicketLink
from c4rsys.companies.models import Company
from c4rsys.platform import errors

State = SupportTicket.State

# PROPOSAL: any open state moves to any other; a closed ticket only back to «in progress».
TRANSITIONS: dict[str, tuple[str, ...]] = {
    State.NEW: (State.IN_PROGRESS, State.WAITING, State.CLOSED),
    State.IN_PROGRESS: (State.WAITING, State.CLOSED),
    State.WAITING: (State.IN_PROGRESS, State.CLOSED),
    State.CLOSED: (State.IN_PROGRESS,),
}


@dataclass(frozen=True)
class NewTicket:
    received_on: date
    channel: str
    applicant_name: str
    applicant_contact: str
    subject: str
    description: str


def _name(user: UserAccount) -> str:
    return " ".join(x for x in (user.last_name, user.first_name) if x)


def link_payload(link: TicketLink) -> dict[str, Any]:
    body: dict[str, Any] = {"id": str(link.id), "kind": link.kind}
    if link.kind == TicketLink.Kind.COMPANY:
        company = Company.objects.filter(pk=link.company_id).first() if link.company_id else None
        body["company"] = (
            {"public_id": company.public_id, "display_name": company.display_name}
            if company
            else None
        )
    elif link.kind == TicketLink.Kind.USER:
        user = UserAccount.objects.filter(pk=link.user_id).first() if link.user_id else None
        body["user"] = {"public_id": user.public_id, "name": _name(user)} if user else None
    else:
        body["record"] = {"type": link.record_type, "ref": link.record_ref}
    return body


def payload(t: SupportTicket, *, detail: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(t.id),
        "number": t.number,
        "received_on": t.received_on.isoformat(),
        "channel": t.channel,
        "applicant_name": t.applicant_name,
        "applicant_contact": t.applicant_contact,
        "subject": t.subject,
        "state": t.state,
        "assignee": {
            "id": str(t.assignee.id),
            "name": _name(t.assignee.user),
        }
        if t.assignee
        else None,
        "created_at": t.created_at.isoformat(),
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "version": t.version,
    }
    if detail:
        body["description"] = t.description
        body["result"] = t.result
        body["links"] = [link_payload(x) for x in t.links.order_by("created_at")]
        body["comments"] = [
            {
                "id": str(c.id),
                "kind": c.kind,
                "text": c.text,
                "author": _name(c.author.user),
                "created_at": c.created_at.isoformat(),
            }
            for c in t.comments.select_related("author__user").order_by("created_at")
        ]
    return body


def tickets(state: str = "") -> QuerySet[SupportTicket]:
    query = SupportTicket.objects.select_related("assignee__user").order_by("-number")
    if state:
        query = query.filter(state=state)
    return query


def get(ticket_id: UUID) -> SupportTicket:
    found = SupportTicket.objects.select_related("assignee__user").filter(pk=ticket_id).first()
    if found is None:
        raise errors.not_found()
    return found


def by_number(number: int) -> SupportTicket:
    found = SupportTicket.objects.filter(number=number).first()
    if found is None:
        raise errors.validation_error("ticket_number", code="TICKET_UNKNOWN")
    return found


def locked(ticket_id: UUID, expected: int | None) -> SupportTicket:
    found = SupportTicket.objects.select_for_update().filter(pk=ticket_id).first()
    if found is None:
        raise errors.not_found()
    if expected is not None and found.version != expected:
        raise errors.version_conflict()
    return found


def _snapshot(t: SupportTicket) -> dict[str, Any]:
    return {
        "state": t.state,
        "assignee": str(t.assignee_id) if t.assignee_id else None,
        "result": t.result,
    }


def create(ctx: StaffContext, data: NewTicket) -> SupportTicket:
    t = SupportTicket.objects.create(
        number=next_number("ticket"),
        received_on=data.received_on,
        channel=data.channel,
        applicant_name=data.applicant_name,
        applicant_contact=data.applicant_contact,
        subject=data.subject,
        description=data.description,
        created_by_user_id=ctx.staff.user.id,
    )
    ctx.journal(
        "ticket.created",
        object_type="support_ticket",
        object_id=t.id,
        ticket_id=t.id,
        after={"number": t.number, "subject": t.subject, "received_on": t.received_on},
    )
    return t


def set_state(ctx: StaffContext, t: SupportTicket, state: str, result: str) -> SupportTicket:
    if state not in TRANSITIONS.get(t.state, ()):
        raise errors.business_rule("TICKET_STATE_INVALID", state=t.state)
    if state == State.CLOSED and not (result or t.result):
        raise errors.validation_error("result", code="TICKET_RESULT_REQUIRED")
    before = _snapshot(t)
    t.state = state
    if result:
        t.result = result
    t.closed_at = timezone.now() if state == State.CLOSED else None
    t.version += 1
    t.save()
    ctx.journal(
        "ticket.state_changed",
        object_type="support_ticket",
        object_id=t.id,
        ticket_id=t.id,
        before=before,
        after=_snapshot(t),
    )
    return t


def assign(ctx: StaffContext, t: SupportTicket, member_id: UUID | None) -> SupportTicket:
    member = None
    if member_id is not None:
        member = SystemStaffMember.objects.filter(
            pk=member_id, state=SystemStaffMember.State.ACTIVE
        ).first()
        if member is None:
            raise errors.validation_error("assignee", code="STAFF_MEMBER_UNKNOWN")
    before = _snapshot(t)
    t.assignee = member
    t.version += 1
    t.save()
    ctx.journal(
        "ticket.assigned",
        object_type="support_ticket",
        object_id=t.id,
        ticket_id=t.id,
        before=before,
        after=_snapshot(t),
    )
    return t


def comment(ctx: StaffContext, t: SupportTicket, kind: str, text: str) -> TicketComment:
    found = TicketComment.objects.create(ticket=t, kind=kind, text=text, author=ctx.staff.member)
    t.version += 1
    t.save(update_fields=["version"])
    ctx.journal(
        f"ticket.{kind}_added",
        object_type="support_ticket",
        object_id=t.id,
        ticket_id=t.id,
        after={"comment": str(found.id), "kind": kind},
    )
    return found


def link(
    ctx: StaffContext,
    t: SupportTicket,
    kind: str,
    *,
    company: Company | None = None,
    user: UserAccount | None = None,
    record_type: str = "",
    record_ref: str = "",
) -> TicketLink:
    found = TicketLink.objects.create(
        ticket=t,
        kind=kind,
        company_id=company.id if company else None,
        user_id=user.id if user else None,
        record_type=record_type,
        record_ref=record_ref,
        created_by_user_id=ctx.staff.user.id,
    )
    t.version += 1
    t.save(update_fields=["version"])
    ctx.journal(
        "ticket.linked",
        object_type="support_ticket",
        object_id=t.id,
        ticket_id=t.id,
        target_company_id=company.id if company else None,
        target_user_id=user.id if user else None,
        after=link_payload(found),
    )
    return found
