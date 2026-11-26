"""Fresh data of a linked profile (ТЗ § 6.4.4, § 6.4.1–6.4.3, § 6.3.8).

* Later changes of the profile do not reach the company by themselves (§ 6.4.3), and the company
  does not ask for them (customer's decision of 26.11.2026): the holder of the profile gives the
  fresh data on his own — a person in the personal cabinet, a company by its top-authority
  director or an employee with the right (§ 6.3.8). He chooses what to give.
* The data go through a confirmed link and are kept as a new snapshot next to it; the working
  fields of the card change only when an employee takes a value (§ 6.4.2, links.apply_value).

The table keeps the requests the companies sent before the decision; those that waited for an
answer were cancelled (migration 0017).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.accounts import services as accounts
from c4rsys.accounts.models import UserAccount
from c4rsys.companies import services as companies
from c4rsys.companies.models import Company
from c4rsys.parties import links
from c4rsys.parties.models import ProfileDataRequest, ProfileLink
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

State = ProfileDataRequest.State
GIVEN = "profile_data.given"


def of_link(company_id: UUID, link_id: UUID) -> QuerySet[ProfileDataRequest]:
    """The data given through the link after it was confirmed, the latest first."""
    return ProfileDataRequest.objects.filter(
        company_id=company_id, link_id=link_id, state=State.ACCEPTED
    ).order_by("-created_at")


def _give(
    ctx: CommandContext, query: QuerySet[ProfileLink], link_id: UUID, data: dict[str, Any]
) -> ProfileDataRequest:
    if not data:
        raise errors.validation_error("fields", code="FIELD_REQUIRED")
    peek = query.filter(pk=link_id).first()
    if peek is None:
        raise errors.not_found()
    # The card first, then its link: the same order as the other commands of a link.
    links._lock_link_card(peek)
    link = ProfileLink.objects.select_for_update().get(pk=link_id)
    if link.state != ProfileLink.State.ACCEPTED or link.access_revoked_at is not None:
        # Nothing is given through a broken link (§ 6.6.3).
        raise errors.business_rule("LINK_NOT_ACCEPTED")
    now = timezone.now()
    item = ProfileDataRequest.objects.create(
        company_id=link.company_id,
        link=link,
        state=State.ACCEPTED,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
        expires_at=now,
        decided_at=now,
        decided_by_user_id=ctx.actor.user_id,
        shared={"data": data, "at": now.isoformat()},
    )
    ctx.audit(
        GIVEN,
        object_type="profile_link",
        object_id=link.id,
        after={"request": str(item.id), "fields": sorted(data)},
        company_id=link.company_id,
    )
    return item


def give_as_user(
    ctx: CommandContext, user: UserAccount, link_id: UUID, fields: list[str]
) -> ProfileDataRequest:
    query = ProfileLink.objects.filter(profile=ProfileLink.Profile.PERSON, target_user_id=user.id)
    data = links._chosen(accounts.shareable_profile(user), fields, links.PERSON_FIELDS)
    return _give(ctx, query, link_id, data)


def give_as_company(
    ctx: CommandContext, company: Company, link_id: UUID, fields: list[str]
) -> ProfileDataRequest:
    data = links._chosen(companies.shareable_profile(company), fields, links.COMPANY_FIELDS)
    item = _give(ctx, links.for_company(company.id), link_id, data)
    # The journal of the company that gave its data.
    ctx.audit(
        "profile_data.sent",
        object_type="profile_data_request",
        object_id=item.id,
        after={"fields": sorted(data), "receiver": str(item.company_id)},
        company_id=company.id,
    )
    return item


# --- Payloads ------------------------------------------------------------------------------------


def payload(item: ProfileDataRequest, now: datetime) -> dict[str, Any]:
    """For the company that received the data."""
    return {
        "id": str(item.id),
        "link_id": str(item.link_id),
        "state": item.state,
        "occasion": item.occasion,
        "created_at": item.created_at.isoformat(),
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "shared": (item.shared or {}).get("data", {}),
        "shared_at": (item.shared or {}).get("at"),
        "compare": _compare(item),
    }


def _compare(item: ProfileDataRequest) -> dict[str, dict[str, Any]]:
    """What the card holds now for each given value (customer's remark of 25.11.2026)."""
    data = (item.shared or {}).get("data", {})
    if not data:
        return {}
    card_type = links.card_type_of(item.link)
    card = links.card_model(card_type).objects.filter(pk=links.current_card_id(item.link)).first()
    return links.compare(card, card_type, data) if card is not None else {}


def given_payload(item: ProfileDataRequest) -> dict[str, Any]:
    """For the one who gave the data."""
    return {
        "id": str(item.id),
        "link_id": str(item.link_id),
        "given_at": item.created_at.isoformat(),
        "shared": (item.shared or {}).get("data", {}),
    }
