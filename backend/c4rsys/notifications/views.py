"""HTTP API of the notification centre (ТЗ § 20.1.5, § 20.2.2).

The centre belongs to the user: it shows the notifications of every company where the user works,
with filters by kind, state, company, location and period. Reading is the user's own mark.
"""

from datetime import date, datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import QuerySet
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.companies.models import Employment
from c4rsys.notifications.models import Notification, NotificationRecipient
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.platform.http import idempotency_key, respond
from c4rsys.platform.middleware import get_request_id

LIMIT = 200


def _mine(user_id: UUID) -> QuerySet[NotificationRecipient]:
    employments = Employment.objects.filter(user_id=user_id, state=Employment.State.ACTIVE)
    return NotificationRecipient.objects.filter(employment__in=employments).select_related(
        "notification", "notification__company", "notification__location"
    )


def _payload(r: NotificationRecipient) -> dict[str, Any]:
    n = r.notification
    return {
        "id": str(r.id),
        "company": {"public_id": n.company.public_id, "name": n.company.display_name},
        "location": {"id": str(n.location_id), "name": n.location.name} if n.location else None,
        "kind": n.kind,
        "event": n.event,
        "params": n.params,
        "object_type": n.object_type or None,
        "object_id": str(n.object_id) if n.object_id else None,
        "state": n.state,
        "done_reason": n.done_reason or None,
        "created_at": n.created_at.isoformat(),
        "last_at": n.last_at.isoformat(),
        "repeats": n.repeats,
        "read": r.read_at is not None,
    }


def _day(value: str, key: str, end: bool) -> datetime:
    try:
        on = date.fromisoformat(value)
    except ValueError as exc:
        raise errors.validation_error(key, code="DATE_INVALID") from exc
    return datetime.combine(on, time.max if end else time.min, tzinfo=ZoneInfo("UTC"))


@require_GET
def notification_list(request: HttpRequest) -> JsonResponse:
    """Filters: `kind`, `state` (active | done), `company` (public id), `location`, `from`, `to`
    (dates of the last occurrence), `unread=1`."""
    user, _session = require_user(request)
    params = request.GET
    query = _mine(user.id)
    if params.get("kind") in Notification.Kind.values:
        query = query.filter(notification__kind=params["kind"])
    if params.get("state") in Notification.State.values:
        query = query.filter(notification__state=params["state"])
    if params.get("company"):
        query = query.filter(notification__company__public_id=params["company"])
    if params.get("location"):
        try:
            query = query.filter(notification__location_id=UUID(params["location"]))
        except ValueError as exc:
            raise errors.validation_error("location") from exc
    if params.get("from"):
        query = query.filter(notification__last_at__gte=_day(params["from"], "from", False))
    if params.get("to"):
        query = query.filter(notification__last_at__lte=_day(params["to"], "to", True))
    if params.get("unread") == "1":
        query = query.filter(read_at__isnull=True)
    items = query.order_by("-notification__last_at")[:LIMIT]
    return JsonResponse({"notifications": [_payload(r) for r in items]})


@require_GET
def notification_count(request: HttpRequest) -> JsonResponse:
    """Unread active notifications, for the mark in the header."""
    user, _session = require_user(request)
    count = (
        _mine(user.id)
        .filter(read_at__isnull=True, notification__state=Notification.State.ACTIVE)
        .count()
    )
    return JsonResponse({"unread": count})


@require_GET
def notification_detail(request: HttpRequest, recipient_id: UUID) -> JsonResponse:
    """With the history of repeated reminders (§ 20.3.2)."""
    user, _session = require_user(request)
    found = _mine(user.id).filter(pk=recipient_id).first()
    if found is None:
        raise errors.not_found()
    history = [
        {"at": x.at.isoformat(), "params": x.params}
        for x in found.notification.repetitions.order_by("at")
    ]
    return JsonResponse({"notification": {**_payload(found), "history": history}})


@require_POST
def notification_read(request: HttpRequest) -> JsonResponse:
    """`{ids: [...]}` or `{all: true}`: the user's own mark; it does not end the notification
    (§ 20.3.3)."""
    import json

    user, session = require_user(request)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError as exc:
        raise errors.validation_error("body") from exc
    if not isinstance(data, dict):
        raise errors.validation_error("body")
    read_all = data.get("all") is True
    ids = data.get("ids") or []
    if not read_all and (not isinstance(ids, list) or not ids or len(ids) > LIMIT):
        raise errors.validation_error("ids", code="FIELD_REQUIRED")
    try:
        chosen = [UUID(str(x)) for x in ids]
    except ValueError as exc:
        raise errors.validation_error("ids") from exc

    def handler(ctx: CommandContext) -> CommandResult:
        from django.utils import timezone

        query = _mine(user.id).filter(read_at__isnull=True)
        if not read_all:
            query = query.filter(pk__in=chosen)
        marked = NotificationRecipient.objects.filter(
            pk__in=list(query.values_list("id", flat=True))
        ).update(read_at=timezone.now())
        ctx.audit(
            "notification.read",
            object_type="user_account",
            object_id=user.id,
            after={"count": marked, "all": read_all},
        )
        return CommandResult(200, {"marked": marked})

    return respond(
        run_command(
            command="notification.read",
            actor=Actor(user_id=user.id, session_id=session.id),
            idempotency_key=idempotency_key(request),
            payload={"ids": [str(x) for x in chosen], "all": read_all},
            handler=handler,
            request_id=get_request_id(request),
        )
    )
