"""The catalogue of events and the company's rules (ТЗ § 19.1.9, § 20.3.1, § 20.3.4–20.3.6;
customer's decisions З-7, З-8, З-9 of 29.10.2026).

* Mandatory events cannot be switched off and their frequency is set by c4rsys (§ 20.3.4).
* Working events are on in a new company with the moments of decision З-7; the company switches
  each off or changes the moment and the repetition (§ 20.3.5–20.3.6).
* Automatic tasks of § 19.1.9 are off by default (decision З-8).
* Chosen recipients count only while they have access to the object (§ 20.3.6); without them the
  order of § 20.2.2 applies with the right of the event (decision З-9).
* Instant events are read from the journal of actions, so the modules raise nothing themselves:
  the journal is written in the same transaction as every operation.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.notifications import services
from c4rsys.notifications.models import JournalCursor, NotificationRule
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import AuditEvent

Kind = services.Kind


@dataclass(frozen=True)
class Event:
    kind: str
    permission: str | None
    mandatory: bool = False
    lead_minutes: int = 0
    repeat_minutes: int = 0
    # Whether the moment before the occasion can be set (upcoming events).
    has_lead: bool = False
    # Whether § 19.1.9 allows an automatic task for it.
    task: bool = False


HOUR = 60
EVENTS: dict[str, Event] = {
    # Mandatory (§ 20.3.4).
    "employee.access_changed": Event(Kind.INFO, None, mandatory=True),
    "document.signing_requested": Event(Kind.ACTION, "documents.sign", mandatory=True),
    "document.signing_result": Event(Kind.INFO, "documents.view", mandatory=True),
    "rental.exception_pending": Event(Kind.ACTION, "rental.exception.decide", mandatory=True),
    # Requests to link with the company's profile and the answers (§ 6.3.8, part К4).
    "link.requested": Event(Kind.ACTION, "company.link.answer", mandatory=True),
    "link.answered": Event(Kind.INFO, None, mandatory=True),
    # Requests to the owner of a vehicle and the answers (§ 8.3.1, part К5).
    "vehicle.owner_requested": Event(Kind.ACTION, "company.link.answer", mandatory=True),
    "vehicle.owner_answered": Event(Kind.INFO, None, mandatory=True),
    "rental.inspection_missing": Event(
        Kind.ACTION, "rental.complete_formalization", mandatory=True
    ),
    # § 10.3.3: a required check not done is a conflict and a notification. PROPOSAL: mandatory
    # like the missing inspection of decision З-11, to the employees who close the rental.
    "rental.inspection_check_missing": Event(
        Kind.ACTION, "rental.complete_formalization", mandatory=True
    ),
    # Working, by the company (§ 20.3.5; defaults of decision З-7).
    "rental.issue_upcoming": Event(Kind.REMINDER, "rental.issue", lead_minutes=HOUR, has_lead=True),
    "rental.return_upcoming": Event(
        Kind.REMINDER, "rental.accept_return", lead_minutes=HOUR, has_lead=True
    ),
    "rental.return_overdue": Event(Kind.WARNING, "rental.accept_return", repeat_minutes=2 * HOUR),
    "rental.booking_cancelled": Event(Kind.INFO, "rental.book"),
    "service.maintenance_due": Event(Kind.WARNING, "service.create", task=True),
    "service.works_done": Event(Kind.INFO, "service.view"),
    "warehouse.expiry": Event(
        Kind.WARNING, "warehouse.view", lead_minutes=7 * 24 * HOUR, has_lead=True
    ),
    "finance.debt": Event(Kind.WARNING, "finance.account.view", task=True),
    "task.assigned": Event(Kind.INFO, None),
}


@dataclass(frozen=True)
class Rule:
    event: str
    enabled: bool
    lead_minutes: int
    repeat_minutes: int
    recipient_ids: list[UUID] = field(default_factory=list)
    auto_task: bool = False
    version: int = 0


def rule(company_id: UUID, event: str) -> Rule:
    spec = EVENTS[event]
    stored = NotificationRule.objects.filter(company_id=company_id, event=event).first()
    if spec.mandatory or stored is None:
        return Rule(event, True, spec.lead_minutes, spec.repeat_minutes)
    return Rule(
        event,
        stored.enabled,
        stored.lead_minutes,
        stored.repeat_minutes,
        [UUID(x) for x in stored.recipient_ids],
        stored.auto_task and spec.task,
        stored.version,
    )


def rules_of(company_id: UUID) -> list[Rule]:
    return [rule(company_id, code) for code in EVENTS]


def set_rule(
    ctx: CommandContext,
    company: Company,
    event: str,
    *,
    enabled: bool,
    lead_minutes: int,
    repeat_minutes: int,
    recipient_ids: list[UUID],
    auto_task: bool,
    expected: int,
) -> Rule:
    spec = EVENTS.get(event)
    if spec is None:
        raise errors.validation_error("event")
    if spec.mandatory:
        raise errors.business_rule("NOTIFICATION_MANDATORY")
    if lead_minutes > 60 * 24 * 90 or repeat_minutes > 60 * 24 * 30:
        raise errors.validation_error("lead_minutes")
    if auto_task and not spec.task:
        raise errors.validation_error("auto_task")
    known = set(services.active_employments(company.id))
    if not set(recipient_ids) <= known:
        raise errors.validation_error("recipient_ids", code="NOTIFICATION_RECIPIENT_UNKNOWN")
    found = (
        NotificationRule.objects.select_for_update().filter(company=company, event=event).first()
    )
    if (found.version if found else 0) != expected:
        raise errors.version_conflict()
    before = rule(company.id, event)
    found = found or NotificationRule(company=company, event=event, version=0)
    found.enabled = enabled
    found.lead_minutes = lead_minutes if spec.has_lead else 0
    found.repeat_minutes = repeat_minutes
    found.recipient_ids = [str(x) for x in recipient_ids]
    found.auto_task = auto_task
    found.updated_by_user_id = ctx.actor.user_id
    found.updated_at = timezone.now()
    found.version += 1
    found.save()
    ctx.audit(
        "notification.rule_changed",
        object_type="notification_rule",
        object_id=found.id,
        before={
            "enabled": before.enabled,
            "lead": before.lead_minutes,
            "repeat": before.repeat_minutes,
        },
        after={
            "event": event,
            "enabled": enabled,
            "lead": found.lead_minutes,
            "repeat": repeat_minutes,
            "recipients": found.recipient_ids,
            "auto_task": auto_task,
        },
    )
    return rule(company.id, event)


def recipients_for(
    company_id: UUID,
    event: str,
    *,
    location_id: UUID | None = None,
    responsible: Iterable[UUID | None] = (),
) -> list[UUID]:
    spec = EVENTS[event]
    chosen = rule(company_id, event).recipient_ids
    if chosen and spec.permission is not None:
        allowed = set(services.with_permission(company_id, spec.permission, location_id))
        chosen = [x for x in chosen if x in allowed]
    if chosen:
        return chosen
    return services.recipients(
        company_id, responsible=responsible, permission=spec.permission, location_id=location_id
    )


def raise_event(
    company_id: UUID,
    event: str,
    *,
    condition: str,
    location_id: UUID | None = None,
    object_type: str = "",
    object_id: UUID | None = None,
    params: dict[str, object] | None = None,
    responsible: Iterable[UUID | None] = (),
    recipients: list[UUID] | None = None,
    now: datetime | None = None,
    done: bool = False,
) -> bool:
    """Raises the event by the company's rule; repeats it by the rule's interval. `done` — an
    instant event with no cause to remove: it stays in the history, done at once."""
    current = rule(company_id, event)
    if not current.enabled:
        services.resolve(company_id, condition, "rule_disabled")
        return False
    moment = now or timezone.now()
    existing = services.active(company_id, condition)
    if existing is not None:
        due = (
            current.repeat_minutes
            and (moment - existing.last_at).total_seconds() >= current.repeat_minutes * 60
        )
        if not due:
            return False
    people = (
        recipients
        if recipients is not None
        else recipients_for(company_id, event, location_id=location_id, responsible=responsible)
    )
    if not people:
        return False
    services.notify(
        company_id,
        condition=condition,
        event=event,
        kind=EVENTS[event].kind,
        recipients=people,
        location_id=location_id,
        object_type=object_type,
        object_id=object_id,
        params=params,
        repeat=existing is not None,
        now=moment,
    )
    if done:
        services.resolve(company_id, condition, "instant", moment)
    return True


# --- Instant events from the journal of actions --------------------------------------------------

Handler = Callable[[AuditEvent], int]
_journal: dict[str, list[Handler]] = {}
CURSOR = "events"


def on_journal(action: str, handler: Handler) -> None:
    _journal.setdefault(action, []).append(handler)


def read_journal(now: datetime) -> int:
    """New journal entries of the watched actions since the last reading; the first reading
    starts from now, without the past."""
    with transaction.atomic():
        cursor, created = JournalCursor.objects.select_for_update().get_or_create(name=CURSOR)
        if created or cursor.last_id is None:
            last = AuditEvent.objects.order_by("-id").values_list("id", flat=True).first()
            cursor.last_id = last
            cursor.save()
            return 0
        # Entries of the last two minutes wait: a longer transaction may still commit one
        # with an earlier id.
        entries = list(
            AuditEvent.objects.filter(
                id__gt=cursor.last_id, occurred_at__lte=now - timedelta(minutes=2)
            ).order_by("id")[:5000]
        )
        changed = 0
        for entry in entries:
            for handler in _journal.get(entry.action, []):
                changed += handler(entry)
        if entries:
            cursor.last_id = entries[-1].id
            cursor.save()
    return changed


def on_access_changed(entry: AuditEvent) -> int:
    """The employee whose access changed is told (§ 20.3.4)."""
    if entry.company_id is None or entry.object_type != "employment":
        return 0
    return int(
        raise_event(
            entry.company_id,
            "employee.access_changed",
            condition=f"employee.access_changed:{entry.id}",
            params={"action": entry.action},
            recipients=[UUID(entry.object_id)],
            done=True,
        )
    )
