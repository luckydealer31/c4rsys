"""Disputes of vehicle ownership for the system verification (ТЗ § 8.3.12, § 8.3.16, § 24.1.7).

A dispute is «the vehicle is mine» answered to a request of a company while another owner was
confirmed: nothing changed automatically (c4rsys.fleet.owner_requests). The verification decides:
the current owner stays, or his period ends at a chosen moment and the claimant's period starts
from it. The earlier period is kept as history; the decision keeps its reason, the staff member
and the ticket. Business records of companies are not touched (§ 24.1.4).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import QuerySet

from c4rsys.accounts import services as accounts
from c4rsys.companies.models import Company
from c4rsys.fleet.models import (
    Vehicle,
    VehicleOwnerAnswer,
    VehicleOwnerRequest,
    VehicleOwnership,
    VehicleOwnershipDecision,
)
from c4rsys.fleet.owner_requests import _disputed
from c4rsys.platform import errors

Question = VehicleOwnerAnswer.Question
Kind = VehicleOwnershipDecision.Kind


def _claims() -> QuerySet[VehicleOwnerAnswer]:
    return VehicleOwnerAnswer.objects.select_related("request").filter(
        question=Question.OWNERSHIP, answer=True
    )


def open_disputes() -> list[VehicleOwnerRequest]:
    """Requests with a dispute of ownership and no decision yet, oldest first."""
    decided = set(VehicleOwnershipDecision.objects.values_list("request_id", flat=True))
    found = []
    for answer in _claims().exclude(request_id__in=decided).order_by("answered_at")[:500]:
        if _disputed(answer.request, answer.answered_at):
            found.append(answer.request)
    return found


def decided_disputes(limit: int = 50) -> list[VehicleOwnershipDecision]:
    return list(
        VehicleOwnershipDecision.objects.select_related("request", "vehicle").order_by(
            "-decided_at"
        )[:limit]
    )


def _party(user_id: UUID | None, company_id: UUID | None) -> dict[str, Any] | None:
    if user_id is not None:
        user = accounts.get_user(user_id)
        return {
            "type": "user",
            "public_id": user.public_id if user else "",
            "name": accounts.names({user_id}).get(user_id, ""),
        }
    if company_id is not None:
        company = Company.objects.filter(pk=company_id).first()
        return {
            "type": "company",
            "public_id": company.public_id if company else "",
            "name": company.display_name if company else "",
        }
    return None


def _current(vehicle_id: UUID) -> VehicleOwnership | None:
    return VehicleOwnership.objects.filter(vehicle_id=vehicle_id, ended_at__isnull=True).first()


def dispute_payload(request: VehicleOwnerRequest) -> dict[str, Any]:
    claim = _claims().filter(request=request).first()
    current = _current(request.vehicle_id)
    decision = VehicleOwnershipDecision.objects.filter(request=request).first()
    return {
        "request_id": str(request.id),
        "vehicle": request.vehicle.public_id,
        "company": {
            "public_id": request.company.public_id,
            "name": request.company.display_name,
        },
        "claimant": _party(request.target_user_id, request.target_company_id),
        "claimed_at": claim.answered_at.isoformat() if claim else None,
        "current_owner": _party(current.owner_user_id, current.owner_company_id)
        if current
        else None,
        "current_since": current.started_at.isoformat() if current else None,
        "decision": {
            "kind": decision.kind,
            "reason": decision.reason,
            "effective_at": decision.effective_at.isoformat() if decision.effective_at else None,
            "decided_at": decision.decided_at.isoformat(),
        }
        if decision
        else None,
    }


def decide(
    request_id: UUID,
    *,
    kind: str,
    reason: str,
    effective_at: datetime | None,
    by_user_id: UUID,
    ticket_id: UUID | None,
    now: datetime,
) -> tuple[VehicleOwnershipDecision, dict[str, Any]]:
    """Run inside the staff command. Returns the decision and the change for the journal."""
    if kind not in Kind.values:
        raise errors.validation_error("kind")
    request = (
        VehicleOwnerRequest.objects.select_for_update()
        .select_related("vehicle", "company")
        .filter(pk=request_id)
        .first()
    )
    if request is None:
        raise errors.not_found()
    Vehicle.objects.select_for_update().filter(pk=request.vehicle_id).first()
    claim = _claims().filter(request=request).first()
    if claim is None or not _disputed(request, claim.answered_at):
        raise errors.business_rule("DISPUTE_NOT_OPEN")
    if VehicleOwnershipDecision.objects.filter(request=request).exists():
        raise errors.business_rule("DISPUTE_DECIDED")
    current = (
        VehicleOwnership.objects.select_for_update()
        .filter(vehicle_id=request.vehicle_id, ended_at__isnull=True)
        .first()
    )
    before = {
        "owner": _party(current.owner_user_id, current.owner_company_id) if current else None,
        "since": current.started_at if current else None,
    }
    new: VehicleOwnership | None = None
    moment = None
    if kind == Kind.TRANSFER:
        moment = effective_at or now
        if moment > now or (current is not None and moment < current.started_at):
            raise errors.validation_error("effective_at", code="DISPUTE_MOMENT_INVALID")
        if current is not None:
            current.ended_at = moment
            current.save(update_fields=["ended_at"])
        new = VehicleOwnership.objects.create(
            vehicle_id=request.vehicle_id,
            owner_user_id=request.target_user_id if request.target_company_id is None else None,
            owner_company_id=request.target_company_id,
            started_at=moment,
            request=request,
        )
    decision = VehicleOwnershipDecision.objects.create(
        request=request,
        vehicle_id=request.vehicle_id,
        kind=kind,
        reason=reason,
        previous_ownership=current,
        new_ownership=new,
        effective_at=moment,
        ticket_id=ticket_id,
        decided_by_user_id=by_user_id,
        decided_at=now,
    )
    after = {
        "kind": kind,
        "owner": _party(new.owner_user_id, new.owner_company_id) if new else before["owner"],
        "since": new.started_at if new else before["since"],
    }
    return decision, {"before": before, "after": after, "vehicle": request.vehicle.public_id}
