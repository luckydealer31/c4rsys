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
from c4rsys.parties.models import ProfileLink
from c4rsys.platform.models import AuditEvent

REQUESTED = "link.requested"
ANSWERED = "link.answered"


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
    return changed


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
