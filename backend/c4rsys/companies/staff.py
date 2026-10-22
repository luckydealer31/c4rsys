"""Employees: invitation and its acceptance, blocking and restoring access.

ТЗ § 2.5.1–2.5.2, § 5.1.1–5.1.7, § 23.3.1, § 23.3.10.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.accounts import mail
from c4rsys.accounts import services as accounts
from c4rsys.accounts.models import UserAccount
from c4rsys.companies import access
from c4rsys.companies.models import Company, Employment, Invitation, Position
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.ids import normalize_public_id

# Always 7 days at stage 1; a term chosen by the company comes later (§ 2.5.1, decision 22.10.2026).
INVITATION_TTL = timedelta(days=7)

CURRENT_STATES = (Employment.State.ACTIVE, Employment.State.BLOCKED)


def effective_state(invitation: Invitation, now: datetime) -> str:
    """A pending invitation past its term is expired (§ 2.5.1)."""
    if invitation.state == Invitation.State.PENDING and invitation.expires_at <= now:
        return Invitation.State.EXPIRED
    return invitation.state


def employments_of(company_id: UUID) -> QuerySet[Employment]:
    return (
        Employment.objects.filter(company_id=company_id)
        .select_related("position", "user")
        .order_by("created_at")
    )


def get_employment(company_id: UUID, employment_id: UUID) -> Employment:
    employment = employments_of(company_id).filter(pk=employment_id).first()
    if employment is None:
        raise errors.not_found()
    return employment


# --- Invitation by the company (§ 5.1.3) -----------------------------------------------------


@dataclass(frozen=True)
class Target:
    user: UserAccount | None
    email: str


def resolve_target(value: str) -> Target:
    """A user ID or an email. A found profile gets the invitation inside c4rsys; an email without a
    profile gets a link to register (§ 5.1.3). The answer does not reveal which case applied."""
    public_id = normalize_public_id(value)
    if public_id is not None:
        user = accounts.find_active_user(public_id) if public_id.startswith("USR-") else None
        if user is None:
            raise errors.business_rule("INVITATION_TARGET_NOT_FOUND", field="target")
        return Target(user=user, email="")
    email = accounts.normalize_email(value)
    return Target(user=accounts.find_user_by_email(email), email=email)


@dataclass(frozen=True)
class PositionChoice:
    position_id: UUID | None = None
    name: str = ""


def _position(company: Company, choice: PositionChoice, now: datetime) -> Position:
    """An existing position of the handbook or a new one added for reuse (§ 5.1.5). Top authority
    and owner positions are not given by invitation (§ 3.1.4, § 3.3.4)."""
    if choice.position_id is not None:
        position = Position.objects.filter(company=company, pk=choice.position_id).first()
        if position is None:
            raise errors.validation_error("position_id", code="POSITION_UNKNOWN")
    else:
        position = Position.objects.filter(company=company, name__iexact=choice.name).first()
        if position is None:
            position = Position.objects.create(company=company, name=choice.name, created_at=now)
    if position.is_top_authority or position.is_owner_position:
        raise errors.business_rule("POSITION_NOT_ASSIGNABLE", field="position_id")
    return position


def _pending_for(company: Company, target: Target) -> Invitation | None:
    match = Q()
    if target.user is not None:
        match |= Q(target_user_id=target.user.id)
    if target.email:
        match |= Q(target_email=target.email)
    return (
        Invitation.objects.select_for_update()
        .filter(match, company=company, state=Invitation.State.PENDING)
        .select_related("employment")
        .first()
    )


def _is_current_employee(company_id: UUID, user: UserAccount) -> bool:
    return Employment.objects.filter(
        company_id=company_id, user=user, state__in=CURRENT_STATES
    ).exists()


def invite(
    ctx: CommandContext,
    company: Company,
    target: Target,
    choice: PositionChoice,
    rights: list[access.RightSpec],
) -> Invitation:
    """Creates the employee card and the invitation (§ 5.1.1). A repeat for the same target with the
    same conditions is a reminder that extends the term; changed conditions cancel the pending
    invitation and create a new one from the same card (§ 2.5.2)."""
    now = timezone.now()
    if target.user is not None and _is_current_employee(company.id, target.user):
        raise errors.business_rule("EMPLOYEE_ALREADY_EXISTS", field="target")
    position = _position(company, choice, now)
    offered = {"position_id": str(position.id), "rights": [r.as_dict() for r in rights]}

    pending = _pending_for(company, target)
    if pending is not None and effective_state(pending, now) == Invitation.State.EXPIRED:
        pending.state = Invitation.State.EXPIRED
        pending.save(update_fields=["state"])
    elif pending is not None and pending.offered == offered:
        pending.expires_at = now + INVITATION_TTL
        pending.save(update_fields=["expires_at"])
        ctx.audit(
            "invitation.reminded",
            object_type="invitation",
            object_id=pending.id,
            after={"expires_at": pending.expires_at},
        )
        _notify(target, company)
        return pending
    elif pending is not None:
        pending.state = Invitation.State.CANCELLED
        pending.decided_at = now
        pending.save(update_fields=["state", "decided_at"])
        ctx.audit(
            "invitation.cancelled",
            object_type="invitation",
            object_id=pending.id,
            reason="conditions_changed",
        )

    card = pending.employment if pending is not None else _reusable_card(company, target)
    if card is None:
        card = Employment.objects.create(
            company=company, position=position, state=Employment.State.INVITED, created_at=now
        )
        ctx.audit(
            "employment.created",
            object_type="employment",
            object_id=card.id,
            after={"position": str(position.id), "state": card.state},
        )
    elif card.position_id != position.id:
        card.position = position
        card.save(update_fields=["position"])

    invitation = Invitation.objects.create(
        company=company,
        employment=card,
        target_user_id=target.user.id if target.user else None,
        target_email=target.email,
        offered=offered,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
        expires_at=now + INVITATION_TTL,
    )
    ctx.audit(
        "invitation.sent",
        object_type="invitation",
        object_id=invitation.id,
        after={
            "employment": str(card.id),
            "target_user": target.user.public_id if target.user else None,
            "target_email": target.email,
            **offered,
            "expires_at": invitation.expires_at,
        },
    )
    _notify(target, company)
    return invitation


def _reusable_card(company: Company, target: Target) -> Employment | None:
    """After an expired or declined invitation a new one is sent from the same card (§ 5.1.1)."""
    match = Q()
    if target.user is not None:
        match |= Q(target_user_id=target.user.id)
    if target.email:
        match |= Q(target_email=target.email)
    last = (
        Invitation.objects.filter(
            match, company=company, employment__state=Employment.State.INVITED
        )
        .select_related("employment")
        .order_by("-created_at")
        .first()
    )
    return last.employment if last is not None else None


def _notify(target: Target, company: Company) -> None:
    if target.email:
        email, name = target.email, company.display_name
        transaction.on_commit(lambda: mail.send_invitation(email, name))


def cancel_invitation(ctx: CommandContext, company_id: UUID, invitation_id: UUID) -> Invitation:
    """The sender may cancel while the invitation is pending (§ 2.5.1)."""
    invitation = (
        Invitation.objects.select_for_update()
        .filter(company_id=company_id, pk=invitation_id)
        .first()
    )
    if invitation is None:
        raise errors.not_found()
    if effective_state(invitation, timezone.now()) != Invitation.State.PENDING:
        raise errors.business_rule("INVITATION_NOT_PENDING")
    invitation.state = Invitation.State.CANCELLED
    invitation.decided_at = timezone.now()
    invitation.save(update_fields=["state", "decided_at"])
    ctx.audit("invitation.cancelled", object_type="invitation", object_id=invitation.id)
    return invitation


# --- Decision of the invited user (§ 5.1.3, § 5.1.7) -----------------------------------------


def invitations_for(user: UserAccount) -> QuerySet[Invitation]:
    """Invitations addressed to the user by ID or by any of their confirmed emails."""
    return Invitation.objects.filter(
        Q(target_user_id=user.id) | Q(target_email__in=accounts.confirmed_emails(user))
    ).select_related("company", "employment__position")


def _locked_for(user: UserAccount, invitation_id: UUID) -> Invitation:
    invitation = invitations_for(user).select_for_update(of=("self",)).filter(pk=invitation_id)
    found = invitation.first()
    if found is None:
        raise errors.not_found()
    state = effective_state(found, timezone.now())
    if state == Invitation.State.EXPIRED:
        raise errors.business_rule("INVITATION_EXPIRED")
    if state != Invitation.State.PENDING:
        raise errors.business_rule("INVITATION_NOT_PENDING")
    return found


@dataclass(frozen=True)
class SharedData:
    """Optional data the user gives the company; first and last name are always given (§ 5.1.7)."""

    middle_name: bool = False
    email: bool = False


def accept(
    ctx: CommandContext, user: UserAccount, invitation_id: UUID, shared: SharedData
) -> Invitation:
    """Links the existing card to the profile; the offered rights take effect (§ 5.1.1). Accepting
    is explicit consent to give the shown data to the company (§ 5.1.7)."""
    if not user.has_full_name:
        raise errors.business_rule("NAME_REQUIRED")
    invitation = _locked_for(user, invitation_id)
    if _is_current_employee(invitation.company_id, user):
        raise errors.business_rule("EMPLOYEE_ALREADY_EXISTS")
    now = timezone.now()
    card = Employment.objects.select_for_update().get(pk=invitation.employment_id)
    card.user = user
    card.state = Employment.State.ACTIVE
    card.started_at = now
    card.shares_middle_name = shared.middle_name
    card.shares_email = shared.email
    card.version += 1
    card.save(
        update_fields=[
            "user",
            "state",
            "started_at",
            "shares_middle_name",
            "shares_email",
            "version",
        ]
    )
    rights = access.parse_rights(invitation.offered.get("rights", []))
    access.assign(card, rights, invitation.created_by_user_id)
    invitation.state = Invitation.State.ACCEPTED
    invitation.decided_at = now
    invitation.save(update_fields=["state", "decided_at"])
    ctx.audit(
        "invitation.accepted",
        object_type="invitation",
        object_id=invitation.id,
        after={
            "employment": str(card.id),
            "user": user.public_id,
            "shared": {"middle_name": shared.middle_name, "email": shared.email},
        },
        company_id=invitation.company_id,
    )
    return invitation


def decline(ctx: CommandContext, user: UserAccount, invitation_id: UUID) -> Invitation:
    """The card stays for accounting; a new invitation may be sent from it (§ 5.1.1)."""
    invitation = _locked_for(user, invitation_id)
    invitation.state = Invitation.State.DECLINED
    invitation.decided_at = timezone.now()
    invitation.save(update_fields=["state", "decided_at"])
    ctx.audit(
        "invitation.declined",
        object_type="invitation",
        object_id=invitation.id,
        company_id=invitation.company_id,
    )
    return invitation


# --- Blocking and restoring access (§ 23.3.8, § 23.3.10) -------------------------------------


def set_blocked(
    ctx: CommandContext, company_id: UUID, employment_id: UUID, *, blocked: bool
) -> Employment:
    """A blocked employee loses access at once; restoring is a separate action. The own access and
    that of the director with top authority are not changed this way."""
    employment = (
        Employment.objects.select_for_update()
        .select_related("position")
        .filter(company_id=company_id, pk=employment_id)
        .first()
    )
    if employment is None:
        raise errors.not_found()
    if employment.id == ctx.actor.employment_id:
        raise errors.business_rule("SELF_ACCESS_CHANGE_FORBIDDEN")
    if employment.position.is_top_authority:
        raise errors.business_rule("EMPLOYEE_IS_TOP_AUTHORITY")
    source, target = (
        (Employment.State.ACTIVE, Employment.State.BLOCKED)
        if blocked
        else (Employment.State.BLOCKED, Employment.State.ACTIVE)
    )
    if employment.state != source:
        raise errors.business_rule("EMPLOYMENT_STATE_INVALID", state=employment.state)
    employment.state = target
    employment.version += 1
    employment.save(update_fields=["state", "version"])
    ctx.audit(
        "employee.access_blocked" if blocked else "employee.access_restored",
        object_type="employment",
        object_id=employment.id,
        before={"state": source},
        after={"state": target},
    )
    return employment


def invitation_payload(invitation: Invitation, now: datetime) -> dict[str, Any]:
    position = invitation.employment.position
    return {
        "id": str(invitation.id),
        "state": effective_state(invitation, now),
        "employment_id": str(invitation.employment_id),
        "position": {"id": str(position.id), "code": position.code, "name": position.name},
        "rights": invitation.offered.get("rights", []),
        "created_at": invitation.created_at.isoformat(),
        "expires_at": invitation.expires_at.isoformat(),
    }
