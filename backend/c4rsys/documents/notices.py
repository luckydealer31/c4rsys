"""Warnings of the term of documents in the notification centre (ТЗ § 18.1.13, § 20.3.6;
decision Р-7): one lasting warning from `warn_days` before the end, without repetitions, until a
new version or the archive removes the cause. The employees who may view the document in its
scope receive it (§ 20.2.2, decision З-9); without them — the heads of the company."""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from c4rsys.documents import services
from c4rsys.documents.models import Document
from c4rsys.notifications import services as notifications
from c4rsys.notifications.models import Notification

EVENT = "document.term"


def _condition(d: Document) -> str:
    return f"{EVENT}:{d.id}"


def _people(d: Document) -> list[UUID]:
    if d.all_locations:
        return notifications.recipients(d.company_id, permission="documents.view")
    found: set[UUID] = set()
    for location_id in services.location_ids(d):
        found |= set(notifications.with_permission(d.company_id, "documents.view", location_id))
    return sorted(found) or notifications.heads(d.company_id)


def run(now: datetime) -> int:
    changed = 0
    candidates = Document.objects.select_related("company").filter(
        state=Document.State.ACTIVE, valid_until__isnull=False, warn_days__isnull=False
    )
    warned = set()
    for d in candidates:
        today = now.astimezone(ZoneInfo(d.company.timezone or "UTC")).date()
        state = services.warning(d, today)
        if state is None:
            continue
        warned.add(d.id)
        current = notifications.active(d.company_id, _condition(d))
        params = {
            "number": d.number,
            "title": d.title,
            "valid_until": d.valid_until.isoformat() if d.valid_until else None,
            "state": state,
        }
        if current is not None and current.params == params:
            continue
        if current is not None:
            # «Expiring» became «expired»: a new step of the same warning, not a repetition.
            notifications.resolve(d.company_id, current.condition, "document_term_changed", now)
        notifications.notify(
            d.company_id,
            condition=_condition(d),
            event=EVENT,
            kind=notifications.Kind.WARNING,
            recipients=_people(d),
            object_type="document",
            object_id=d.id,
            params=params,
            now=now,
        )
        changed += 1
    for notice in Notification.objects.filter(event=EVENT, state=Notification.State.ACTIVE):
        if notice.object_id not in warned:
            notifications.resolve(notice.company_id, notice.condition, "document_term_removed", now)
            changed += 1
    return changed
