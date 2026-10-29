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
from c4rsys.platform.models import AuditEvent

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


# --- Signing (§ 20.3.4: mandatory) ---------------------------------------------------------------


def signing(now: datetime) -> int:
    """«Action required» to an employee assigned to sign for the company while the signature is
    awaited on the last sent version."""
    from c4rsys.documents import signing as signing_service
    from c4rsys.documents.models import DocumentSigner
    from c4rsys.notifications import rules

    changed = 0
    awaited: set[str] = set()
    for signer in DocumentSigner.objects.select_related("document").filter(
        role=DocumentSigner.Role.COMPANY, state=DocumentSigner.State.PENDING
    ):
        d = signer.document
        sent = signing_service.sent_version(d)
        if (
            d.state != Document.State.ACTIVE
            or sent is None
            or sent.id != signer.document_version_id
        ):
            continue
        condition = f"document.signing_requested:{signer.id}"
        awaited.add(condition)
        changed += rules.raise_event(
            d.company_id,
            "document.signing_requested",
            condition=condition,
            object_type="document",
            object_id=d.id,
            params={"number": d.number, "title": d.title},
            recipients=[signer.employment_id] if signer.employment_id else [],
            now=now,
        )
    for n in Notification.objects.filter(
        event="document.signing_requested", state=Notification.State.ACTIVE
    ):
        if n.condition not in awaited:
            notifications.resolve(n.company_id, n.condition, "signing_answered", now)
            changed += 1
    return changed


def on_signing_answer(entry: AuditEvent) -> int:
    """The result of signing to the author of the document (§ 20.3.4)."""
    from c4rsys.companies.models import Employment
    from c4rsys.notifications import rules

    d = Document.objects.filter(pk=entry.object_id).first()
    if d is None:
        return 0
    author = Employment.objects.filter(
        company_id=d.company_id, user_id=d.author_user_id, state=Employment.State.ACTIVE
    ).first()
    if author is None:
        return 0
    after = entry.after or {}
    return int(
        rules.raise_event(
            d.company_id,
            "document.signing_result",
            condition=f"document.signing_result:{entry.id}",
            object_type="document",
            object_id=d.id,
            params={
                "number": d.number,
                "title": d.title,
                "signed": entry.action == "document.signed",
                "role": after.get("role"),
                "comment": after.get("comment", ""),
            },
            recipients=[author.id],
            done=True,
        )
    )
