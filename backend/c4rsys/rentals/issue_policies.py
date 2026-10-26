"""Policies and exceptions of the issue (ТЗ § 14.3.2, § 14.3.5–14.3.7, § 23.4.7).

A new company has non-blocking policies: client debt and incomplete payment warn, documents are
not required, the condition check of the inspection is not required (§ 14.3.6). A company may set
stricter ones for the whole company or a rental location (customer's decisions В2–В3). A strict
requirement left unmet is passed only by an exception: permitted in advance by an authorized
employee, or recorded after the transfer by the issuing employee and then confirmed or rejected
(§ 14.3.7). Insufficient deposit (§ 14.3.3) and a missing inspection (§ 10.3.3) never block.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.finance import payments
from c4rsys.orders import services as orders
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money
from c4rsys.rentals import pricing
from c4rsys.rentals.models import IssueException, IssuePolicy, Rental

DEFAULTS = {
    "debt": IssuePolicy.Debt.WARN.value,
    "payment": IssuePolicy.Payment.WARN.value,
    "documents": IssuePolicy.Documents.NONE.value,
    "inspection": IssuePolicy.Inspection.NOT_REQUIRED.value,
}
FIELDS = tuple(DEFAULTS)
# Requirements an exception may cover; deposit and inspection are not among them (§ 14.3.7).
REQUIREMENTS = ("payment", "debt", "documents")


# --- Policy ---------------------------------------------------------------------------------


def _stored(company_id: UUID, location_id: UUID | None) -> IssuePolicy | None:
    query = IssuePolicy.objects.filter(company_id=company_id)
    if location_id is None:
        return query.filter(location__isnull=True).first()
    return query.filter(location_id=location_id).first()


def policy_for(company_id: UUID, location_id: UUID | None) -> dict[str, Any]:
    """The policy in effect: of the rental location if it has its own, else of the company, else
    the start policies (§ 14.3.6)."""
    rows = {
        p.location_id: p
        for p in IssuePolicy.objects.filter(company_id=company_id).filter(
            Q(location__isnull=True) | Q(location_id__in=[location_id] if location_id else [])
        )
    }
    chosen, source = (rows.get(location_id), "location") if location_id else (None, "")
    if chosen is None:
        chosen, source = rows.get(None), "company"
    if chosen is None:
        return {**DEFAULTS, "source": "default"}
    return {**{f: getattr(chosen, f) for f in FIELDS}, "source": source}


def policy_payload(company_id: UUID, location_id: UUID | None) -> dict[str, Any]:
    stored = _stored(company_id, location_id)
    return {
        "location_id": str(location_id) if location_id else None,
        "own": stored is not None,
        **({f: getattr(stored, f) for f in FIELDS} if stored else DEFAULTS),
        "effective": policy_for(company_id, location_id),
        "version": stored.version if stored else 0,
    }


def set_policy(
    ctx: CommandContext, company_id: UUID, location_id: UUID | None, values: dict[str, str]
) -> IssuePolicy:
    choices = {
        "debt": IssuePolicy.Debt.values,
        "payment": IssuePolicy.Payment.values,
        "documents": IssuePolicy.Documents.values,
        "inspection": IssuePolicy.Inspection.values,
    }
    for name in FIELDS:
        if values.get(name) not in choices[name]:
            raise errors.validation_error(name, code="CHOICE_REQUIRED")
    if location_id is not None:
        locations.require_usable(company_id, location_id, "location_id")
    query = IssuePolicy.objects.select_for_update().filter(company_id=company_id)
    policy = (
        query.filter(location__isnull=True)
        if location_id is None
        else query.filter(location_id=location_id)
    ).first()
    before = {f: getattr(policy, f) for f in FIELDS} if policy else None
    if policy is None:
        policy = IssuePolicy(company_id=company_id, location_id=location_id, version=0)
    for name in FIELDS:
        setattr(policy, name, values[name])
    policy.updated_by_user_id = ctx.actor.user_id
    policy.updated_at = timezone.now()
    policy.version += 1
    policy.save()
    ctx.audit(
        "rental.issue_policy_changed",
        object_type="rental_issue_policy",
        object_id=policy.id,
        before=before,
        after={"location": str(location_id) if location_id else None, **values},
    )
    return policy


# --- Requirements at the issue ---------------------------------------------------------------


@dataclass
class Assessment:
    policy: dict[str, Any]
    unmet: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def unmet_codes(self) -> list[str]:
        return [u["code"] for u in self.unmet]

    @property
    def inspection_required(self) -> bool:
        return bool(self.policy["inspection"] == IssuePolicy.Inspection.REQUIRED)


def _rounded(amounts: dict[str, Decimal]) -> dict[str, str]:
    places = {c.code: c.minor_units for c in Currency.objects.filter(code__in=amounts)}
    return {c: str(round_money(v, places[c])) for c, v in sorted(amounts.items())}


def assess(
    company_id: UUID,
    location_id: UUID | None,
    *,
    client_id: UUID | None,
    order_id: UUID | None,
    snapshot: dict[str, Any] | None,
    start: datetime,
    planned_end: datetime | None,
) -> Assessment:
    """Checks client debt (§ 14.3.5) and the payment of the new rental (§ 14.3.2) by the policy
    in effect. Full payment: paid to the order, beyond its other charges, at least the preliminary
    cost to the planned end; without a planned end it cannot be checked — a warning (В4)."""
    result = Assessment(policy_for(company_id, location_id))
    policy = result.policy
    if client_id is not None and policy["debt"] != IssuePolicy.Debt.ALLOW:
        debt = payments.client_debt(company_id, client_id)
        if debt:
            item = {"code": "debt", "amounts": _rounded(debt)}
            if policy["debt"] == IssuePolicy.Debt.DECIDE:
                result.unmet.append(item)
            result.warnings.append({"code": "CLIENT_HAS_DEBT", "amounts": item["amounts"]})
    if snapshot is None:
        return result
    strict = policy["payment"] == IssuePolicy.Payment.REQUIRE
    if planned_end is None:
        if strict:
            result.warnings.append({"code": "PAYMENT_NOT_CHECKED"})
        return result
    cost = pricing.calculate(pricing.terms_from_snapshot(snapshot), start, planned_end)
    available = Decimal(0)
    if order_id is not None:
        order = orders.get_order(company_id, order_id)
        money = orders.amounts_of(order).get(cost.currency)
        if money is not None:
            available = money.paid - money.charged
    missing = cost.base_amount - max(available, Decimal(0))
    if missing > 0:
        amounts = _rounded({cost.currency: missing})
        if strict:
            result.unmet.append({"code": "payment", "amounts": amounts})
        result.warnings.append({"code": "PAYMENT_INCOMPLETE", "amounts": amounts})
    return result


# --- Exceptions (§ 14.3.7) ------------------------------------------------------------------


def exception_of(rental_id: UUID) -> IssueException | None:
    return IssueException.objects.filter(rental_id=rental_id).first()


def _check_requirements(requirements: list[str]) -> list[str]:
    chosen = [r for r in REQUIREMENTS if r in set(requirements)]
    if not chosen or len(chosen) != len(set(requirements)):
        raise errors.validation_error("requirements", code="CHOICE_REQUIRED")
    return chosen


def _reason(reason: str, field_name: str = "reason") -> str:
    if not reason.strip():
        raise errors.validation_error(field_name, code="FIELD_REQUIRED")
    return reason.strip()


def _payload(item: IssueException) -> dict[str, Any]:
    return {
        "mode": item.mode,
        "state": item.state,
        "requirements": item.requirements,
        "reason": item.reason,
        "decision_reason": item.decision_reason,
        "decided_at": item.decided_at,
    }


def grant(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    requirements: list[str],
    reason: str,
    expected_version: int,
) -> IssueException:
    """An advance permission on a booking: the issue may go without the chosen requirements
    being met; it does not meet them (§ 14.3.7). A new permission replaces one not yet used."""
    chosen = _check_requirements(requirements)
    text = _reason(reason)
    rental = Rental.objects.select_for_update().filter(company_id=company_id, pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    if rental.version != expected_version:
        raise errors.version_conflict()
    if rental.state != Rental.State.BOOKED:
        raise errors.business_rule("BOOKING_NOT_ACTIVE")
    item = IssueException.objects.select_for_update().filter(rental_id=rental.id).first()
    before = _payload(item) if item else None
    if item is None:
        item = IssueException(company_id=company_id, rental=rental, version=0)
    item.mode = IssueException.Mode.ADVANCE
    item.state = IssueException.State.GRANTED
    item.requirements = chosen
    item.reason = text
    item.created_by_user_id = ctx.actor.user_id
    item.created_at = timezone.now()
    item.version += 1
    item.save()
    rental.version += 1
    rental.save(update_fields=["version", "updated_at"])
    ctx.audit(
        "rental.issue_exception_granted",
        object_type="rental",
        object_id=rental.id,
        before=before,
        after=_payload(item),
    )
    return item


def revoke(ctx: CommandContext, company_id: UUID, rental_id: UUID, expected_version: int) -> None:
    rental = Rental.objects.select_for_update().filter(company_id=company_id, pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    if rental.version != expected_version:
        raise errors.version_conflict()
    item = IssueException.objects.select_for_update().filter(rental_id=rental.id).first()
    if item is None or item.state != IssueException.State.GRANTED:
        raise errors.business_rule("ISSUE_EXCEPTION_NOT_GRANTED")
    before = _payload(item)
    item.delete()
    rental.version += 1
    rental.save(update_fields=["version", "updated_at"])
    ctx.audit(
        "rental.issue_exception_revoked", object_type="rental", object_id=rental.id, before=before
    )


@dataclass(frozen=True)
class ExceptionRequest:
    """How the issue passes unmet strict requirements: `advance` — the issuing employee has the
    right to permit it now; `after_fact` — the vehicle has already been handed over."""

    mode: str = ""
    reason: str = ""
    may_authorize: bool = False


def resolve(
    ctx: CommandContext, rental: Rental, assessment: Assessment, request: ExceptionRequest
) -> IssueException | None:
    """Called by the issue once the rental is active. Unmet requirements need an advance
    permission covering them, a permission given now, or a recorded transfer (§ 14.3.7)."""
    unmet = assessment.unmet_codes
    item = IssueException.objects.select_for_update().filter(rental_id=rental.id).first()
    if not unmet:
        return item
    if item is not None and item.state == IssueException.State.GRANTED:
        if set(unmet) <= set(item.requirements):
            item.state = IssueException.State.APPLIED
            item.version += 1
            item.save(update_fields=["state", "version"])
            ctx.audit(
                "rental.issue_exception_applied",
                object_type="rental",
                object_id=rental.id,
                after={**_payload(item), "unmet": assessment.unmet},
            )
            return item
    if request.mode == IssueException.Mode.ADVANCE and request.may_authorize:
        mode, state = IssueException.Mode.ADVANCE, IssueException.State.APPLIED
    elif request.mode == IssueException.Mode.AFTER_FACT:
        mode, state = IssueException.Mode.AFTER_FACT, IssueException.State.PENDING
    else:
        raise errors.business_rule(
            "ISSUE_REQUIREMENTS_UNMET",
            requirements=assessment.unmet,
            can_authorize=request.may_authorize,
        )
    text = _reason(request.reason, "exception_reason")
    if item is not None:  # an advance permission that does not cover everything is replaced
        item.delete()
    item = IssueException.objects.create(
        company_id=rental.company_id,
        rental=rental,
        mode=mode,
        state=state,
        requirements=unmet,
        reason=text,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "rental.issue_exception_recorded"
        if mode == IssueException.Mode.AFTER_FACT
        else "rental.issue_exception_applied",
        object_type="rental",
        object_id=rental.id,
        after={**_payload(item), "unmet": assessment.unmet},
    )
    return item


def decide(
    ctx: CommandContext,
    company_id: UUID,
    rental_id: UUID,
    confirm: bool,
    reason: str,
    expected_version: int,
) -> IssueException:
    """Confirms or rejects an exception recorded after the transfer. A rejection does not undo
    the issue or free the vehicle: it marks a violation (§ 14.3.7; customer's decision В7)."""
    text = _reason(reason)
    rental = Rental.objects.select_for_update().filter(company_id=company_id, pk=rental_id).first()
    if rental is None:
        raise errors.not_found()
    if rental.version != expected_version:
        raise errors.version_conflict()
    item = IssueException.objects.select_for_update().filter(rental_id=rental.id).first()
    if item is None or item.state != IssueException.State.PENDING:
        raise errors.business_rule("ISSUE_EXCEPTION_NOT_PENDING")
    before = _payload(item)
    item.state = IssueException.State.CONFIRMED if confirm else IssueException.State.REJECTED
    item.decision_reason = text
    item.decided_by_user_id = ctx.actor.user_id
    item.decided_at = timezone.now()
    item.version += 1
    item.save()
    rental.version += 1
    rental.save(update_fields=["version", "updated_at"])
    ctx.audit(
        "rental.issue_exception_confirmed" if confirm else "rental.issue_exception_rejected",
        object_type="rental",
        object_id=rental.id,
        before=before,
        after=_payload(item),
    )
    return item


def payload(rental_id: UUID) -> dict[str, Any] | None:
    item = exception_of(rental_id)
    if item is None:
        return None
    return {
        **_payload(item),
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "created_at": item.created_at.isoformat(),
        "version": item.version,
    }
