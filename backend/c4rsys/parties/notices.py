"""Mandatory notifications of the requests to link with a profile and of the answers (ТЗ § 20.3.4,
§ 6.3.8).

* A request to a company is told to those who may answer it: the top-authority director and the
  employees with the right. It lasts until the answer, the cancel or the expiry (§ 20.3.3).
* The answer is told to the employee who sent the request.
* A person sees the request in the personal area; an email address is told by a letter when the
  request is sent.
"""

from datetime import datetime

from django.utils import timezone

from c4rsys.notifications.models import Notification
from c4rsys.parties import links
from c4rsys.parties.models import ProfileDataRequest, ProfileLink
from c4rsys.platform.models import AuditEvent

REQUESTED = "link.requested"
ANSWERED = "link.answered"
DATA_REQUESTED = "link.data_requested"
DATA_ANSWERED = "link.data_answered"


def requests(now: datetime) -> int:
    from c4rsys.companies import services as companies
    from c4rsys.notifications import rules
    from c4rsys.notifications import services as notifications

    changed = 0
    awaited: set[str] = set()
    for link in ProfileLink.objects.select_related("company").filter(
        profile=ProfileLink.Profile.COMPANY,
        state=ProfileLink.State.PENDING,
        target_company_id__isnull=False,
    ):
        if links.effective_state(link, now) != ProfileLink.State.PENDING:
            continue
        target = link.target_company_id
        if target is None:
            continue
        condition = f"{REQUESTED}:{link.id}"
        awaited.add(condition)
        changed += rules.raise_event(
            target,
            REQUESTED,
            condition=condition,
            object_type="profile_link",
            object_id=link.id,
            params={"company": link.company.display_name},
            recipients=[e.id for e in companies.link_answerers(target)],
            now=now,
        )
    for n in Notification.objects.filter(event=REQUESTED, state=Notification.State.ACTIVE):
        if n.condition not in awaited:
            notifications.resolve(n.company_id, n.condition, "link_answered", now)
            changed += 1
    return changed + data_requests(now)


def data_requests(now: datetime) -> int:
    """§ 6.4.4: a request of fresh data from a company profile is told to those who may answer,
    until the answer, the cancel or the expiry."""
    from c4rsys.companies import services as companies
    from c4rsys.notifications import rules
    from c4rsys.notifications import services as notifications
    from c4rsys.parties import data_requests as requests_of_data

    changed = 0
    awaited: set[str] = set()
    for item in ProfileDataRequest.objects.select_related("company", "link").filter(
        state=ProfileDataRequest.State.PENDING,
        link__profile=ProfileLink.Profile.COMPANY,
        link__target_company_id__isnull=False,
    ):
        target = item.link.target_company_id
        if target is None or requests_of_data.effective_state(item, now) != item.State.PENDING:
            continue
        condition = f"{DATA_REQUESTED}:{item.id}"
        awaited.add(condition)
        changed += rules.raise_event(
            target,
            DATA_REQUESTED,
            condition=condition,
            object_type="profile_data_request",
            object_id=item.id,
            params={"company": item.company.display_name},
            recipients=[e.id for e in companies.link_answerers(target)],
            now=now,
        )
    for n in Notification.objects.filter(event=DATA_REQUESTED, state=Notification.State.ACTIVE):
        if n.condition not in awaited:
            notifications.resolve(n.company_id, n.condition, "link_answered", now)
            changed += 1
    return changed


def on_data_answer(entry: AuditEvent) -> int:
    """The answer to a request of fresh data is told to the employee who sent it."""
    from c4rsys.companies.models import Employment
    from c4rsys.notifications import rules

    request_id = (entry.after or {}).get("request")
    if not request_id:
        return 0
    found = ProfileDataRequest.objects.select_related("link").filter(pk=str(request_id)).first()
    if found is None:
        return 0
    author = Employment.objects.filter(
        company_id=found.company_id,
        user_id=found.created_by_user_id,
        state=Employment.State.ACTIVE,
    ).first()
    if author is None:
        return 0
    return int(
        rules.raise_event(
            found.company_id,
            DATA_ANSWERED,
            condition=f"{DATA_ANSWERED}:{entry.id}",
            object_type=links.card_type_of(found.link),
            object_id=links.current_card_id(found.link),
            params={
                "target": found.link.target_label,
                "accepted": entry.action.endswith("accepted"),
            },
            recipients=[author.id],
            now=timezone.now(),
            done=True,
        )
    )


def on_answer(entry: AuditEvent) -> int:
    from c4rsys.companies.models import Employment
    from c4rsys.notifications import rules

    link = ProfileLink.objects.filter(pk=entry.object_id).first()
    if link is None:
        return 0
    author = Employment.objects.filter(
        company_id=link.company_id, user_id=link.created_by_user_id, state=Employment.State.ACTIVE
    ).first()
    if author is None:
        return 0
    return int(
        rules.raise_event(
            link.company_id,
            ANSWERED,
            condition=f"{ANSWERED}:{entry.id}",
            object_type=links.card_type_of(link),
            object_id=links.card_id_of(link),
            params={"target": link.target_label, "accepted": entry.action.endswith("accepted")},
            recipients=[author.id],
            now=timezone.now(),
            done=True,
        )
    )
