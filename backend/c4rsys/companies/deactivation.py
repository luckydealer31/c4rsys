"""Deactivation and restoration of a company (ТЗ § 3.6).

* A user with `company.deactivate` starts deactivation after a one-time code (§ 3.6.1). Active
  rentals, service orders and unfinished warehouse operations block it; the modules tell their
  blockers through `register_blocker`.
* The approval policy of § 3.6.2: none, all owners or owners together holding a percent. Without
  a set policy and with several owners all owners approve. A decision counts only while the owners
  and their shares stay as they were when it was given; right before the change of state the
  owners, shares and decisions are checked again.
* A deactivated company accepts no new orders and no new activity: every command goes through
  `guard`, which lets pass only the commands of `ALLOWED` (settlements of former obligations,
  history and export, access of employees, ownership and the restoration itself, § 3.6.1,
  § 3.6.3). Background processes go on by their own rules: the deposit moves to the balance
  (§ 3.6.4), repeating tasks are paused by the tasks module.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import connection
from django.utils import timezone

from c4rsys.companies import ownership
from c4rsys.companies.models import (
    Company,
    CompanyStateApproval,
    CompanyStateRequest,
    OwnershipPolicy,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext

Kind = CompanyStateRequest.Kind
Mode = OwnershipPolicy.Mode

# --- Blockers and state handlers of other modules -------------------------------------------------

Blocker = Callable[[UUID], list[str]]
StateHandler = Callable[[UUID], None]
_blockers: list[Blocker] = []
_on_restored: list[StateHandler] = []


def register_blocker(check: Blocker) -> None:
    """`check(company_id)` returns codes of the operations that keep the company active."""
    _blockers.append(check)


def register_restored(handler: StateHandler) -> None:
    """Called in the transaction that restores the company (repeating tasks, § 3.6.4)."""
    _on_restored.append(handler)


def blockers(company_id: UUID) -> list[str]:
    return sorted({code for check in _blockers for code in check(company_id)})


def is_deactivated(company_id: UUID) -> bool:
    return Company.objects.filter(pk=company_id, deactivated_at__isnull=False).exists()


# --- No new activity in a deactivated company (§ 3.6.1, § 3.6.3) ----------------------------------

# Commands a deactivated company still runs. Everything else is new activity and is refused with
# COMPANY_DEACTIVATED. The composition is a PROPOSAL (TODO(spec): confirm the list).
ALLOWED = frozenset(
    {
        # Restoration, approvals, ownership and the profile.
        "company.restore",
        # Refused by the command itself when the company is already deactivated.
        "company.deactivate",
        "company.state_request_cancel",
        "company.state_request_complete",
        "company.state_approval_answer",
        "company.update_profile",
        "company.public_fields",
        # Access of employees: who settles, exports or restores needs access (§ 3.6.1). The card,
        # the positions handbook and the organisation structure are not changed (PROPOSAL).
        "employee.invite",
        "employee.cancel_invitation",
        "employee.change_rights",
        "employee.block",
        "employee.restore",
        "employee.terminate",
        # History and export within the rights (§ 3.6.1).
        "data.export",
        "notification.read",
        "notification.rule",
        "report.dashboard_create",
        "report.dashboard_remove",
        "report.dashboard_change",
        "file.upload",
        # Former settlements (§ 3.6.3): receiving debts, returning advances and deposits, paying
        # suppliers and other parties, and the corrections of money records.
        "payment.receive_cash",
        "money.record",
        "money.transfer",
        "money.reverse",
        "payment.relink",
        "payment.change_client",
        "expense.split",
        "settlement.opening",
        "settlement.cancel",
        "settlement.return",
        "supplier.payment",
        "client.refund",
        "offset.create",
        "offset.cancel",
        "offset.correct",
        "cash_account.rename",
        "cash_account.archive",
        # Closing an order that is left only with its settlement (§ 12.4).
        "order.close",
    }
)
ALLOWED_PREFIXES = ("ownership.", "deposit.")


def allowed_when_deactivated(command: str) -> bool:
    return command in ALLOWED or command.startswith(ALLOWED_PREFIXES)


def guard(command: str, actor: Actor) -> None:
    """Refuses new activity of a deactivated company. The row is read with FOR KEY SHARE: a
    deactivation (FOR UPDATE) waits for commands in flight, and a command started after it sees
    the new state."""
    if actor.company_id is None or allowed_when_deactivated(command):
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT deactivated_at FROM company WHERE id = %s FOR KEY SHARE", [actor.company_id]
        )
        row = cursor.fetchone()
    if row is not None and row[0] is not None:
        raise errors.business_rule("COMPANY_DEACTIVATED")


# --- Policy (§ 3.6.2) -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Required:
    mode: str  # none / all / percent — the effective one
    percent: Decimal | None
    configured: str  # the stored mode, «unset» when none is set


def required(company_id: UUID) -> Required:
    policy = ownership.policy_of(company_id)
    if policy.state_mode == Mode.UNSET:
        # § 3.6.2: several owners and no policy — all owners. With one owner the ТЗ says nothing;
        # PROPOSAL: no additional approval (TODO(spec)).
        several = len(ownership.current_shares(company_id)) > 1
        return Required(Mode.ALL if several else Mode.NONE, None, Mode.UNSET)
    return Required(policy.state_mode, policy.state_percent, policy.state_mode)


def composition(company_id: UUID) -> str:
    """A fingerprint of the current owners and shares."""
    parts = sorted(
        f"{ownership.party_of_share(s).key}:{s.percent.normalize()}"
        for s in ownership.current_shares(company_id)
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


@dataclass(frozen=True)
class Tally:
    approved: Decimal
    declined: Decimal
    parties: dict[str, str]  # owner key → counted decision
    satisfied: bool
    impossible: bool


def tally(request: CompanyStateRequest) -> Tally:
    shares = {
        ownership.party_of_share(s).key: s.percent
        for s in ownership.current_shares(request.company_id)
    }
    current = composition(request.company_id)
    latest: dict[str, str] = {}
    for a in request.approvals.order_by("decided_at", "id"):
        if a.composition != current:
            continue
        key = ownership.party_key(a.party_user, a.party_company)
        if key in shares:
            latest[key] = a.decision
    approved = sum(
        (shares[k] for k, d in latest.items() if d == CompanyStateApproval.Decision.APPROVED),
        Decimal(0),
    )
    declined = sum(
        (shares[k] for k, d in latest.items() if d == CompanyStateApproval.Decision.DECLINED),
        Decimal(0),
    )
    need = required(request.company_id)
    if need.mode == Mode.NONE:
        satisfied, impossible = True, False
    elif need.mode == Mode.ALL:
        satisfied = all(latest.get(k) == CompanyStateApproval.Decision.APPROVED for k in shares)
        impossible = declined > 0
    else:
        threshold = need.percent or Decimal(100)
        satisfied = approved >= threshold
        impossible = Decimal(100) - declined < threshold
    return Tally(approved, declined, latest, satisfied, impossible)


# --- Commands -------------------------------------------------------------------------------------


def _lock(company_id: UUID) -> Company:
    return Company.objects.select_for_update().get(pk=company_id)


def pending_request(company_id: UUID) -> CompanyStateRequest | None:
    return CompanyStateRequest.objects.filter(
        company_id=company_id, state=CompanyStateRequest.State.PENDING
    ).first()


def start(ctx: CommandContext, company_id: UUID, kind: str) -> CompanyStateRequest:
    """Deactivation or restoration; done at once when the policy needs nothing more."""
    company = _lock(company_id)
    if kind == Kind.DEACTIVATE and company.deactivated_at is not None:
        raise errors.business_rule("COMPANY_ALREADY_DEACTIVATED")
    if kind == Kind.RESTORE and company.deactivated_at is None:
        raise errors.business_rule("COMPANY_NOT_DEACTIVATED")
    if pending_request(company_id) is not None:
        raise errors.business_rule("COMPANY_STATE_REQUEST_PENDING")
    if kind == Kind.DEACTIVATE:
        found = blockers(company_id)
        if found:
            raise errors.business_rule("COMPANY_DEACTIVATION_BLOCKED", blockers=found)
    request = CompanyStateRequest.objects.create(
        company=company,
        kind=kind,
        initiated_by_user_id=ctx.actor.user_id,
        initiated_by_employment_id=ctx.actor.employment_id,
    )
    need = required(company_id)
    ctx.audit(
        f"company.{kind}_requested",
        object_type="company_state_request",
        object_id=request.id,
        after={"kind": kind, "policy": need.configured, "mode": need.mode, "percent": need.percent},
    )
    # PROPOSAL: an initiator who owns a share approves by starting the request.
    initiator = ownership.Party(user=ownership.user_by_id(ctx.actor.user_id), company=None)
    if any(
        ownership.party_of_share(s).key == initiator.key
        for s in ownership.current_shares(company_id)
    ):
        _record(ctx, request, initiator, approve=True)
    try_complete(ctx, request)
    return request


def _record(
    ctx: CommandContext, request: CompanyStateRequest, party: "ownership.Party", *, approve: bool
) -> CompanyStateApproval:
    decision = (
        CompanyStateApproval.Decision.APPROVED
        if approve
        else CompanyStateApproval.Decision.DECLINED
    )
    found = CompanyStateApproval.objects.create(
        company_id=request.company_id,
        request=request,
        party_user=party.user,
        party_company=party.company,
        decision=decision,
        composition=composition(request.company_id),
        decided_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        f"company.state_{decision}",
        object_type="company_state_request",
        object_id=request.id,
        after={"party": party.key, "kind": request.kind},
        company_id=request.company_id,
    )
    return found


def try_complete(ctx: CommandContext, request: CompanyStateRequest) -> bool:
    """Right before the change the owners, shares, decisions and blockers are read again."""
    company = _lock(request.company_id)
    current = tally(request)
    if current.impossible:
        request.state = CompanyStateRequest.State.DECLINED
        request.decided_at = timezone.now()
        request.save(update_fields=["state", "decided_at"])
        ctx.audit(
            "company.state_request_declined",
            object_type="company_state_request",
            object_id=request.id,
            company_id=request.company_id,
        )
        return False
    if not current.satisfied:
        return False
    if request.kind == Kind.DEACTIVATE and blockers(company.id):
        return False
    now = timezone.now()
    before = company.deactivated_at
    company.deactivated_at = now if request.kind == Kind.DEACTIVATE else None
    company.version += 1
    company.save(update_fields=["deactivated_at", "version", "updated_at"])
    request.state = CompanyStateRequest.State.COMPLETED
    request.decided_at = now
    request.save(update_fields=["state", "decided_at"])
    if request.kind == Kind.RESTORE:
        for handler in _on_restored:
            handler(company.id)
    ctx.audit(
        "company.deactivated" if request.kind == Kind.DEACTIVATE else "company.restored",
        object_type="company",
        object_id=company.public_id,
        before={"deactivated_at": before},
        after={"deactivated_at": company.deactivated_at, "request": str(request.id)},
        company_id=company.id,
    )
    return True


def _pending(company_id: UUID, request_id: UUID) -> CompanyStateRequest:
    _lock(company_id)
    request = (
        CompanyStateRequest.objects.select_for_update()
        .filter(company_id=company_id, pk=request_id)
        .first()
    )
    if request is None:
        raise errors.not_found()
    if request.state != CompanyStateRequest.State.PENDING:
        raise errors.business_rule("COMPANY_STATE_REQUEST_NOT_PENDING")
    return request


def decide(
    ctx: CommandContext, user_id: UUID, request_id: UUID, party_key: str, *, approve: bool
) -> CompanyStateRequest:
    """An owner's decision, given by the user for himself or for an owner company where he has
    full rights (PROPOSAL)."""
    request = CompanyStateRequest.objects.filter(pk=request_id).first()
    if request is None:
        raise errors.not_found()
    request = _pending(request.company_id, request_id)
    owners = {
        ownership.party_of_share(s).key: ownership.party_of_share(s)
        for s in ownership.current_shares(request.company_id)
    }
    party = owners.get(party_key)
    if party is None or not ownership.can_act_for(user_id, party):
        raise errors.not_found()
    _record(ctx, request, party, approve=approve)
    try_complete(ctx, request)
    return request


def cancel(ctx: CommandContext, company_id: UUID, request_id: UUID) -> CompanyStateRequest:
    request = _pending(company_id, request_id)
    request.state = CompanyStateRequest.State.CANCELLED
    request.decided_at = timezone.now()
    request.save(update_fields=["state", "decided_at"])
    ctx.audit(
        "company.state_request_cancelled",
        object_type="company_state_request",
        object_id=request.id,
    )
    return request


def complete(ctx: CommandContext, company_id: UUID, request_id: UUID) -> CompanyStateRequest:
    """A new attempt when the approvals are there but a blocker appeared meanwhile."""
    request = _pending(company_id, request_id)
    if not try_complete(ctx, request):
        current = tally(request)
        if not current.satisfied:
            raise errors.business_rule("COMPANY_STATE_APPROVALS_MISSING")
        raise errors.business_rule("COMPANY_DEACTIVATION_BLOCKED", blockers=blockers(company_id))
    return request


# --- Reading --------------------------------------------------------------------------------------


def pending_for(user_id: UUID) -> list[tuple[CompanyStateRequest, "ownership.Party"]]:
    """Requests waiting for a decision the user may give: for himself as an owner, or for an
    owner company where he has full rights; a counted decision is not asked again."""
    found = []
    for request in CompanyStateRequest.objects.select_related("company").filter(
        state=CompanyStateRequest.State.PENDING
    ):
        decided = tally(request).parties
        for party in ownership.owners_of(request.company_id):
            if party.key not in decided and ownership.can_act_for(user_id, party):
                found.append((request, party))
    return found


def request_payload(request: CompanyStateRequest) -> dict[str, Any]:
    current = tally(request)
    need = required(request.company_id)
    owners = []
    for share in ownership.current_shares(request.company_id):
        party = ownership.party_of_share(share)
        owners.append(
            {
                "owner": ownership.owner_card(party),
                "percent": str(share.percent),
                "decision": current.parties.get(party.key),
            }
        )
    history = [
        {
            "party": ownership.party_key(a.party_user, a.party_company),
            "decision": a.decision,
            "decided_at": a.decided_at.isoformat(),
            "counted": a.composition == composition(request.company_id),
        }
        for a in request.approvals.order_by("decided_at", "id")
    ]
    return {
        "id": str(request.id),
        "kind": request.kind,
        "state": request.state,
        "created_at": request.created_at.isoformat(),
        "decided_at": request.decided_at.isoformat() if request.decided_at else None,
        "mode": need.mode,
        "percent": str(need.percent) if need.percent is not None else None,
        "approved_percent": str(current.approved),
        "satisfied": current.satisfied,
        "owners": owners,
        "history": history,
    }
