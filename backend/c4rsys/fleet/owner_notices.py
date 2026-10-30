"""Mandatory notifications of the requests to the owner of a vehicle and of the answers
(ТЗ § 20.3.4, § 8.3.1): a request to a company is told to those who may answer it until it is
closed; each answer is told to the employee who sent the request."""

from datetime import datetime

from django.utils import timezone

from c4rsys.fleet import owner_requests
from c4rsys.fleet.models import VehicleOwnerRequest
from c4rsys.notifications.models import Notification
from c4rsys.platform.models import AuditEvent

REQUESTED = "vehicle.owner_requested"
ANSWERED = "vehicle.owner_answered"


def requests(now: datetime) -> int:
    from c4rsys.companies import services as companies
    from c4rsys.notifications import rules
    from c4rsys.notifications import services as notifications

    changed = 0
    awaited: set[str] = set()
    for request in VehicleOwnerRequest.objects.select_related("company").filter(
        state=VehicleOwnerRequest.State.OPEN, target_company_id__isnull=False
    ):
        target = request.target_company_id
        if target is None or owner_requests.effective_state(request, now) != "open":
            continue
        condition = f"{REQUESTED}:{request.id}"
        awaited.add(condition)
        changed += rules.raise_event(
            target,
            REQUESTED,
            condition=condition,
            object_type="owner_request",
            object_id=request.id,
            params={"company": request.company.display_name},
            recipients=[e.id for e in companies.link_answerers(target)],
            now=now,
        )
    for n in Notification.objects.filter(event=REQUESTED, state=Notification.State.ACTIVE):
        if n.condition not in awaited:
            notifications.resolve(n.company_id, n.condition, "owner_answered", now)
            changed += 1
    return changed


def on_answer(entry: AuditEvent) -> int:
    from c4rsys.companies.models import Employment
    from c4rsys.notifications import rules

    after = entry.after or {}
    request_id = after.get("request")
    if not isinstance(request_id, str):
        return 0
    request = VehicleOwnerRequest.objects.filter(pk=request_id).first()
    if request is None:
        return 0
    author = Employment.objects.filter(
        company_id=request.company_id,
        user_id=request.created_by_user_id,
        state=Employment.State.ACTIVE,
    ).first()
    if author is None:
        return 0
    return int(
        rules.raise_event(
            request.company_id,
            ANSWERED,
            condition=f"{ANSWERED}:{entry.id}",
            object_type="vehicle",
            object_id=request.vehicle_card_id,
            params={"target": request.target_label, "complete": bool(after.get("complete"))},
            recipients=[author.id],
            now=timezone.now(),
            done=True,
        )
    )
