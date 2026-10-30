"""The request to the owner of a managed vehicle (ТЗ § 8.3.1, § 8.3.3–8.3.6, § 8.3.8,
§ 8.3.13–8.3.14, § 7.1.2, § 20.3.4; decisions Б-4, Б-5).

* The company sends it from the vehicle card to the declared owner: a person by USR ID, email or
  phone, a company by its CMP ID. Nothing tells the company whether such a profile exists.
* Three separate answers: the link with the company, the vehicle belongs to the owner, consent
  to the management by this company. They may come one by one; until all three are given the
  request waits for the rest. An answer is never changed (§ 8.3.14).
* «The vehicle is mine» records the confirmed period of ownership of the global vehicle and puts
  it into the personal garage, whatever the other answers (§ 8.3.1). If another owner is
  confirmed already, nothing changes automatically: it is a dispute for the support (§ 8.3.12).
* The management is confirmed only by three «yes» (§ 8.3.1). A «no» on the link or the
  management is shown as a conflict; the company keeps working with the vehicle (§ 8.3.3).
* A new request is allowed after a refusal or an incomplete answer; it keeps the earlier
  answers (§ 8.3.14). No automatic reminders; a request is valid 30 days (Б-5).
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.accounts import mail
from c4rsys.accounts import services as accounts
from c4rsys.accounts.models import UserAccount
from c4rsys.companies.models import Company
from c4rsys.fleet import characteristics
from c4rsys.fleet.models import (
    Vehicle,
    VehicleCard,
    VehicleOwnerAnswer,
    VehicleOwnerClaim,
    VehicleOwnerRequest,
    VehicleOwnership,
)
from c4rsys.parties import links
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

TTL = timedelta(days=30)
State = VehicleOwnerRequest.State
Question = VehicleOwnerAnswer.Question
QUESTIONS = (Question.LINK, Question.OWNERSHIP, Question.MANAGEMENT)
OWNER_CHANGED = "owner_changed"


def effective_state(request: VehicleOwnerRequest, now: datetime) -> str:
    if request.state == State.OPEN and request.expires_at <= now:
        return State.EXPIRED
    return request.state


def answers_of(request: VehicleOwnerRequest) -> dict[str, bool]:
    return {a.question: a.answer for a in request.answers.all()}


def _claim(card: VehicleCard) -> VehicleOwnerClaim | None:
    return card.owner_claims.order_by("-created_at").first()


def latest(card: VehicleCard) -> VehicleOwnerRequest | None:
    return card.owner_requests.order_by("-created_at").first()


def management_confirmed(card: VehicleCard) -> bool:
    """Three «yes» to the request of the current declaration (§ 8.3.1)."""
    request = latest(card)
    claim = _claim(card)
    if request is None or claim is None or request.claim_id != claim.id:
        return False
    given = answers_of(request)
    return all(given.get(q) is True for q in QUESTIONS)


# --- The company -------------------------------------------------------------------------------


def _target(claim: VehicleOwnerClaim) -> links.Target:
    kind = "company" if claim.lookup.upper().startswith("CMP-") else "person"
    return links.resolve_target(kind, claim.lookup)


def send(ctx: CommandContext, company: Company, card_id: UUID) -> VehicleOwnerRequest:
    card = (
        VehicleCard.objects.select_for_update()
        .select_related("vehicle")
        .filter(company=company, pk=card_id)
        .first()
    )
    if card is None:
        raise errors.not_found()
    if card.relation != VehicleCard.Relation.MANAGED:
        raise errors.business_rule("OWNER_REQUEST_MANAGED_ONLY")
    claim = _claim(card)
    if claim is None or claim.kind != VehicleOwnerClaim.Kind.PROFILE:
        raise errors.business_rule("OWNER_PROFILE_REQUIRED")
    target = _target(claim)
    if target.company is not None and target.company.id == company.id:
        raise errors.business_rule("LINK_SELF")
    now = timezone.now()
    previous = latest(card)
    if previous is not None and previous.state == State.OPEN and previous.claim_id != claim.id:
        # Left open by a change of the owner made before such requests were closed with it.
        close_for_new_owner(ctx, card)
        previous.refresh_from_db()
    if previous is not None:
        state = effective_state(previous, now)
        given = answers_of(previous)
        if state == State.OPEN and not given:
            raise errors.business_rule("OWNER_REQUEST_PENDING")
        if (
            state == State.COMPLETE
            and previous.claim_id == claim.id
            and all(given.get(q) is True for q in QUESTIONS)
        ):
            raise errors.business_rule("OWNER_ALREADY_CONFIRMED")
        if state in (State.OPEN, State.EXPIRED) and previous.state == State.OPEN:
            # An incomplete answer: the new request keeps the earlier one with its answers.
            previous.state = State.SUPERSEDED if state == State.OPEN else State.EXPIRED
            previous.closed_at = now
            previous.save(update_fields=["state", "closed_at"])
    request = VehicleOwnerRequest.objects.create(
        company=company,
        vehicle_card=card,
        vehicle=card.vehicle,
        claim=claim,
        target_label=target.label,
        target_user_id=target.user.id
        if target.user and not target.email and not target.phone
        else None,
        target_company_id=target.company.id if target.company else None,
        target_email=target.email,
        target_phone=target.phone,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
        expires_at=now + TTL,
    )
    ctx.audit(
        "vehicle.owner_requested",
        object_type="vehicle_card",
        object_id=card.id,
        after={"request": str(request.id), "target": request.target_label},
    )
    if target.email:
        email, name = target.email, company.display_name
        transaction.on_commit(lambda: mail.send_owner_request(email, name))
    return request


def cancel(ctx: CommandContext, company_id: UUID, card_id: UUID) -> VehicleOwnerRequest:
    request = (
        VehicleOwnerRequest.objects.select_for_update()
        .filter(company_id=company_id, vehicle_card_id=card_id, state=State.OPEN)
        .first()
    )
    if request is None or effective_state(request, timezone.now()) != State.OPEN:
        raise errors.business_rule("OWNER_REQUEST_NOT_OPEN")
    request.state = State.CANCELLED
    request.closed_at = timezone.now()
    request.save(update_fields=["state", "closed_at"])
    ctx.audit(
        "vehicle.owner_request_cancelled",
        object_type="vehicle_card",
        object_id=card_id,
        after={"request": str(request.id)},
    )
    return request


def close_for_new_owner(ctx: CommandContext, card: VehicleCard) -> None:
    """Another declared owner: the open request was addressed to the previous one and is no
    longer answered (§ 8.3.13). It is cancelled with the reason and stays in the history with
    its answers; a request to the new owner may be sent. The card is locked by the caller."""
    now = timezone.now()
    for request in VehicleOwnerRequest.objects.select_for_update().filter(
        vehicle_card_id=card.id, state=State.OPEN
    ):
        expired = effective_state(request, now) == State.EXPIRED
        request.state = State.EXPIRED if expired else State.CANCELLED
        request.closed_at = now
        request.closed_reason = "" if expired else OWNER_CHANGED
        request.save(update_fields=["state", "closed_at", "closed_reason"])
        if not expired:
            ctx.audit(
                "vehicle.owner_request_cancelled",
                object_type="vehicle_card",
                object_id=card.id,
                after={"request": str(request.id), "reason": OWNER_CHANGED},
            )


def requests_of_card(company_id: UUID, card_id: UUID) -> QuerySet[VehicleOwnerRequest]:
    return (
        VehicleOwnerRequest.objects.filter(company_id=company_id, vehicle_card_id=card_id)
        .prefetch_related("answers")
        .order_by("-created_at")
    )


# --- The owner ---------------------------------------------------------------------------------


def for_user(user: UserAccount) -> QuerySet[VehicleOwnerRequest]:
    return VehicleOwnerRequest.objects.filter(
        Q(target_user_id=user.id)
        | Q(target_email__in=accounts.confirmed_emails(user))
        | Q(target_phone__in=accounts.confirmed_phones(user))
    ).filter(target_company_id__isnull=True)


def for_company(company_id: UUID) -> QuerySet[VehicleOwnerRequest]:
    return VehicleOwnerRequest.objects.filter(target_company_id=company_id)


def _parse(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict) or not value:
        raise errors.validation_error("answers")
    parsed = {}
    for question, answer in value.items():
        if question not in QUESTIONS or not isinstance(answer, bool):
            raise errors.validation_error("answers", code="VALUE_UNKNOWN")
        parsed[str(question)] = answer
    return parsed


def answer_as_user(
    ctx: CommandContext, user: UserAccount, request_id: UUID, given: Any
) -> VehicleOwnerRequest:
    return _answer(ctx, for_user(user), request_id, _parse(given), user=user, company=None)


def answer_as_company(
    ctx: CommandContext, company: Company, request_id: UUID, given: Any
) -> VehicleOwnerRequest:
    return _answer(
        ctx, for_company(company.id), request_id, _parse(given), user=None, company=company
    )


def _answer(
    ctx: CommandContext,
    query: QuerySet[VehicleOwnerRequest],
    request_id: UUID,
    given: dict[str, bool],
    *,
    user: UserAccount | None,
    company: Company | None,
) -> VehicleOwnerRequest:
    user_id = user.id if user is not None else None
    peek = query.filter(pk=request_id).first()
    if peek is None:
        raise errors.not_found()
    # The vehicle card first, then the request: the same order as a change of the declared
    # owner, which closes the open request (close_for_new_owner).
    card = VehicleCard.objects.select_for_update().get(pk=peek.vehicle_card_id)
    request = query.select_for_update().get(pk=request_id)
    now = timezone.now()
    state = effective_state(request, now)
    if state == State.EXPIRED:
        raise errors.business_rule("OWNER_REQUEST_EXPIRED")
    if state != State.OPEN:
        raise errors.business_rule("OWNER_REQUEST_NOT_OPEN")
    # Checked again on fresh data: the request is to the current declaration of a managed card.
    claim = _claim(card)
    if (
        claim is None
        or claim.id != request.claim_id
        or card.state != VehicleCard.State.ACTIVE
        or card.relation != VehicleCard.Relation.MANAGED
    ):
        raise errors.business_rule("OWNER_REQUEST_NOT_OPEN")
    before = answers_of(request)
    repeated = sorted(set(given) & set(before))
    if repeated:
        raise errors.business_rule("OWNER_ANSWER_GIVEN", questions=repeated)
    responder = ctx.actor.user_id
    for question, value in given.items():
        VehicleOwnerAnswer.objects.create(
            request=request,
            question=question,
            answer=value,
            user_id=responder,
            company_id=company.id if company else None,
            answered_at=now,
        )
    if user_id is not None:
        request.target_user_id = user_id
    ownership = ""
    if given.get(Question.OWNERSHIP) is True:
        ownership = _own(request, user_id, company.id if company else None, now)
    complete = len(before) + len(given) == len(QUESTIONS)
    if complete:
        request.state = State.COMPLETE
        request.closed_at = now
    request.save(update_fields=["target_user_id", "state", "closed_at"])
    after = {"request": str(request.id), "answers": given, "complete": complete}
    if ownership:
        after["ownership"] = ownership
    if given.get(Question.LINK) is True:
        # «Yes» to the link makes the owner a client of the company: a client card linked to
        # the profile (customer's decision of 30.10.2026).
        client = links.client_from_owner(
            ctx,
            request.company,
            owner_user=user,
            owner_company=company,
            requested_by=request.created_by_user_id,
        )
        after["client"] = str(client.id)
    # Journaled in the company that asked: its vehicle card is concerned (§ 20.3.4).
    ctx.audit(
        "vehicle.owner_answered",
        object_type="vehicle_card",
        object_id=request.vehicle_card_id,
        after=after,
        company_id=request.company_id,
    )
    if company is not None:
        ctx.audit(
            "vehicle.owner_answer_given",
            object_type="vehicle_owner_request",
            object_id=request.id,
            after={"answers": given, "requester": str(request.company_id)},
            company_id=company.id,
        )
    return request


def _own(
    request: VehicleOwnerRequest, user_id: UUID | None, company_id: UUID | None, now: datetime
) -> str:
    """Records the confirmed ownership of the global vehicle (§ 8.3.13, § 8.3.16). Another
    confirmed owner is not replaced automatically: it is a dispute (§ 8.3.12). The global vehicle
    is locked so that two first owners answering at once are serialized: the later one finds the
    earlier and becomes a dispute."""
    Vehicle.objects.select_for_update().filter(pk=request.vehicle_id).first()
    current = (
        VehicleOwnership.objects.select_for_update()
        .filter(vehicle_id=request.vehicle_id, ended_at__isnull=True)
        .first()
    )
    if current is not None:
        same = (
            current.owner_user_id == user_id
            if user_id is not None
            else current.owner_company_id == company_id
        )
        return "already" if same else "dispute"
    VehicleOwnership.objects.create(
        vehicle_id=request.vehicle_id,
        owner_user_id=user_id,
        owner_company_id=company_id,
        started_at=now,
        request=request,
    )
    return "recorded"


def garage_of_user(user_id: UUID) -> QuerySet[VehicleOwnership]:
    """Vehicles in the personal garage: the current confirmed ownership (§ 7.1.2)."""
    return VehicleOwnership.objects.filter(owner_user_id=user_id, ended_at__isnull=True)


def garage_payload(user_id: UUID) -> list[dict[str, Any]]:
    """The minimal personal garage: the name, make, model and plate (§ 7.1.2). The name is the
    make and model, or the VHL ID without them (PROPOSAL)."""
    result = []
    for ownership in garage_of_user(user_id).select_related("vehicle").order_by("-started_at"):
        facts = characteristics.describe(ownership.vehicle)
        make, model = facts["make_name"] or "", facts["model_name"] or ""
        result.append(
            {
                "vehicle_id": ownership.vehicle.public_id,
                "name": " ".join(p for p in (make, model) if p) or ownership.vehicle.public_id,
                "make": make,
                "model": model,
                "plate": facts["plate"],
                "since": ownership.started_at.isoformat(),
            }
        )
    return result


def has_confirmed_ownership(card_ids: list[UUID]) -> set[UUID]:
    """Cards whose requests recorded a confirmed ownership; they stop the cancel of an import."""
    return set(
        VehicleOwnership.objects.filter(request__vehicle_card_id__in=card_ids).values_list(
            "request__vehicle_card_id", flat=True
        )
    )


# --- Payloads ----------------------------------------------------------------------------------


def _names(request: VehicleOwnerRequest) -> dict[UUID, str]:
    return accounts.names({a.user_id for a in request.answers.all()})


def request_payload(request: VehicleOwnerRequest, now: datetime) -> dict[str, Any]:
    """For the company: the answers with who answered and when (§ 8.3.14)."""
    state = effective_state(request, now)
    given = list(request.answers.all())
    names = _names(request)
    mine = next((a for a in given if a.question == Question.OWNERSHIP and a.answer), None)
    dispute = mine is not None and _disputed(request, mine.answered_at)
    return {
        "id": str(request.id),
        "state": "awaiting_rest" if state == State.OPEN and given else state,
        "target": request.target_label,
        "by_phone": bool(request.target_phone),
        "created_at": request.created_at.isoformat(),
        "expires_at": request.expires_at.isoformat(),
        "answers": [
            {
                "question": a.question,
                "answer": a.answer,
                "at": a.answered_at.isoformat(),
                "by": names.get(a.user_id, ""),
            }
            for a in sorted(given, key=lambda a: QUESTIONS.index(Question(a.question)))
        ],
        # «The vehicle is mine» while another owner is confirmed: for the support (§ 8.3.12).
        "ownership_dispute": dispute,
        "closed_reason": request.closed_reason,
    }


def _disputed(request: VehicleOwnerRequest, answered_at: datetime) -> bool:
    """«The vehicle is mine» recorded nothing because another owner was confirmed at that moment.
    The same owner confirmed by an earlier request is no dispute."""
    if VehicleOwnership.objects.filter(request=request).exists():
        return False
    owner = (
        Q(owner_company_id=request.target_company_id)
        if request.target_company_id is not None
        else Q(owner_user_id=request.target_user_id)
    )
    return not VehicleOwnership.objects.filter(
        owner,
        Q(ended_at__isnull=True) | Q(ended_at__gt=answered_at),
        vehicle_id=request.vehicle_id,
        started_at__lte=answered_at,
    ).exists()


def owner_status(card: VehicleCard) -> dict[str, Any]:
    """The state of the owner's confirmation shown with the declared owner."""
    request = latest(card)
    claim = _claim(card)
    current = request is not None and claim is not None and request.claim_id == claim.id
    given = answers_of(request) if current and request is not None else {}
    return {
        "confirmed": current and all(given.get(q) is True for q in QUESTIONS),
        "ownership_confirmed": given.get(Question.OWNERSHIP) is True,
        # § 8.3.3: a refusal of the declared link or management is a conflict.
        "conflict": given.get(Question.LINK) is False or given.get(Question.MANAGEMENT) is False,
    }


def incoming_payload(request: VehicleOwnerRequest, now: datetime) -> dict[str, Any]:
    """For the owner: who asks, which vehicle, the answers given so far."""
    facts = characteristics.describe(request.vehicle)
    company = request.company
    return {
        "id": str(request.id),
        "state": effective_state(request, now),
        "company": {"public_id": company.public_id, "display_name": company.display_name},
        "vehicle": {
            "vehicle_id": request.vehicle.public_id,
            "make": facts["make_name"],
            "model": facts["model_name"],
            "year": facts["year"],
            "plate": facts["plate"],
            "vin": facts["vin"],
        },
        "created_at": request.created_at.isoformat(),
        "expires_at": request.expires_at.isoformat(),
        "answers": answers_of(request),
    }
