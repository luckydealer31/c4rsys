"""Raising, repeating and ending notifications; recipients (ТЗ § 20.1–20.3; decisions З-1, З-9).

Other modules raise a notification for a condition and end it when the cause is removed:
* `notify` — the first time creates the active notification of the condition; afterwards adds
  recipients and, for a repetition, keeps it in the history and marks it unread again
  (§ 20.3.2).
* `resolve` — the cause is removed: the notification is done for every recipient (§ 20.3.3).
* `recipients` — the order of § 20.2.2: the responsible ones; without them the employees with the
  right in the location; without them the top-authority director and the full administrators.
* `register_check` — a periodic check of a module, run by the schedule every 5 minutes (З-5).
"""

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from c4rsys.companies import access
from c4rsys.companies.models import Employment
from c4rsys.notifications.models import Notification, NotificationRecipient, NotificationRepeat
from c4rsys.platform.commands import Actor

Kind = Notification.Kind
Check = Callable[[datetime], int]
_checks: list[Check] = []


def register_check(check: Check) -> None:
    _checks.append(check)


def run_checks(now: datetime | None = None) -> int:
    moment = now or timezone.now()
    return sum(check(moment) for check in _checks)


def active(company_id: UUID, condition: str) -> Notification | None:
    return Notification.objects.filter(
        company_id=company_id, condition=condition, state=Notification.State.ACTIVE
    ).first()


def notify(
    company_id: UUID,
    *,
    condition: str,
    event: str,
    kind: str,
    recipients: Iterable[UUID],
    location_id: UUID | None = None,
    object_type: str = "",
    object_id: UUID | None = None,
    params: dict[str, Any] | None = None,
    repeat: bool = False,
    now: datetime | None = None,
    limited: Iterable[UUID] = (),
    limited_params: dict[str, Any] | None = None,
) -> Notification:
    """`limited` — recipients without access to the object: they see `limited_params` only
    (§ 5.2.3). A recipient who is also among `recipients` sees everything."""
    moment = now or timezone.now()
    people = set(recipients)
    restricted = set(limited) - people
    with transaction.atomic():
        found = (
            Notification.objects.select_for_update()
            .filter(company_id=company_id, condition=condition, state=Notification.State.ACTIVE)
            .first()
        )
        if found is None:
            found = Notification.objects.create(
                company_id=company_id,
                location_id=location_id,
                kind=kind,
                event=event,
                condition=condition,
                params=params or {},
                limited_params=limited_params or {},
                object_type=object_type,
                object_id=object_id,
                created_at=moment,
                last_at=moment,
            )
        elif repeat:
            NotificationRepeat.objects.create(
                company_id=company_id, notification=found, at=moment, params=params or {}
            )
            found.repeats += 1
            found.last_at = moment
            found.params = params or found.params
            found.limited_params = limited_params or found.limited_params
            found.save(update_fields=["repeats", "last_at", "params", "limited_params"])
            # A repeated reminder is shown as new to everyone (§ 20.3.2).
            NotificationRecipient.objects.filter(notification=found).update(read_at=None)
        known = dict(
            NotificationRecipient.objects.filter(notification=found).values_list(
                "employment_id", "limited"
            )
        )
        # Who got access meanwhile sees the whole notification.
        upgraded = [x for x in people if known.get(x) is True]
        if upgraded:
            NotificationRecipient.objects.filter(
                notification=found, employment_id__in=upgraded
            ).update(limited=False)
        NotificationRecipient.objects.bulk_create(
            [
                NotificationRecipient(company_id=company_id, notification=found, employment_id=x)
                for x in sorted(people - set(known))
            ]
            + [
                NotificationRecipient(
                    company_id=company_id, notification=found, employment_id=x, limited=True
                )
                for x in sorted(restricted - set(known))
            ]
        )
    return found


def resolve(company_id: UUID, condition: str, reason: str, now: datetime | None = None) -> bool:
    """The cause is removed: done for all recipients; the record and history stay (§ 20.3.3)."""
    count = Notification.objects.filter(
        company_id=company_id, condition=condition, state=Notification.State.ACTIVE
    ).update(state=Notification.State.DONE, done_at=now or timezone.now(), done_reason=reason)
    return count > 0


def resolve_object(company_id: UUID, object_type: str, object_id: UUID, reason: str) -> int:
    return Notification.objects.filter(
        company_id=company_id,
        object_type=object_type,
        object_id=object_id,
        state=Notification.State.ACTIVE,
    ).update(state=Notification.State.DONE, done_at=timezone.now(), done_reason=reason)


# --- Recipients (§ 20.2.2; decision З-9) ---------------------------------------------------------


def _actors(company_id: UUID) -> list[Actor]:
    return [
        Actor(user_id=e.user_id, company_id=company_id, employment_id=e.id)
        for e in Employment.objects.filter(
            company_id=company_id, state=Employment.State.ACTIVE, user__isnull=False
        )
        if e.user_id is not None
    ]


def active_employments(company_id: UUID) -> list[UUID]:
    return list(
        Employment.objects.filter(
            company_id=company_id, state=Employment.State.ACTIVE, user__isnull=False
        ).values_list("id", flat=True)
    )


def with_permission(company_id: UUID, permission: str, location_id: UUID | None) -> list[UUID]:
    """Active employees with the right in the location, or in the whole company."""
    resolver = access.RightsResolver()
    found = []
    for actor in _actors(company_id):
        allowed = (
            resolver.has_permission(actor, permission, location_id=location_id)
            if location_id is not None
            else resolver.has_permission(actor, permission, whole_company=True)
        )
        if allowed and actor.employment_id is not None:
            found.append(actor.employment_id)
    return found


def heads(company_id: UUID) -> list[UUID]:
    """The top-authority director and the employees with full administrative access."""
    resolver = access.RightsResolver()
    found = []
    for e in Employment.objects.select_related("position").filter(
        company_id=company_id, state=Employment.State.ACTIVE, user__isnull=False
    ):
        actor = Actor(user_id=e.user_id or e.id, company_id=company_id, employment_id=e.id)
        top = e.position.is_top_authority
        if top or resolver.has_permission(actor, "employee.rights.manage", whole_company=True):
            found.append(e.id)
    return found


def recipients(
    company_id: UUID,
    *,
    responsible: Iterable[UUID | None] = (),
    permission: str | None = None,
    location_id: UUID | None = None,
) -> list[UUID]:
    chosen = [x for x in responsible if x is not None]
    if chosen:
        return sorted(set(chosen))
    if permission is not None:
        allowed = with_permission(company_id, permission, location_id)
        if allowed:
            return allowed
    return heads(company_id)
