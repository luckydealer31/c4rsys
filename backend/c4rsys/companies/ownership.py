"""Owners, share transfers, corporate ownership and succession of the top authority.

ТЗ § 3.3.4, § 3.3.6, § 3.4.1–3.4.6, § 3.5.4.

* An owner is a user or another company. A share gives owner rights only: to see the owners,
  the shares and the history of ownership and to start a transfer of the own share (§ 3.3.4).
* Each owner has a dedicated position (`OwnerSeat`). A user owner's position is for himself; an
  owner company's position is for its representative within the limit of rights set by the top
  authority of this company (§ 3.4.1).
* A transfer is started by the current owner of the share with a one-time code; it waits for the
  recipient, for each owner of an owner company on either side, for the co-owners by the policy
  of § 3.4.2 and for the successor of the top authority (§ 3.4.6). Until then nothing changes;
  then one operation moves the share.
* No company owns its own share and no chain of owners closes a cycle (§ 3.5.4).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.accounts import services as accounts
from c4rsys.accounts.models import UserAccount
from c4rsys.companies import access, orgstructure, services
from c4rsys.companies.models import (
    Company,
    Employment,
    Invitation,
    OwnerSeat,
    OwnershipPolicy,
    OwnershipShare,
    Position,
    ShareTransfer,
    ShareTransferApproval,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.ids import normalize_public_id

FULL = Decimal("100")
# § 2.5.1: a request lasts 7 days; a term chosen at sending is not offered (as invitations at
# stage 1, decision 22.10.2026).
TRANSFER_TTL = timedelta(days=7)
Role = ShareTransferApproval.Role
Mode = OwnershipPolicy.Mode


# --- Parties ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Party:
    """An owner, a sender, a recipient or a successor: a user or a company."""

    user: UserAccount | None
    company: Company | None

    @property
    def key(self) -> str:
        return party_key(self.user, self.company)

    @property
    def is_company(self) -> bool:
        return self.company is not None

    def fields(self, prefix: str) -> dict[str, Any]:
        return {f"{prefix}_user": self.user, f"{prefix}_company": self.company}


def party_key(user: UserAccount | None, company: Company | None) -> str:
    if company is not None:
        return company.public_id
    if user is None:
        raise ValueError("user")
    return user.public_id


def user_by_id(user_id: UUID) -> UserAccount | None:
    return accounts.get_user(user_id)


def party_of_share(share: OwnershipShare) -> Party:
    return Party(user=share.owner_user, company=share.owner_company)


def resolve_party(value: str, field: str = "recipient") -> Party:
    """A user by c4rsys ID or username, a company by its company ID (§ 2.1.4, § 3.1.1)."""
    text = value.strip()
    public_id = normalize_public_id(text)
    if public_id is not None and public_id.startswith("USR-"):
        user = accounts.find_active_user(public_id)
        if user is not None:
            return Party(user=user, company=None)
    elif public_id is not None and public_id.startswith("CMP-"):
        company = Company.objects.filter(public_id=public_id).first()
        if company is not None:
            return Party(user=None, company=company)
    else:
        user = accounts.find_active_user_by_username(text)
        if user is not None:
            return Party(user=user, company=None)
    raise errors.business_rule("OWNERSHIP_PARTY_NOT_FOUND", field=field)


def owner_card(party: Party) -> dict[str, Any]:
    """Minimal data of an owner seen by the owners (§ 3.3.6): first and last name and ID of a
    user; display name and company ID of a company."""
    if party.company is not None:
        return {
            "type": "company",
            "public_id": party.company.public_id,
            "display_name": party.company.display_name,
        }
    if party.user is None:
        raise ValueError("party.user")
    return {
        "type": "user",
        "public_id": party.user.public_id,
        "first_name": party.user.first_name,
        "last_name": party.user.last_name,
    }


def search_card(party: Party) -> dict[str, Any]:
    """Before acceptance: a user by ID and username only (§ 2.2.4); a company by its minimal
    search card (§ 3.1.8)."""
    if party.company is not None:
        return {
            "type": "company",
            "public_id": party.company.public_id,
            "display_name": party.company.display_name,
            "logo": str(party.company.logo_id) if party.company.logo_id else None,
            "country": party.company.country,
            # Fields the company made public (§ 3.1.8, § 3.5.3).
            "public": services.public_data(party.company),
        }
    if party.user is None:
        raise ValueError("party.user")
    return {"type": "user", "public_id": party.user.public_id, "username": party.user.username}


# --- Current owners -----------------------------------------------------------------------------


def current_shares(company_id: UUID) -> list[OwnershipShare]:
    return list(
        OwnershipShare.objects.filter(company_id=company_id, valid_to__isnull=True)
        .select_related("owner_user", "owner_company")
        .order_by("-percent", "valid_from")
    )


def share_of(company_id: UUID, party: Party) -> OwnershipShare | None:
    return next((s for s in current_shares(company_id) if party_of_share(s).key == party.key), None)


def owners_of(company_id: UUID) -> list[Party]:
    return [party_of_share(s) for s in current_shares(company_id)]


def owned_companies(user: UserAccount) -> QuerySet[Company]:
    return Company.objects.filter(shares__owner_user=user, shares__valid_to__isnull=True).distinct()


def is_owner(company_id: UUID, user_id: UUID) -> bool:
    return OwnershipShare.objects.filter(
        company_id=company_id, owner_user_id=user_id, valid_to__isnull=True
    ).exists()


def full_rights_employment(user_id: UUID, company_id: UUID) -> Employment | None:
    employment = (
        Employment.objects.select_related("position")
        .filter(company_id=company_id, user_id=user_id, state=Employment.State.ACTIVE)
        .first()
    )
    if employment is not None and access.has_full_rights(employment):
        return employment
    return None


def can_act_for(user_id: UUID, party: Party) -> bool:
    """A user decides for himself; a company through its top authority or an employee with full
    rights (§ 3.4.1 for the representative; for the other decisions a PROPOSAL)."""
    if party.user is not None:
        return party.user.id == user_id
    if party.company is None:
        raise ValueError("party.company")
    return full_rights_employment(user_id, party.company.id) is not None


def policy_of(company_id: UUID) -> OwnershipPolicy:
    found = OwnershipPolicy.objects.filter(company_id=company_id).first()
    return found or OwnershipPolicy(company_id=company_id)


def check_sum(company_id: UUID) -> None:
    """§ 3.3.4: the current shares make 100%."""
    total = sum((s.percent for s in current_shares(company_id)), Decimal(0))
    if total != FULL:
        raise errors.business_rule("OWNERSHIP_SUM_INVALID", total=str(total))


# --- Cycles (§ 3.5.4) -----------------------------------------------------------------------------


def ancestors(company_id: UUID) -> set[UUID]:
    """Companies owning the company directly or through other companies."""
    found: set[UUID] = set()
    frontier = {company_id}
    while frontier:
        owners = set(
            OwnershipShare.objects.filter(
                company_id__in=frontier, valid_to__isnull=True, owner_company__isnull=False
            ).values_list("owner_company_id", flat=True)
        )
        frontier = {x for x in owners if x is not None} - found
        found |= frontier
    return found


def check_no_cycle(company_id: UUID, recipient: Party) -> None:
    if recipient.company is None:
        return
    if recipient.company.id == company_id or company_id in ancestors(recipient.company.id):
        raise errors.business_rule("OWNERSHIP_CYCLE", field="recipient")


# --- Dedicated positions (§ 3.3.4, § 3.4.1, § 3.4.4) ---------------------------------------------


def current_seat(company_id: UUID, party: Party) -> OwnerSeat | None:
    return (
        OwnerSeat.objects.select_for_update(of=("self",))
        .filter(company_id=company_id, **party.fields("owner"))
        .exclude(state=OwnerSeat.State.ENDED)
        .select_related("position", "holder")
        .first()
    )


def top_seat(company_id: UUID) -> OwnerSeat | None:
    return (
        OwnerSeat.objects.filter(
            company_id=company_id, state=OwnerSeat.State.ACTIVE, position__is_top_authority=True
        )
        .select_related("position", "holder", "owner_user", "owner_company")
        .first()
    )


def _prepare_seat(ctx: CommandContext, company: Company, party: Party, now: datetime) -> OwnerSeat:
    """§ 3.4.3: a dedicated position is prepared for the recipient; it gives no access before
    the share passes. A recipient that already owns a share keeps its position."""
    seat = current_seat(company.id, party)
    if seat is not None:
        return seat
    code = Position.Code.OWNER_REPRESENTATIVE if party.is_company else Position.Code.OWNER
    position = Position.objects.create(
        company=company, code=code, is_owner_position=True, created_at=now
    )
    seat = OwnerSeat.objects.create(
        company=company, position=position, created_at=now, **party.fields("owner")
    )
    ctx.audit(
        "ownership.seat_prepared",
        object_type="owner_seat",
        object_id=seat.id,
        after={"owner": party.key, "position": str(position.id), "code": code},
        company_id=company.id,
    )
    return seat


def _terminate(ctx: CommandContext, employment: Employment, reason: str) -> None:
    if employment.state == Employment.State.TERMINATED:
        return
    before = employment.state
    employment.state = Employment.State.TERMINATED
    employment.version += 1
    employment.save(update_fields=["state", "version"])
    ctx.audit(
        "employment.terminated",
        object_type="employment",
        object_id=employment.id,
        before={"state": before},
        after={"state": employment.state},
        reason=reason,
        company_id=employment.company_id,
    )
    orgstructure.on_terminated(ctx, employment)


def _release_holder(ctx: CommandContext, seat: OwnerSeat, reason: str) -> None:
    """The holder leaves the dedicated position: its rights of the position are revoked and an
    employment held only through it ends (§ 3.4.4)."""
    holder = seat.holder
    if holder is None:
        return
    holder = (
        Employment.objects.select_for_update(of=("self",))
        .select_related("position")
        .get(pk=holder.pk)
    )
    access.revoke(access.seat_assignments(holder.id, seat.id), ctx.actor.user_id)
    if holder.position_id == seat.position_id:
        _terminate(ctx, holder, reason)


def _cancel_representative_invitations(ctx: CommandContext, seat: OwnerSeat) -> None:
    for invitation in Invitation.objects.select_for_update(of=("self",)).filter(
        company_id=seat.company_id,
        state=Invitation.State.PENDING,
        offered__owner_seat=str(seat.id),
    ):
        invitation.state = Invitation.State.CANCELLED
        invitation.decided_at = timezone.now()
        invitation.save(update_fields=["state", "decided_at"])
        ctx.audit(
            "invitation.cancelled",
            object_type="invitation",
            object_id=invitation.id,
            reason="representative_replaced",
            company_id=seat.company_id,
        )


def _end_seat(ctx: CommandContext, seat: OwnerSeat, now: datetime) -> None:
    """§ 3.4.4: after the whole remaining share is transferred the dedicated position ends with
    the access through it; other positions and their access stay."""
    _release_holder(ctx, seat, "whole_share_transferred")
    _cancel_representative_invitations(ctx, seat)
    seat.state = OwnerSeat.State.ENDED
    seat.ended_at = now
    seat.version += 1
    seat.save(update_fields=["state", "ended_at", "version"])
    ctx.audit(
        "ownership.seat_ended",
        object_type="owner_seat",
        object_id=seat.id,
        reason="whole_share_transferred",
        company_id=seat.company_id,
    )


def _activate_seat(ctx: CommandContext, seat: OwnerSeat, party: Party, now: datetime) -> None:
    if seat.state == OwnerSeat.State.ACTIVE:
        return
    seat.state = OwnerSeat.State.ACTIVE
    seat.activated_at = now
    if party.user is not None:
        # The user's own position: his current employment, or a new card with this position and
        # no rights; working access is given separately (§ 3.4.3).
        employment = (
            Employment.objects.select_for_update(of=("self",))
            .filter(
                company_id=seat.company_id,
                user=party.user,
                state__in=(Employment.State.ACTIVE, Employment.State.BLOCKED),
            )
            .first()
        )
        if employment is None:
            employment = Employment.objects.create(
                company_id=seat.company_id,
                user=party.user,
                position=seat.position,
                state=Employment.State.ACTIVE,
                started_at=now,
                created_at=now,
            )
            ctx.audit(
                "employment.created",
                object_type="employment",
                object_id=employment.id,
                after={
                    "user": party.user.public_id,
                    "position": str(seat.position_id),
                    "position_code": seat.position.code,
                    "state": employment.state,
                },
                company_id=seat.company_id,
            )
            orgstructure.attach_new(ctx, employment)
        seat.holder = employment
    seat.version += 1
    seat.save(update_fields=["state", "activated_at", "holder", "version"])
    ctx.audit(
        "ownership.seat_activated",
        object_type="owner_seat",
        object_id=seat.id,
        after={"owner": party.key, "holder": str(seat.holder_id) if seat.holder_id else None},
        company_id=seat.company_id,
    )


# --- Transfer (§ 3.4.3, § 3.4.6) ------------------------------------------------------------------


def effective_state(transfer: ShareTransfer, now: datetime) -> str:
    if transfer.state == ShareTransfer.State.PENDING and transfer.expires_at <= now:
        return ShareTransfer.State.EXPIRED
    return transfer.state


def _lock_company(company_id: UUID) -> Company:
    # FOR NO KEY UPDATE: serialises the changes of ownership of one company without waiting for
    # ordinary commands (they read the row FOR KEY SHARE); a deactivation (FOR UPDATE) waits.
    return Company.objects.select_for_update(no_key=True).get(pk=company_id)


@dataclass(frozen=True)
class NewTransfer:
    sender: Party
    recipient: Party
    percent: Decimal
    successor: Party | None = None


def _validate_successor(
    company_id: UUID, sender: Party, recipient: Party, successor: Party | None, whole: bool
) -> None:
    top = top_seat(company_id)
    sender_is_top = top is not None and party_key(top.owner_user, top.owner_company) == sender.key
    if whole and sender_is_top:
        if successor is None:
            raise errors.business_rule("TOP_AUTHORITY_SUCCESSOR_REQUIRED", field="successor")
        # § 3.4.6: from the owners of the company; the recipient becomes one with the transfer
        # (PROPOSAL: the recipient may be chosen).
        allowed = {p.key for p in owners_of(company_id)} | {recipient.key}
        if successor.key == sender.key or successor.key not in allowed:
            raise errors.business_rule("SUCCESSOR_NOT_OWNER", field="successor")
    elif successor is not None:
        raise errors.business_rule("SUCCESSOR_NOT_EXPECTED", field="successor")


def start_transfer(ctx: CommandContext, company_id: UUID, new: NewTransfer) -> ShareTransfer:
    """The current owner starts the transfer; the caller has checked the one-time code."""
    # § 2.3.1: no transfer of ownership within 72 hours after a recovery through the support.
    accounts.require_not_recently_recovered(ctx.actor.user_id)
    company = _lock_company(company_id)
    now = timezone.now()
    own = share_of(company.id, new.sender)
    if own is None:
        raise errors.permission_denied("ownership.transfer")
    if new.percent <= 0 or new.percent > own.percent:
        raise errors.business_rule(
            "TRANSFER_EXCEEDS_SHARE", field="percent", share=str(own.percent)
        )
    if new.recipient.key == new.sender.key:
        raise errors.business_rule("TRANSFER_RECIPIENT_INVALID", field="recipient")
    check_no_cycle(company.id, new.recipient)
    whole = new.percent == own.percent
    _validate_successor(company.id, new.sender, new.recipient, new.successor, whole)

    # § 2.5.2: one pending request per pair; the same conditions remind and extend the term,
    # changed conditions cancel the former request.
    for pending in ShareTransfer.objects.select_for_update(of=("self",)).filter(
        company=company,
        state=ShareTransfer.State.PENDING,
        **new.sender.fields("sender"),
        **new.recipient.fields("recipient"),
    ):
        if effective_state(pending, now) == ShareTransfer.State.EXPIRED:
            _close(ctx, pending, ShareTransfer.State.EXPIRED, now)
            continue
        same_successor = party_key_or_none(pending.successor_user, pending.successor_company) == (
            new.successor.key if new.successor else None
        )
        if pending.percent == new.percent and same_successor:
            pending.expires_at = now + TRANSFER_TTL
            pending.save(update_fields=["expires_at"])
            ctx.audit(
                "ownership.transfer_reminded",
                object_type="share_transfer",
                object_id=pending.id,
                after={"expires_at": pending.expires_at},
                company_id=company.id,
            )
            return pending
        _close(ctx, pending, ShareTransfer.State.CANCELLED, now, reason="conditions_changed")

    policy = policy_of(company.id)
    seat = _prepare_seat(ctx, company, new.recipient, now)
    transfer = ShareTransfer.objects.create(
        company=company,
        percent=new.percent,
        recipient_seat=seat,
        policy_mode=policy.transfer_mode,
        policy_percent=policy.transfer_percent,
        initiated_by_user_id=ctx.actor.user_id,
        created_at=now,
        expires_at=now + TRANSFER_TTL,
        **new.sender.fields("sender"),
        **new.recipient.fields("recipient"),
        **(new.successor.fields("successor") if new.successor else {}),
    )
    rows: list[tuple[str, Party]] = []
    if new.sender.company is not None:
        rows += [(Role.SENDER_OWNER, p) for p in owners_of(new.sender.company.id)]
    if new.recipient.company is not None:
        rows += [(Role.RECIPIENT_OWNER, p) for p in owners_of(new.recipient.company.id)]
    else:
        rows.append((Role.RECIPIENT, new.recipient))
    if policy.transfer_mode in (Mode.ALL, Mode.PERCENT):
        rows += [(Role.COOWNER, p) for p in owners_of(company.id) if p.key != new.sender.key]
    if new.successor is not None:
        rows.append((Role.SUCCESSOR, new.successor))
    for role, party in rows:
        ShareTransferApproval.objects.create(
            company=company, transfer=transfer, role=role, created_at=now, **party.fields("party")
        )
    ctx.audit(
        "ownership.transfer_requested",
        object_type="share_transfer",
        object_id=transfer.id,
        after={
            "sender": new.sender.key,
            "recipient": new.recipient.key,
            "percent": new.percent,
            "sender_share": own.percent,
            "successor": new.successor.key if new.successor else None,
            "policy": policy.transfer_mode,
            "policy_percent": policy.transfer_percent,
            "approvals": [{"role": r, "party": p.key} for r, p in rows],
            "expires_at": transfer.expires_at,
        },
        company_id=company.id,
    )
    return transfer


def party_key_or_none(user: UserAccount | None, company: Company | None) -> str | None:
    if user is None and company is None:
        return None
    return party_key(user, company)


def _close(
    ctx: CommandContext, transfer: ShareTransfer, state: str, now: datetime, reason: str = ""
) -> None:
    transfer.state = state
    transfer.decided_at = now
    transfer.save(update_fields=["state", "decided_at"])
    ctx.audit(
        f"ownership.transfer_{state}",
        object_type="share_transfer",
        object_id=transfer.id,
        reason=reason,
        company_id=transfer.company_id,
    )


def _locked_transfer(transfer_id: UUID) -> ShareTransfer:
    found = ShareTransfer.objects.filter(pk=transfer_id).first()
    if found is None:
        raise errors.not_found()
    _lock_company(found.company_id)
    transfer = (
        ShareTransfer.objects.select_for_update(of=("self",))
        .select_related(
            "sender_user",
            "sender_company",
            "recipient_user",
            "recipient_company",
            "successor_user",
            "successor_company",
        )
        .get(pk=transfer_id)
    )
    now = timezone.now()
    state = effective_state(transfer, now)
    if state == ShareTransfer.State.EXPIRED:
        raise errors.business_rule("TRANSFER_EXPIRED")
    if state != ShareTransfer.State.PENDING:
        raise errors.business_rule("TRANSFER_NOT_PENDING")
    return transfer


def sender_of(t: ShareTransfer) -> Party:
    return Party(user=t.sender_user, company=t.sender_company)


def recipient_of(t: ShareTransfer) -> Party:
    return Party(user=t.recipient_user, company=t.recipient_company)


def successor_of(t: ShareTransfer) -> Party | None:
    if t.successor_user is None and t.successor_company is None:
        return None
    return Party(user=t.successor_user, company=t.successor_company)


def cancel_transfer(ctx: CommandContext, transfer_id: UUID, user_id: UUID) -> ShareTransfer:
    """The sending side cancels while the request is pending (§ 2.5.1)."""
    transfer = _locked_transfer(transfer_id)
    if not can_act_for(user_id, sender_of(transfer)):
        raise errors.not_found()
    _close(ctx, transfer, ShareTransfer.State.CANCELLED, timezone.now())
    return transfer


def _sync_coowners(transfer: ShareTransfer, now: datetime) -> None:
    """Co-owners who joined while the request waits are asked too (PROPOSAL)."""
    if transfer.policy_mode not in (Mode.ALL, Mode.PERCENT):
        return
    asked = {
        party_key(a.party_user, a.party_company)
        for a in transfer.approvals.filter(role=Role.COOWNER).select_related(
            "party_user", "party_company"
        )
    }
    for party in owners_of(transfer.company_id):
        if party.key != sender_of(transfer).key and party.key not in asked:
            ShareTransferApproval.objects.create(
                company_id=transfer.company_id,
                transfer=transfer,
                role=Role.COOWNER,
                created_at=now,
                **party.fields("party"),
            )


def _coowners_satisfied(transfer: ShareTransfer) -> bool:
    """§ 3.4.2 by the policy of the request. In the percent mode the sender's share counts as
    approved by starting the transfer and the shares are those of now (PROPOSAL)."""
    if transfer.policy_mode not in (Mode.ALL, Mode.PERCENT):
        return True
    shares = {party_of_share(s).key: s.percent for s in current_shares(transfer.company_id)}
    decided = {
        party_key(a.party_user, a.party_company): a.state
        for a in transfer.approvals.filter(role=Role.COOWNER).select_related(
            "party_user", "party_company"
        )
    }
    others = [k for k in shares if k != sender_of(transfer).key]
    if transfer.policy_mode == Mode.ALL:
        return all(decided.get(k) == ShareTransferApproval.State.APPROVED for k in others)
    approved = shares.get(sender_of(transfer).key, Decimal(0)) + sum(
        (shares[k] for k in others if decided.get(k) == ShareTransferApproval.State.APPROVED),
        Decimal(0),
    )
    return approved >= (transfer.policy_percent or FULL)


def _ready(transfer: ShareTransfer) -> bool:
    required = transfer.approvals.exclude(role=Role.COOWNER)
    if required.exclude(state=ShareTransferApproval.State.APPROVED).exists():
        return False
    return _coowners_satisfied(transfer)


def decide_transfer(
    ctx: CommandContext, user: UserAccount, approval_id: UUID, *, approve: bool
) -> ShareTransfer:
    """A confirmation or a refusal; the last needed confirmation moves the share."""
    approval = ShareTransferApproval.objects.filter(pk=approval_id).first()
    if approval is None:
        raise errors.not_found()
    transfer = _locked_transfer(approval.transfer_id)
    approval = (
        ShareTransferApproval.objects.select_for_update(of=("self",))
        .select_related("party_user", "party_company")
        .get(pk=approval_id)
    )
    party = Party(user=approval.party_user, company=approval.party_company)
    if not can_act_for(user.id, party):
        raise errors.not_found()
    if approval.state != ShareTransferApproval.State.PENDING:
        raise errors.business_rule("TRANSFER_ALREADY_DECIDED")
    if approve:
        # § 2.3.1: within 72 hours after a recovery through the support the person neither gives
        # nor takes ownership or the highest powers (PROPOSAL: every confirmation).
        accounts.require_not_recently_recovered(user.id)
    if approve and approval.role == Role.RECIPIENT and not user.has_full_name:
        # The owners see the first and last name of a new owner (§ 3.3.6).
        raise errors.business_rule("NAME_REQUIRED")
    now = timezone.now()
    approval.state = (
        ShareTransferApproval.State.APPROVED if approve else ShareTransferApproval.State.DECLINED
    )
    approval.decided_by_user_id = user.id
    approval.decided_at = now
    approval.save(update_fields=["state", "decided_by_user_id", "decided_at"])
    ctx.audit(
        f"ownership.transfer_{approval.state}",
        object_type="share_transfer",
        object_id=transfer.id,
        after={"role": approval.role, "party": party.key},
        company_id=transfer.company_id,
    )
    if not approve:
        _close(ctx, transfer, ShareTransfer.State.DECLINED, now, reason=approval.role)
        return transfer
    _sync_coowners(transfer, now)
    if _ready(transfer):
        complete_transfer(ctx, transfer, now)
    return transfer


def _set_share(
    company_id: UUID, party: Party, current: OwnershipShare | None, percent: Decimal, now: datetime
) -> None:
    if current is not None:
        current.valid_to = now
        current.save(update_fields=["valid_to"])
    if percent > 0:
        OwnershipShare.objects.create(
            company_id=company_id, percent=percent, valid_from=now, **party.fields("owner")
        )


def complete_transfer(ctx: CommandContext, transfer: ShareTransfer, now: datetime) -> None:
    """One operation: the sender's share goes down, the recipient's appears or grows; the
    dedicated positions and the top authority follow (§ 3.4.3, § 3.4.4, § 3.4.6). Everything is
    checked again on fresh data first."""
    company_id = transfer.company_id
    sender, recipient, successor = (
        sender_of(transfer),
        recipient_of(transfer),
        successor_of(transfer),
    )
    own = share_of(company_id, sender)
    if own is None or own.percent < transfer.percent:
        raise errors.business_rule(
            "TRANSFER_EXCEEDS_SHARE", share=str(own.percent if own else Decimal(0))
        )
    check_no_cycle(company_id, recipient)
    whole = own.percent == transfer.percent
    _validate_successor(company_id, sender, recipient, successor, whole)
    if successor is not None and successor.company is not None:
        seat = current_seat(company_id, successor)
        holder = seat.holder if seat is not None else None
        if holder is None or holder.state != Employment.State.ACTIVE:
            # § 3.4.6: no top authority to a company with a vacant representative position.
            raise errors.business_rule("SUCCESSOR_REPRESENTATIVE_REQUIRED")

    before = {party_of_share(s).key: str(s.percent) for s in current_shares(company_id)}
    received = share_of(company_id, recipient)
    _set_share(company_id, sender, own, own.percent - transfer.percent, now)
    _set_share(
        company_id,
        recipient,
        received,
        (received.percent if received else Decimal(0)) + transfer.percent,
        now,
    )
    check_sum(company_id)

    recipient_seat = current_seat(company_id, recipient)
    if recipient_seat is None:
        raise errors.business_rule("OWNER_SEAT_MISSING")
    _activate_seat(ctx, recipient_seat, recipient, now)

    old_top = top_seat(company_id)
    sender_seat = current_seat(company_id, sender)
    if whole and sender_seat is not None:
        _end_seat(ctx, sender_seat, now)
    if successor is not None and old_top is not None:
        _pass_top_authority(ctx, old_top, successor, now)

    transfer.state = ShareTransfer.State.COMPLETED
    transfer.decided_at = now
    transfer.save(update_fields=["state", "decided_at"])
    ctx.audit(
        "ownership.transfer_completed",
        object_type="share_transfer",
        object_id=transfer.id,
        before={"shares": before},
        after={
            "shares": {party_of_share(s).key: str(s.percent) for s in current_shares(company_id)},
            "sender": sender.key,
            "recipient": recipient.key,
            "percent": transfer.percent,
            "successor": successor.key if successor else None,
        },
        company_id=company_id,
    )


def _pass_top_authority(
    ctx: CommandContext, old_top: OwnerSeat, successor: Party, now: datetime
) -> None:
    """§ 3.4.6: the top authority passes to the successor's dedicated position; the former
    director loses the former full access (the record of the creator stays)."""
    seat = current_seat(old_top.company_id, successor)
    if seat is None or seat.holder is None:
        raise errors.business_rule("SUCCESSOR_REPRESENTATIVE_REQUIRED")
    holder = (
        Employment.objects.select_for_update(of=("self",))
        .select_related("position")
        .get(pk=seat.holder.pk)
    )
    if holder.state != Employment.State.ACTIVE:
        raise errors.business_rule("SUCCESSOR_REPRESENTATIVE_REQUIRED")
    old_position = Position.objects.select_for_update(of=("self",)).get(pk=old_top.position_id)
    old_position.is_top_authority = False
    old_position.save(update_fields=["is_top_authority"])
    if old_top.holder_id is not None:
        former = Employment.objects.select_for_update(of=("self",)).get(pk=old_top.holder_id)
        if former.pk != holder.pk:
            _terminate(ctx, former, "top_authority_passed")
    position = Position.objects.select_for_update(of=("self",)).get(pk=seat.position_id)
    position.is_top_authority = True
    position.save(update_fields=["is_top_authority"])
    if holder.position_id != position.id:
        # One position per employment at this stage: the successor's card takes his dedicated
        # position, which now carries the top authority (TODO(spec): several positions).
        ctx.audit(
            "employment.position_changed",
            object_type="employment",
            object_id=holder.id,
            before={"position": str(holder.position_id)},
            after={"position": str(position.id)},
            company_id=holder.company_id,
        )
        holder.position = position
        holder.version += 1
        holder.save(update_fields=["position", "version"])
    # The new top manager is at the top of the structure; whoever was left without a responsible
    # is put under him (§ 5.2.1).
    orgstructure.reattach_orphans(ctx, old_top.company_id)
    ctx.audit(
        "ownership.top_authority_passed",
        object_type="owner_seat",
        object_id=seat.id,
        before={"seat": str(old_top.id), "position": str(old_position.id)},
        after={"successor": successor.key, "position": str(position.id), "holder": str(holder.id)},
        company_id=old_top.company_id,
    )


# --- Policy (§ 3.4.2, § 3.6.2) --------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyChange:
    transfer_mode: str
    transfer_percent: Decimal | None
    state_mode: str
    state_percent: Decimal | None


def set_policy(
    ctx: CommandContext, company_id: UUID, change: PolicyChange, expected_version: int
) -> OwnershipPolicy:
    _lock_company(company_id)
    found = (
        OwnershipPolicy.objects.select_for_update(of=("self",))
        .filter(company_id=company_id)
        .first()
    )
    if (found.version if found else 0) != expected_version:
        raise errors.version_conflict()
    policy = found or OwnershipPolicy(company_id=company_id, version=0)
    before = policy_payload(policy)
    policy.transfer_mode = change.transfer_mode
    policy.transfer_percent = change.transfer_percent
    policy.state_mode = change.state_mode
    policy.state_percent = change.state_percent
    policy.updated_by_user_id = ctx.actor.user_id
    policy.updated_at = timezone.now()
    policy.version += 1
    policy.save()
    ctx.audit(
        "ownership.policy_changed",
        object_type="ownership_policy",
        object_id=company_id,
        before=before,
        after=policy_payload(policy),
    )
    return policy


def policy_payload(policy: OwnershipPolicy) -> dict[str, Any]:
    return {
        "transfer_mode": policy.transfer_mode,
        "transfer_percent": str(policy.transfer_percent) if policy.transfer_percent else None,
        "state_mode": policy.state_mode,
        "state_percent": str(policy.state_percent) if policy.state_percent else None,
        "version": policy.version,
    }


# --- Representative of an owner company (§ 3.4.1) -------------------------------------------------


def set_limit(
    ctx: CommandContext, company_id: UUID, seat_id: UUID, specs: list[access.RightSpec]
) -> OwnerSeat:
    """The top authority of the company sets the limit of an owner company's representative.
    Rights of the current representative beyond a narrowed limit are revoked (PROPOSAL)."""
    seat = (
        OwnerSeat.objects.select_for_update(of=("self",))
        .filter(company_id=company_id, pk=seat_id, owner_company__isnull=False)
        .exclude(state=OwnerSeat.State.ENDED)
        .first()
    )
    if seat is None:
        raise errors.not_found()
    access.validate_rights(company_id, specs)
    before = seat.rights_limit
    seat.rights_limit = [s.as_dict() for s in specs]
    seat.version += 1
    seat.save(update_fields=["rights_limit", "version"])
    revoked: list[str] = []
    if seat.holder_id is not None:
        for assignment in access.seat_assignments(seat.holder_id, seat.id):
            if not access.within_limit([access.spec_of(assignment)], specs):
                access.revoke([assignment], ctx.actor.user_id)
                revoked.append(str(assignment.id))
    ctx.audit(
        "ownership.seat_limit_changed",
        object_type="owner_seat",
        object_id=seat.id,
        before={"limit": before},
        after={"limit": seat.rights_limit, "revoked": revoked},
    )
    return seat


def held_seat(owner_company_id: UUID, seat_id: UUID) -> OwnerSeat:
    seat = (
        OwnerSeat.objects.select_for_update(of=("self",))
        .select_related("company", "position", "holder")
        .filter(pk=seat_id, owner_company_id=owner_company_id, state=OwnerSeat.State.ACTIVE)
        .first()
    )
    if seat is None:
        raise errors.not_found()
    return seat


def assign_representative(
    ctx: CommandContext,
    owner_company_id: UUID,
    seat_id: UUID,
    target: UserAccount,
    specs: list[access.RightSpec],
) -> OwnerSeat:
    """The owner company appoints a c4rsys user by ID, its employee or not, with rights within
    the limit, fully or restricted (§ 3.4.1). The person accepts the invitation in the personal
    area (PROPOSAL: acceptance like an employee's, § 5.1.7); the former representative leaves
    when the new one accepts. Appointing the current representative again redefines his rights.
    """
    seat = held_seat(owner_company_id, seat_id)
    limit = access.parse_rights(seat.rights_limit)
    access.validate_rights(seat.company_id, specs)
    if not access.within_limit(specs, limit):
        raise errors.business_rule("REPRESENTATIVE_RIGHTS_EXCEED_LIMIT", field="rights")
    now = timezone.now()
    holder = seat.holder
    if holder is not None and holder.user_id == target.id:
        access.revoke(access.seat_assignments(holder.id, seat.id), ctx.actor.user_id)
        access.assign(holder, specs, ctx.actor.user_id, owner_seat_id=seat.id)
        ctx.audit(
            "ownership.representative_rights_set",
            object_type="owner_seat",
            object_id=seat.id,
            after={"holder": str(holder.id), "rights": [s.as_dict() for s in specs]},
        )
        ctx.audit(
            "employee.rights_changed",
            object_type="employment",
            object_id=holder.id,
            after={"rights": [s.as_dict() for s in specs], "owner_seat": str(seat.id)},
            company_id=seat.company_id,
        )
        return seat
    if Employment.objects.filter(
        company_id=seat.company_id,
        user=target,
        state__in=(Employment.State.ACTIVE, Employment.State.BLOCKED),
    ).exists():
        # One current employment per person and company at this stage (TODO(spec)).
        raise errors.business_rule("EMPLOYEE_ALREADY_EXISTS", field="target")
    _cancel_representative_invitations(ctx, seat)
    card = Employment.objects.create(
        company_id=seat.company_id,
        position=seat.position,
        state=Employment.State.INVITED,
        created_at=now,
    )
    orgstructure.attach_new(ctx, card)
    invitation = Invitation.objects.create(
        company_id=seat.company_id,
        employment=card,
        target_user_id=target.id,
        offered={
            "position_id": str(seat.position_id),
            "rights": [s.as_dict() for s in specs],
            "owner_seat": str(seat.id),
        },
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
        expires_at=now + TRANSFER_TTL,
    )
    ctx.audit(
        "ownership.representative_invited",
        object_type="owner_seat",
        object_id=seat.id,
        after={"target": target.public_id, "rights": [s.as_dict() for s in specs]},
    )
    ctx.audit(
        "invitation.sent",
        object_type="invitation",
        object_id=invitation.id,
        after={
            "employment": str(card.id),
            "target_user": target.public_id,
            **invitation.offered,
            "expires_at": invitation.expires_at,
        },
        company_id=seat.company_id,
    )
    return seat


def on_representative_accepted(
    ctx: CommandContext, invitation: Invitation, card: Employment
) -> None:
    """Called by the acceptance of the invitation: the person becomes the representative."""
    seat = (
        OwnerSeat.objects.select_for_update(of=("self",))
        .select_related("holder")
        .filter(pk=invitation.offered["owner_seat"], state=OwnerSeat.State.ACTIVE)
        .first()
    )
    if seat is None or seat.position_id != card.position_id:
        raise errors.business_rule("INVITATION_NOT_PENDING")
    specs = access.parse_rights(invitation.offered.get("rights", []))
    if not access.within_limit(specs, access.parse_rights(seat.rights_limit)):
        raise errors.business_rule("REPRESENTATIVE_RIGHTS_EXCEED_LIMIT")
    _release_holder(ctx, seat, "representative_replaced")
    before = str(seat.holder_id) if seat.holder_id else None
    seat.holder = card
    seat.version += 1
    seat.save(update_fields=["holder", "version"])
    access.assign(card, specs, invitation.created_by_user_id, owner_seat_id=seat.id)
    ctx.audit(
        "ownership.representative_assigned",
        object_type="owner_seat",
        object_id=seat.id,
        before={"holder": before},
        after={"holder": str(card.id), "rights": invitation.offered.get("rights", [])},
        company_id=seat.company_id,
    )


def remove_representative(ctx: CommandContext, owner_company_id: UUID, seat_id: UUID) -> OwnerSeat:
    """The position stays vacant without a term (§ 3.4.1)."""
    seat = held_seat(owner_company_id, seat_id)
    if seat.position.is_top_authority:
        # § 3.4.6: the top authority does not stay with a vacant representative position.
        raise errors.business_rule("REPRESENTATIVE_IS_TOP_AUTHORITY")
    _cancel_representative_invitations(ctx, seat)
    before = str(seat.holder_id) if seat.holder_id else None
    _release_holder(ctx, seat, "representative_removed")
    seat.holder = None
    seat.version += 1
    seat.save(update_fields=["holder", "version"])
    ctx.audit(
        "ownership.representative_removed",
        object_type="owner_seat",
        object_id=seat.id,
        before={"holder": before},
    )
    return seat


# --- Reading -------------------------------------------------------------------------------------


def seat_payload(seat: OwnerSeat) -> dict[str, Any]:
    holder = seat.holder
    holder_user = holder.user if holder is not None else None
    pending = (
        Invitation.objects.filter(
            company_id=seat.company_id,
            state=Invitation.State.PENDING,
            offered__owner_seat=str(seat.id),
        )
        .order_by("-created_at")
        .first()
    )
    pending_user = (
        accounts.get_user(pending.target_user_id)
        if pending is not None and pending.target_user_id is not None
        else None
    )
    return {
        "id": str(seat.id),
        "state": seat.state,
        "position_id": str(seat.position_id),
        "is_top_authority": seat.position.is_top_authority,
        "limit": seat.rights_limit,
        "holder": (
            {
                "employment_id": str(holder.id),
                "state": holder.state,
                "public_id": holder_user.public_id if holder_user else None,
                "first_name": holder_user.first_name if holder_user else "",
                "last_name": holder_user.last_name if holder_user else "",
                "rights": [
                    access.spec_of(a).as_dict() for a in access.seat_assignments(holder.id, seat.id)
                ],
            }
            if holder is not None and holder.state != Employment.State.TERMINATED
            else None
        ),
        "pending_invitation": (
            {
                "id": str(pending.id),
                "target": search_card(Party(user=pending_user, company=None))
                if pending_user
                else None,
                "rights": pending.offered.get("rights", []),
                "expires_at": pending.expires_at.isoformat(),
            }
            if pending is not None
            else None
        ),
        "version": seat.version,
    }


def transfer_payload(transfer: ShareTransfer, *, revealed: bool) -> dict[str, Any]:
    """`revealed` — the recipient is already an owner, so the owners see the minimal data of
    § 3.3.6; before that only the search card (§ 2.2.4, § 3.1.8)."""
    recipient = recipient_of(transfer)
    successor = successor_of(transfer)
    sender = sender_of(transfer)
    approvals = [
        {
            "id": str(a.id),
            "role": a.role,
            "party": party_key(a.party_user, a.party_company),
            "state": a.state,
            "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        }
        for a in transfer.approvals.select_related("party_user", "party_company").order_by(
            "created_at", "id"
        )
    ]
    return {
        "id": str(transfer.id),
        "company": {
            "public_id": transfer.company.public_id,
            "display_name": transfer.company.display_name,
        },
        "state": effective_state(transfer, timezone.now()),
        "sender": owner_card(sender),
        "recipient": owner_card(recipient) if revealed else search_card(recipient),
        "percent": str(transfer.percent),
        "successor": (
            None
            if successor is None
            else owner_card(successor)
            if successor.key != recipient.key or revealed
            else search_card(successor)
        ),
        "policy_mode": transfer.policy_mode,
        "policy_percent": str(transfer.policy_percent) if transfer.policy_percent else None,
        "approvals": approvals,
        "created_at": transfer.created_at.isoformat(),
        "expires_at": transfer.expires_at.isoformat(),
        "decided_at": transfer.decided_at.isoformat() if transfer.decided_at else None,
    }


def history(company_id: UUID) -> list[dict[str, Any]]:
    """Every share row with its period (§ 3.3.4): the history of ownership."""
    rows = (
        OwnershipShare.objects.filter(company_id=company_id)
        .select_related("owner_user", "owner_company")
        .order_by("valid_from", "id")
    )
    return [
        {
            "owner": owner_card(party_of_share(s)),
            "percent": str(s.percent),
            "valid_from": s.valid_from.isoformat(),
            "valid_to": s.valid_to.isoformat() if s.valid_to else None,
        }
        for s in rows
    ]


def transfers_of(company_id: UUID) -> QuerySet[ShareTransfer]:
    return transfers_of_query().filter(company_id=company_id).order_by("-created_at")


def _full_rights_companies(user: UserAccount) -> list[UUID]:
    return [
        e.company_id
        for e in Employment.objects.select_related("position").filter(
            user=user, state=Employment.State.ACTIVE
        )
        if access.has_full_rights(e)
    ]


def transfers_of_user(user: UserAccount) -> list[ShareTransfer]:
    """Pending transfers the user sends himself or for a company where he has full rights."""
    now = timezone.now()
    return list(
        transfers_of_query()
        .filter(
            Q(sender_user=user) | Q(sender_company_id__in=_full_rights_companies(user)),
            state=ShareTransfer.State.PENDING,
            expires_at__gt=now,
        )
        .order_by("-created_at")
    )


def transfers_of_query() -> QuerySet[ShareTransfer]:
    return ShareTransfer.objects.select_related(
        "company",
        "sender_user",
        "sender_company",
        "recipient_user",
        "recipient_company",
        "successor_user",
        "successor_company",
    )


def pending_approvals_for(user: UserAccount) -> list[ShareTransferApproval]:
    """Confirmations the user may give: his own, and those of companies where he has full
    rights."""
    companies = _full_rights_companies(user)
    now = timezone.now()
    found = (
        ShareTransferApproval.objects.filter(
            Q(party_user=user) | Q(party_company_id__in=companies),
            state=ShareTransferApproval.State.PENDING,
            transfer__state=ShareTransfer.State.PENDING,
            transfer__expires_at__gt=now,
        )
        .select_related("transfer", "transfer__company", "party_user", "party_company")
        .order_by("created_at")
    )
    return list(found)
