"""Requests of fresh data from a linked profile (ТЗ § 6.4.4, § 6.4.1–6.4.3, § 6.3.8).

* Later changes of the profile do not reach the company by themselves (§ 6.4.3). The company may
  ask for the current data only when the client asks for a service: the employee states what the
  client asked for; nothing is sent automatically or periodically.
* The request goes through a confirmed link. The person answers in the personal cabinet; a
  company — its top-authority director or an employee with the right (§ 6.3.8). The recipient
  chooses what to give; the request does not open every field.
* The answer is kept as a new snapshot next to the link; the working fields of the card change
  only when an employee takes a value (§ 6.4.2, links.apply_value).

PROPOSAL: one request waits at a time per link; it is valid 30 days like a request to link; the
person is not sent a letter (the cabinet shows the request).
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.accounts import services as accounts
from c4rsys.accounts.models import UserAccount
from c4rsys.companies import services as companies
from c4rsys.companies.models import Company
from c4rsys.parties import links
from c4rsys.parties.models import ClientCard, ProfileDataRequest, ProfileLink
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

TTL = timedelta(days=30)
State = ProfileDataRequest.State


def effective_state(item: ProfileDataRequest, now: datetime) -> str:
    if item.state == State.PENDING and item.expires_at <= now:
        return State.EXPIRED
    return item.state


def of_link(company_id: UUID, link_id: UUID) -> QuerySet[ProfileDataRequest]:
    return ProfileDataRequest.objects.filter(company_id=company_id, link_id=link_id).order_by(
        "-created_at"
    )


def request(
    ctx: CommandContext, company_id: UUID, link_id: UUID, occasion: str
) -> ProfileDataRequest:
    occasion = occasion.strip()
    if not occasion:
        # § 6.4.4: only when the client asks for a service — what they asked for is stated.
        raise errors.validation_error("occasion", code="FIELD_REQUIRED")
    peek = ProfileLink.objects.filter(company_id=company_id, pk=link_id).first()
    if peek is None:
        raise errors.not_found()
    card = links._lock_link_card(peek)
    link = ProfileLink.objects.select_for_update().get(pk=link_id)
    if link.state != ProfileLink.State.ACCEPTED or link.access_revoked_at is not None:
        raise errors.business_rule("LINK_NOT_ACCEPTED")
    if card.state != ClientCard.State.ACTIVE:
        raise errors.business_rule("CARD_ARCHIVED")
    now = timezone.now()
    pending = (
        ProfileDataRequest.objects.select_for_update()
        .filter(link=link, state=State.PENDING)
        .first()
    )
    if pending is not None:
        if effective_state(pending, now) != State.EXPIRED:
            raise errors.business_rule("DATA_REQUEST_PENDING")
        pending.state = State.EXPIRED
        pending.save(update_fields=["state"])
    item = ProfileDataRequest.objects.create(
        company_id=company_id,
        link=link,
        occasion=occasion,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
        expires_at=now + TTL,
    )
    ctx.audit(
        "profile_data_request.sent",
        object_type="profile_link",
        object_id=link.id,
        after={"request": str(item.id), "occasion": occasion, "expires_at": item.expires_at},
    )
    return item


def cancel(ctx: CommandContext, company_id: UUID, request_id: UUID) -> ProfileDataRequest:
    item = (
        ProfileDataRequest.objects.select_for_update()
        .filter(company_id=company_id, pk=request_id)
        .first()
    )
    if item is None:
        raise errors.not_found()
    if effective_state(item, timezone.now()) != State.PENDING:
        raise errors.business_rule("DATA_REQUEST_NOT_PENDING")
    item.state = State.CANCELLED
    item.decided_at = timezone.now()
    item.save(update_fields=["state", "decided_at"])
    ctx.audit(
        "profile_data_request.cancelled",
        object_type="profile_link",
        object_id=item.link_id,
        after={"request": str(item.id)},
    )
    return item


# --- The recipient (§ 6.3.8) -------------------------------------------------------------------


def for_user(user: UserAccount) -> QuerySet[ProfileDataRequest]:
    return ProfileDataRequest.objects.select_related("link", "company").filter(
        link__profile=ProfileLink.Profile.PERSON, link__target_user_id=user.id
    )


def for_company(company_id: UUID) -> QuerySet[ProfileDataRequest]:
    return ProfileDataRequest.objects.select_related("link", "company").filter(
        link__profile=ProfileLink.Profile.COMPANY, link__target_company_id=company_id
    )


def _locked(query: QuerySet[ProfileDataRequest], request_id: UUID) -> ProfileDataRequest:
    item = query.select_for_update(of=("self",)).filter(pk=request_id).first()
    if item is None:
        raise errors.not_found()
    state = effective_state(item, timezone.now())
    if state == State.EXPIRED:
        raise errors.business_rule("DATA_REQUEST_EXPIRED")
    if state != State.PENDING:
        raise errors.business_rule("DATA_REQUEST_NOT_PENDING")
    if item.link.state != ProfileLink.State.ACCEPTED:
        # The link was broken meanwhile: nothing is given through it (§ 6.6.3).
        raise errors.business_rule("LINK_NOT_ACCEPTED")
    return item


def _answer(
    ctx: CommandContext, item: ProfileDataRequest, data: dict[str, Any] | None
) -> ProfileDataRequest:
    now = timezone.now()
    item.state = State.ACCEPTED if data is not None else State.DECLINED
    item.decided_at = now
    item.decided_by_user_id = ctx.actor.user_id
    if data is not None:
        item.shared = {"data": data, "at": now.isoformat()}
    item.save(update_fields=["state", "decided_at", "decided_by_user_id", "shared"])
    ctx.audit(
        "profile_data_request.accepted" if data is not None else "profile_data_request.declined",
        object_type="profile_link",
        object_id=item.link_id,
        after={"request": str(item.id), "fields": sorted(data or {})},
        company_id=item.company_id,
    )
    return item


def accept_as_user(
    ctx: CommandContext, user: UserAccount, request_id: UUID, fields: list[str]
) -> ProfileDataRequest:
    item = _locked(for_user(user), request_id)
    data = links._chosen(accounts.shareable_profile(user), fields, links.PERSON_FIELDS)
    return _answer(ctx, item, data)


def decline_as_user(ctx: CommandContext, user: UserAccount, request_id: UUID) -> ProfileDataRequest:
    return _answer(ctx, _locked(for_user(user), request_id), None)


def accept_as_company(
    ctx: CommandContext, company: Company, request_id: UUID, fields: list[str]
) -> ProfileDataRequest:
    item = _locked(for_company(company.id), request_id)
    data = links._chosen(companies.shareable_profile(company), fields, links.COMPANY_FIELDS)
    _answered(ctx, item, company.id, accepted=True)
    return _answer(ctx, item, data)


def decline_as_company(
    ctx: CommandContext, company: Company, request_id: UUID
) -> ProfileDataRequest:
    item = _locked(for_company(company.id), request_id)
    _answered(ctx, item, company.id, accepted=False)
    return _answer(ctx, item, None)


def _answered(
    ctx: CommandContext, item: ProfileDataRequest, company_id: UUID, *, accepted: bool
) -> None:
    ctx.audit(
        "profile_data_request.answered",
        object_type="profile_data_request",
        object_id=item.id,
        after={"accepted": accepted, "requester": str(item.company_id)},
        company_id=company_id,
    )


# --- Payloads ------------------------------------------------------------------------------------


def payload(item: ProfileDataRequest, now: datetime) -> dict[str, Any]:
    """For the requesting company."""
    return {
        "id": str(item.id),
        "link_id": str(item.link_id),
        "state": effective_state(item, now),
        "occasion": item.occasion,
        "created_at": item.created_at.isoformat(),
        "expires_at": item.expires_at.isoformat(),
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "shared": (item.shared or {}).get("data", {}),
        "shared_at": (item.shared or {}).get("at"),
    }


def incoming_payload(
    item: ProfileDataRequest, now: datetime, available: dict[str, Any]
) -> dict[str, Any]:
    """For the recipient: who asks and why, and the recipient's own data that may be given."""
    return {
        "id": str(item.id),
        "state": effective_state(item, now),
        "company": {
            "public_id": item.company.public_id,
            "display_name": item.company.display_name,
        },
        "card_type": links.card_type_of(item.link),
        "occasion": item.occasion,
        "created_at": item.created_at.isoformat(),
        "expires_at": item.expires_at.isoformat(),
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "available": available,
        "shared": (item.shared or {}).get("data", {}),
    }
