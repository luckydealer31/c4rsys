"""Reading the company journal: history of an object and the journal of an employee
(ТЗ § 23.10, § 24.4.5). The journal is append-only; reading never changes it."""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet

from c4rsys.accounts.models import UserAccount
from c4rsys.companies.models import Employment
from c4rsys.platform.models import AuditEvent

LIMIT = 200

# Actions whose «before / after» carry money (§ 23.10.3).
FINANCIAL_PREFIXES = ("payment.", "charge.")
FINANCIAL_ACTIONS = frozenset({"order.closed", "rental.control_version"})


def is_financial(action: str) -> bool:
    return action.startswith(FINANCIAL_PREFIXES) or action in FINANCIAL_ACTIONS


def object_history(company_id: UUID, objects: Iterable[tuple[str, str]]) -> QuerySet[AuditEvent]:
    """Events of the given objects in the company, oldest first."""
    match = Q()
    for object_type, object_id in objects:
        match |= Q(object_type=object_type, object_id=str(object_id))
    if not match:
        return AuditEvent.objects.none()
    return AuditEvent.objects.filter(match, company_id=company_id).order_by("occurred_at", "id")


def employee_journal(
    company_id: UUID,
    employment_id: UUID,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    section: str = "",
    action: str = "",
) -> QuerySet[AuditEvent]:
    """Actions of one employee in this company, newest first; filters by period, section and
    action type (§ 23.10.2). The journal stays after the employee leaves (§ 23.10.1)."""
    events = AuditEvent.objects.filter(company_id=company_id, actor_employment_id=employment_id)
    if since is not None:
        events = events.filter(occurred_at__gte=since)
    if until is not None:
        events = events.filter(occurred_at__lt=until)
    if section:
        events = events.filter(action__startswith=f"{section}.")
    if action:
        events = events.filter(action=action)
    return events.order_by("-occurred_at", "-id")


def _actors(events: list[AuditEvent]) -> dict[UUID, dict[str, Any]]:
    users = UserAccount.objects.in_bulk({e.actor_user_id for e in events if e.actor_user_id})
    return {
        user_id: {
            "user_id": user.public_id,
            "name": f"{user.first_name} {user.last_name}".strip(),
        }
        for user_id, user in users.items()
    }


def payload(events: Iterable[AuditEvent], *, show_financial: bool = True) -> list[dict[str, Any]]:
    """Time, author, action, object with its reference, before / after and reason (§ 23.10.2).
    Without a financial right the money details of financial actions are hidden (§ 23.10.3,
    decision 22.10.2026)."""
    items = list(events)[:LIMIT]
    actors = _actors(items)
    result = []
    for event in items:
        hidden = not show_financial and is_financial(event.action)
        actor = actors.get(event.actor_user_id) if event.actor_user_id else None
        result.append(
            {
                "id": str(event.id),
                "occurred_at": event.occurred_at.astimezone(UTC).isoformat(),
                "actor": {
                    **(actor or {"user_id": None, "name": ""}),
                    "employment_id": str(event.actor_employment_id)
                    if event.actor_employment_id
                    else None,
                    "system_process": event.system_process or None,
                },
                "action": event.action,
                "object": {"type": event.object_type, "id": event.object_id},
                "before": None if hidden else event.before,
                "after": None if hidden else event.after,
                "details_hidden": hidden,
                "reason": event.reason,
                "result": event.result,
            }
        )
    return result


def employment_exists(company_id: UUID, employment_id: UUID) -> bool:
    return Employment.objects.filter(company_id=company_id, pk=employment_id).exists()
