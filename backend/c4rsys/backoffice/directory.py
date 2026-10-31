"""Search and minimal view of users and companies (ТЗ § 24.1.2, § 24.4.3).

Contacts in the lists and cards are masked; the full values are shown only by a separate
permitted action with a reason, which is journaled (`reveal_*`). The closed data of a company is
reached only through a privileged session (§ 24.3.1).
"""

from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet

from c4rsys.accounts.models import LoginContact, UserAccount, UserSession
from c4rsys.backoffice.models import SystemBlock, SystemStaffMember
from c4rsys.companies import ownership
from c4rsys.companies.models import Company, Employment
from c4rsys.platform import errors
from c4rsys.platform.ids import normalize_public_id

LIMIT = 50


def mask_email(value: str) -> str:
    if not value:
        return ""
    name, _, domain = value.partition("@")
    return f"{name[:1]}***@{domain}" if domain else "***"


def mask_phone(value: str) -> str:
    digits = [c for c in value if c.isdigit()]
    if not digits:
        return ""
    return f"{'+' if value.startswith('+') else ''}***{''.join(digits[-2:])}"


def _name(user: UserAccount) -> str:
    return " ".join(x for x in (user.last_name, user.first_name) if x)


def _active_block(**target: Any) -> SystemBlock | None:
    return SystemBlock.objects.filter(ended_at__isnull=True, **target).first()


def block_payload(block: SystemBlock) -> dict[str, Any]:
    return {
        "id": str(block.id),
        "target_type": block.target_type,
        "company": {
            "public_id": block.company.public_id,
            "display_name": block.company.display_name,
        }
        if block.company
        else None,
        "user": {"public_id": block.user.public_id, "name": _name(block.user)}
        if block.user
        else None,
        "category": block.category,
        "comment": block.comment,
        "disclosed_reason": block.disclosed_reason,
        "started_at": block.started_at.isoformat(),
        "ends_at": block.ends_at.isoformat() if block.ends_at else None,
        "ended_at": block.ended_at.isoformat() if block.ended_at else None,
        "end_kind": block.end_kind or None,
        "end_reason": block.end_reason,
        "notice": block.notice,
        "notified_at": block.notified_at.isoformat() if block.notified_at else None,
    }


# --- Users ----------------------------------------------------------------------------------------


def find_user(public_id: str) -> UserAccount:
    normalized = normalize_public_id(public_id)
    found = UserAccount.objects.filter(public_id=normalized).first() if normalized else None
    if found is None:
        raise errors.not_found()
    return found


def search_users(text: str) -> QuerySet[UserAccount]:
    text = text.strip()
    if not text:
        return UserAccount.objects.none()
    match = Q(username__iexact=text.lstrip("@")) | Q(
        pk__in=LoginContact.objects.filter(value__iexact=text).values("user_id")
    )
    normalized = normalize_public_id(text)
    if normalized:
        match |= Q(public_id=normalized)
    for word in text.split()[:3]:
        if len(word) >= 2:
            match |= Q(first_name__icontains=word) | Q(last_name__icontains=word)
    return UserAccount.objects.filter(match).order_by("last_name", "first_name")[:LIMIT]


def _login_email(user: UserAccount) -> str:
    return (
        LoginContact.objects.filter(user=user, kind=LoginContact.Kind.EMAIL)
        .order_by("verified_at")
        .values_list("value", flat=True)
        .first()
        or ""
    )


def user_summary(user: UserAccount) -> dict[str, Any]:
    return {
        "public_id": user.public_id,
        "name": _name(user),
        "username": user.username or None,
        "state": user.state,
        # § 24.1.8: the ID of a merged account refers to the main one.
        "merged_into": user.merged_into.public_id
        if user.merged_into_id and user.merged_into
        else None,
        "created_at": user.created_at.isoformat(),
        "email_masked": mask_email(_login_email(user)),
        "blocked": _active_block(user=user) is not None,
        "is_staff": SystemStaffMember.objects.filter(
            user=user, state=SystemStaffMember.State.ACTIVE
        ).exists(),
    }


def user_card(user: UserAccount) -> dict[str, Any]:
    """Minimal information: no document data, no birth date, contacts masked."""
    employments = Employment.objects.select_related("company", "position").filter(
        user=user, state__in=(Employment.State.ACTIVE, Employment.State.BLOCKED)
    )
    return {
        **user_summary(user),
        "contacts_masked": {
            "login_emails": [
                mask_email(c.value)
                for c in LoginContact.objects.filter(user=user).order_by("verified_at")
                if c.kind == LoginContact.Kind.EMAIL
            ],
            "contact_email": mask_email(user.contact_email),
            "contact_phone": mask_phone(user.contact_phone),
        },
        "companies": [
            {
                "public_id": e.company.public_id,
                "display_name": e.company.display_name,
                "state": e.state,
                "top_authority": e.position.is_top_authority,
            }
            for e in employments
        ],
        "open_sessions": UserSession.objects.filter(user=user, ended_at__isnull=True).count(),
        "blocks": [
            block_payload(b) for b in SystemBlock.objects.filter(user=user).order_by("-started_at")
        ],
    }


def user_contacts(user: UserAccount) -> dict[str, Any]:
    return {
        "login_emails": [
            c.value
            for c in LoginContact.objects.filter(user=user).order_by("verified_at")
            if c.kind == LoginContact.Kind.EMAIL
        ],
        "login_phones": [
            c.value
            for c in LoginContact.objects.filter(user=user).order_by("verified_at")
            if c.kind == LoginContact.Kind.PHONE
        ],
        "contact_email": user.contact_email,
        "contact_phone": user.contact_phone,
    }


# --- Companies ------------------------------------------------------------------------------------


def find_company(public_id: str) -> Company:
    normalized = normalize_public_id(public_id)
    found = Company.objects.filter(public_id=normalized).first() if normalized else None
    if found is None:
        raise errors.not_found()
    return found


def search_companies(text: str) -> QuerySet[Company]:
    text = text.strip()
    if not text:
        return Company.objects.none()
    match = Q(display_name__icontains=text) | Q(legal_name__icontains=text)
    normalized = normalize_public_id(text)
    if normalized:
        match |= Q(public_id=normalized)
    return Company.objects.filter(match).order_by("display_name")[:LIMIT]


def company_summary(company: Company) -> dict[str, Any]:
    return {
        "public_id": company.public_id,
        "display_name": company.display_name,
        "country": company.country,
        "created_at": company.created_at.isoformat(),
        "deactivated_at": company.deactivated_at.isoformat() if company.deactivated_at else None,
        "blocked": _active_block(company=company) is not None,
    }


def company_card(company: Company) -> dict[str, Any]:
    owners = ownership.current_shares(company.id)
    return {
        **company_summary(company),
        "legal_name": company.legal_name,
        "setup_completed_at": company.setup_completed_at.isoformat()
        if company.setup_completed_at
        else None,
        "contacts_masked": {
            "email": mask_email(company.email),
            "phone": mask_phone(company.phone),
        },
        "employees": Employment.objects.filter(
            company=company, state=Employment.State.ACTIVE
        ).count(),
        "owners": len(owners),
        "blocks": [
            block_payload(b)
            for b in SystemBlock.objects.filter(company=company).order_by("-started_at")
        ],
    }


def company_contacts(company: Company) -> dict[str, Any]:
    return {"email": company.email, "phone": company.phone}


def owner_user_ids(company_id: UUID) -> list[UUID]:
    """Owners who are persons (§ 24.2.2: the owners get the notice of a company block).

    TODO(spec): who is told for an owner that is a company."""
    return [s.owner_user_id for s in ownership.current_shares(company_id) if s.owner_user_id]
