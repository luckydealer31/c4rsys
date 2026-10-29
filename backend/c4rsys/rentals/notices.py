"""Notifications and system tasks of rentals (ТЗ § 20.3.5, § 19.1.9, § 14.3.7; decisions З-7,
З-9, З-11 of 29.10.2026).

* Upcoming issue and return: a reminder the company's moment before the planned time; the issue
  says when the vehicle is not in the location of the issue (§ 20.3.5).
* Overdue return: a warning repeated by the company's interval until the return.
* A cancelled booking: information.
* An exception recorded after the transfer: «action required» and a system task to confirm or
  reject it; the decision ends both, keeping the result (§ 14.3.7, § 19.2.1).
* A returned rental without the saved readings of an inspection: «action required» and a system
  task; a saved inspection ends both (decision З-11).
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import transaction

from c4rsys.notifications import rules
from c4rsys.notifications import services as notifications
from c4rsys.notifications.models import Notification
from c4rsys.rentals.models import IssueException, Rental


def _condition(event: str, rental_id: UUID) -> str:
    return f"{event}:{rental_id}"


def _params(r: Rental) -> dict[str, Any]:
    return {
        "booking_number": r.booking_number,
        "vehicle": r.vehicle_card.name if r.vehicle_card_id else None,
        "planned_start": r.planned_start.isoformat() if r.planned_start else None,
        "planned_end": r.planned_end.isoformat() if r.planned_end else None,
    }


def _close_stale(event: str, keep: set[str], reason: str, now: datetime) -> int:
    closed = 0
    for n in Notification.objects.filter(event=event, state=Notification.State.ACTIVE):
        if n.condition not in keep:
            notifications.resolve(n.company_id, n.condition, reason, now)
            closed += 1
    return closed


def _upcoming(now: datetime) -> int:
    changed = 0
    leads: dict[tuple[UUID, str], int] = {}

    def lead(company_id: UUID, event: str) -> int:
        key = (company_id, event)
        if key not in leads:
            leads[key] = rules.rule(company_id, event).lead_minutes
        return leads[key]

    issues: set[str] = set()
    for r in Rental.objects.select_related("vehicle_card").filter(
        state=Rental.State.BOOKED,
        planned_start__gt=now,
        planned_start__lte=now + timedelta(days=90),
    ):
        if r.planned_start is None:
            continue
        if r.planned_start - timedelta(minutes=lead(r.company_id, "rental.issue_upcoming")) > now:
            continue
        condition = _condition("rental.issue_upcoming", r.id)
        issues.add(condition)
        mismatch = r.vehicle_card.accounting_location_id != r.issue_location_id
        changed += rules.raise_event(
            r.company_id,
            "rental.issue_upcoming",
            condition=condition,
            location_id=r.issue_location_id,
            object_type="rental",
            object_id=r.id,
            params={**_params(r), "location_mismatch": mismatch},
            now=now,
        )
    changed += _close_stale("rental.issue_upcoming", issues, "rental_issue_passed", now)

    returns: set[str] = set()
    overdue: set[str] = set()
    for r in Rental.objects.select_related("vehicle_card").filter(
        state=Rental.State.ACTIVE,
        planned_end__isnull=False,
        planned_end__lte=now + timedelta(days=90),
    ):
        if r.planned_end is None:
            continue
        place = r.planned_return_location_id or r.issue_location_id
        if r.planned_end < now:
            condition = _condition("rental.return_overdue", r.id)
            overdue.add(condition)
            changed += rules.raise_event(
                r.company_id,
                "rental.return_overdue",
                condition=condition,
                location_id=place,
                object_type="rental",
                object_id=r.id,
                params=_params(r),
                now=now,
            )
            continue
        if r.planned_end - timedelta(minutes=lead(r.company_id, "rental.return_upcoming")) > now:
            continue
        condition = _condition("rental.return_upcoming", r.id)
        returns.add(condition)
        changed += rules.raise_event(
            r.company_id,
            "rental.return_upcoming",
            condition=condition,
            location_id=place,
            object_type="rental",
            object_id=r.id,
            params=_params(r),
            now=now,
        )
    changed += _close_stale("rental.return_upcoming", returns, "rental_return_passed", now)
    changed += _close_stale("rental.return_overdue", overdue, "rental_returned", now)
    return changed


def _exceptions(now: datetime) -> int:
    from c4rsys.tasks import services as tasks

    changed = 0
    pending: set[str] = set()
    for item in IssueException.objects.select_related("rental", "rental__vehicle_card").filter(
        state=IssueException.State.PENDING
    ):
        r = item.rental
        condition = _condition("rental.exception_pending", r.id)
        pending.add(condition)
        changed += rules.raise_event(
            r.company_id,
            "rental.exception_pending",
            condition=condition,
            location_id=r.issue_location_id,
            object_type="rental",
            object_id=r.id,
            params={**_params(r), "requirements": item.requirements},
            now=now,
        )
        if tasks.ensure_system(
            r.company_id,
            event="rental.exception_pending",
            condition=condition,
            location_id=r.issue_location_id,
            object_type="rental",
            object_id=r.id,
            params=_params(r),
        ):
            changed += 1
    for n in Notification.objects.filter(
        event="rental.exception_pending", state=Notification.State.ACTIVE
    ):
        if n.condition in pending:
            continue
        found = IssueException.objects.filter(rental_id=str(n.object_id)).first()
        result = found.state if found is not None else "removed"
        notifications.resolve(n.company_id, n.condition, f"exception_{result}", now)
        decided = result in (IssueException.State.CONFIRMED, IssueException.State.REJECTED)
        changed += tasks.close_system(n.company_id, n.condition, done=decided, result=result)
    return changed


def _inspections(now: datetime) -> int:
    """Decision З-11: a returned rental without the saved readings of its inspections."""
    from c4rsys.rentals import formalization
    from c4rsys.tasks import services as tasks

    changed = 0
    missing: set[str] = set()
    for r in Rental.objects.select_related("vehicle_card").filter(
        state=Rental.State.RETURNED, formalization_completed_at__isnull=True
    ):
        lacks = [
            x for x in formalization.missing_conditions(r) if x.endswith("_inspection_not_saved")
        ]
        if not lacks:
            continue
        condition = _condition("rental.inspection_missing", r.id)
        missing.add(condition)
        place = r.planned_return_location_id or r.issue_location_id
        changed += rules.raise_event(
            r.company_id,
            "rental.inspection_missing",
            condition=condition,
            location_id=place,
            object_type="rental",
            object_id=r.id,
            params={**_params(r), "missing": lacks},
            now=now,
        )
        if tasks.ensure_system(
            r.company_id,
            event="rental.inspection_missing",
            condition=condition,
            location_id=place,
            object_type="rental",
            object_id=r.id,
            params=_params(r),
        ):
            changed += 1
    for n in Notification.objects.filter(
        event="rental.inspection_missing", state=Notification.State.ACTIVE
    ):
        if n.condition in missing:
            continue
        rental = Rental.objects.filter(pk=str(n.object_id)).first()
        saved = rental is not None and rental.state in (Rental.State.RETURNED, Rental.State.ACTIVE)
        notifications.resolve(
            n.company_id, n.condition, "inspection_saved" if saved else "rental_gone", now
        )
        changed += tasks.close_system(
            n.company_id,
            n.condition,
            done=saved,
            result="inspection_saved" if saved else "rental_gone",
        )
    return changed


def run(now: datetime) -> int:
    with transaction.atomic():
        return _upcoming(now) + _exceptions(now) + _inspections(now) + _cancelled(now)


def _cancelled(now: datetime) -> int:
    """Bookings cancelled in the last day, by an employee or automatically at no-show; each is
    told once and stays in the history (an instant event)."""
    changed = 0
    for r in Rental.objects.select_related("vehicle_card").filter(
        state=Rental.State.CANCELLED,
        cancelled_at__gte=now - timedelta(days=1),
        booking_number__isnull=False,
    ):
        condition = _condition("rental.booking_cancelled", r.id)
        if Notification.objects.filter(company_id=r.company_id, condition=condition).exists():
            continue
        notifications.resolve(
            r.company_id, _condition("rental.issue_upcoming", r.id), "booking_cancelled", now
        )
        changed += rules.raise_event(
            r.company_id,
            "rental.booking_cancelled",
            condition=condition,
            location_id=r.issue_location_id,
            object_type="rental",
            object_id=r.id,
            params={**_params(r), "reason": r.cancel_reason},
            now=now,
            done=True,
        )
    return changed
