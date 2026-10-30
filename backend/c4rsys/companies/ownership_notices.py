"""Mandatory notifications of the ownership requests (ТЗ § 20.3.4, § 3.4.1, § 3.6.2).

* A confirmation of a share transfer is told to the person who gives it: a user in a company where
  he works (the company of the share, then the sending or receiving company), a party company to
  its top authority and employees with full rights. Everyone in the request is told; one answer is
  not enough (§ 3.4.1). A user without any employment sees the request in the personal area only.
* An owner's approval of a deactivation or restoration is told the same way (§ 3.6.2).
The notification lasts until the decision, the end or the expiry of the request (§ 20.3.3).
"""

from datetime import datetime
from uuid import UUID

from c4rsys.companies import access, deactivation, ownership
from c4rsys.companies.models import (
    CompanyStateRequest,
    Employment,
    ShareTransfer,
    ShareTransferApproval,
)

TRANSFER = "ownership.approval_requested"
STATE = "company.state_approval_requested"


def _recipients(party: ownership.Party, places: list[UUID]) -> tuple[UUID, list[UUID]] | None:
    """The company of the notification and its employments to tell."""
    if party.company is not None:
        heads = [
            e.id
            for e in Employment.objects.select_related("position").filter(
                company_id=party.company.id, state=Employment.State.ACTIVE, user__isnull=False
            )
            if access.has_full_rights(e)
        ]
        return (party.company.id, heads) if heads else None
    if party.user is None:
        raise ValueError("party.user")
    for company_id in places:
        employment = Employment.objects.filter(
            company_id=company_id, user=party.user, state=Employment.State.ACTIVE
        ).first()
        if employment is not None:
            return company_id, [employment.id]
    return None


def run(now: datetime) -> int:
    from c4rsys.notifications import rules
    from c4rsys.notifications import services as notifications
    from c4rsys.notifications.models import Notification

    changed = 0
    awaited: set[str] = set()
    for a in ShareTransferApproval.objects.select_related(
        "transfer", "transfer__company", "party_user", "party_company"
    ).filter(
        state=ShareTransferApproval.State.PENDING, transfer__state=ShareTransfer.State.PENDING
    ):
        t = a.transfer
        if ownership.effective_state(t, now) != ShareTransfer.State.PENDING:
            continue
        places = [x for x in (t.company_id, t.sender_company_id, t.recipient_company_id) if x]
        found = _recipients(ownership.Party(user=a.party_user, company=a.party_company), places)
        if found is None:
            continue
        company_id, people = found
        condition = f"{TRANSFER}:{a.id}"
        awaited.add(condition)
        changed += rules.raise_event(
            company_id,
            TRANSFER,
            condition=condition,
            params={"company": t.company.display_name, "percent": str(t.percent), "role": a.role},
            recipients=people,
            now=now,
        )
    for request in CompanyStateRequest.objects.select_related("company").filter(
        state=CompanyStateRequest.State.PENDING
    ):
        decided = deactivation.tally(request).parties
        for party in ownership.owners_of(request.company_id):
            if party.key in decided:
                continue
            found = _recipients(party, [request.company_id])
            if found is None:
                continue
            company_id, people = found
            condition = f"{STATE}:{request.id}:{party.key}"
            awaited.add(condition)
            changed += rules.raise_event(
                company_id,
                STATE,
                condition=condition,
                params={"company": request.company.display_name, "kind": request.kind},
                recipients=people,
                now=now,
            )
    for n in Notification.objects.filter(
        event__in=(TRANSFER, STATE), state=Notification.State.ACTIVE
    ):
        if n.condition not in awaited:
            notifications.resolve(n.company_id, n.condition, "decided", now)
            changed += 1
    return changed
