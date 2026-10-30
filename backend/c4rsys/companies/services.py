"""Company creation, active company context and the company profile.

ТЗ § 3.1, § 3.3.4–3.3.5, § 3.3.8, § 22.1.4, § 22.1.7.
"""

import zoneinfo
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.accounts.models import UserAccount
from c4rsys.companies.models import (
    ActivityContext,
    Company,
    Employment,
    OwnerSeat,
    OwnershipShare,
    Position,
)
from c4rsys.files import services as files
from c4rsys.files.models import StoredFile
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.platform.ids import normalize_public_id
from c4rsys.platform.models import AuditEvent, Currency
from c4rsys.platform.registry import allocate_public_id

FULL_SHARE = Decimal("100")
_TIMEZONES = frozenset(zoneinfo.available_timezones())

# Modules that set up their data of a new company in the creating transaction (finance: starting
# income and expense articles, § 17.2.8).
_on_created: list[Callable[[UUID], None]] = []


def register_created(handler: Callable[[UUID], None]) -> None:
    _on_created.append(handler)


def validate_timezone(value: str, field: str = "timezone") -> str:
    """IANA time zone name (§ 4.2.2)."""
    if value not in _TIMEZONES:
        raise errors.validation_error(field, code="TIMEZONE_INVALID")
    return value


def validate_currency(value: str, field: str = "main_currency") -> Currency:
    currency = Currency.objects.filter(code=value.upper()).first()
    if currency is None:
        raise errors.validation_error(field, code="CURRENCY_INVALID")
    return currency


@dataclass(frozen=True)
class CompanyContext:
    """The active company of a request and the user's employment in it."""

    company: Company
    employment: Employment

    @property
    def is_top_authority(self) -> bool:
        return self.employment.position.is_top_authority


# --- Creation (§ 3.1.1, § 3.1.4, § 3.3.4–3.3.5) -------------------------------------------


@dataclass(frozen=True)
class NewCompany:
    display_name: str
    timezone: str = ""
    main_currency: str = ""
    logo: UUID | None = None


def create_company(ctx: CommandContext, user: UserAccount, data: NewCompany) -> CompanyContext:
    """One transaction: company with CMP ID, 100% share, director position, employment.

    The creator needs first and last name (§ 2.1.3) and gets no invitation to themself (§ 3.3.5).
    """
    if not user.has_full_name:
        raise errors.business_rule("NAME_REQUIRED")
    currency = validate_currency(data.main_currency) if data.main_currency else None
    now = timezone.now()

    if data.logo is not None:
        files.usable(data.logo, purpose=StoredFile.Purpose.COMPANY_LOGO, user_id=user.id)
    company = Company(
        display_name=data.display_name,
        logo_id=data.logo,
        timezone=data.timezone,
        main_currency=currency,
        created_by=user,
        created_at=now,
    )
    company.public_id = allocate_public_id("CMP", entity_type="company", entity_id=company.id)
    company.save(force_insert=True)
    if data.logo is not None:
        files.attach_to_company(data.logo, company.id)

    share = OwnershipShare.objects.create(
        company=company, owner_user=user, percent=FULL_SHARE, valid_from=now
    )
    position = Position.objects.create(
        company=company,
        code=Position.Code.DIRECTOR,
        is_top_authority=True,
        is_owner_position=True,
        created_at=now,
    )
    employment = Employment.objects.create(
        company=company,
        user=user,
        position=position,
        state=Employment.State.ACTIVE,
        started_at=now,
        created_at=now,
    )

    # The creator's director position is also his dedicated owner position (§ 3.3.4, § 3.4.4).
    seat = OwnerSeat.objects.create(
        company=company,
        position=position,
        owner_user=user,
        holder=employment,
        state=OwnerSeat.State.ACTIVE,
        created_at=now,
        activated_at=now,
    )

    for handler in _on_created:
        handler(company.id)

    # The fact of creation stays in the history regardless of later changes (§ 3.1.4).
    ctx.audit(
        "company.created",
        object_type="company",
        object_id=company.public_id,
        after=profile_snapshot(company),
        company_id=company.id,
    )
    ctx.audit(
        "company.share_granted",
        object_type="ownership_share",
        object_id=share.id,
        after={"owner_user": user.public_id, "percent": share.percent, "seat": str(seat.id)},
        company_id=company.id,
    )
    ctx.audit(
        "employment.created",
        object_type="employment",
        object_id=employment.id,
        after={
            "user": user.public_id,
            "position": str(position.id),
            "position_code": position.code,
            "is_top_authority": True,
            "state": employment.state,
        },
        company_id=company.id,
    )
    return CompanyContext(company=company, employment=employment)


# --- Companies of the user (§ 2.1.8, § 3.3.7) -----------------------------------------------


def companies_of(user: UserAccount) -> QuerySet[Company]:
    """Companies the user is related to as owner, co-owner or through current employment."""
    current_share = Q(shares__owner_user=user, shares__valid_to__isnull=True)
    employed = Q(employments__user=user, employments__state=Employment.State.ACTIVE)
    return Company.objects.filter(current_share | employed).distinct().order_by("display_name")


# --- Active company (§ 3.3.8, docs/stage1/STAGE1_COMMANDS_API.md) ---------------------------


def resolve_company(user: UserAccount, public_id: str, session_id: UUID | None) -> CompanyContext:
    """The company from the address is the active one only with current employment of the user.

    A passed ID gives no authority. A missing company and a company without the user's employment
    get the same NOT_FOUND (§ 2.8.1); the attempt is journaled as a security event. A blocked
    employee loses access at once (§ 23.3.8).
    """
    normalized = normalize_public_id(public_id)
    employment = None
    if normalized is not None:
        employment = (
            Employment.objects.select_related("company", "position")
            .filter(
                company__public_id=normalized,
                user=user,
                state__in=[Employment.State.ACTIVE, Employment.State.BLOCKED],
            )
            .first()
        )
    if employment is not None and employment.state == Employment.State.BLOCKED:
        raise errors.ApiError("EMPLOYMENT_BLOCKED", status=403)
    if employment is None:
        AuditEvent.objects.create(
            actor_user_id=user.id,
            session_id=session_id,
            action="security.company_access_denied",
            object_type="company",
            object_id=public_id[:64],
            result="denied",
        )
        raise errors.not_found()
    return CompanyContext(company=employment.company, employment=employment)


def company_actor(user: UserAccount, context: CompanyContext, session_id: UUID | None) -> Actor:
    return Actor(
        user_id=user.id,
        company_id=context.company.id,
        employment_id=context.employment.id,
        session_id=session_id,
    )


# --- Profile (§ 3.1.6, § 22.1.4, § 22.1.7) --------------------------------------------------


# Optional text fields of the company profile (§ 3.1.5) with their maximum length.
REQUISITES = {
    "legal_name": 300,
    "country": 2,
    "legal_form": 100,
    "legal_address": 500,
    "actual_address": 500,
    "phone": 40,
    "email": 254,
    "website": 300,
    "description": 2000,
}


# Additional fields of the editable profile that may be made public (§ 3.1.8). The minimal search
# card — display name, logo, country and company ID — is always shown and has no switch.
PUBLIC_FIELDS = (
    "legal_name",
    "legal_form",
    "identifiers",
    "legal_address",
    "actual_address",
    "phone",
    "email",
    "website",
    "description",
)


def public_data(company: Company) -> dict[str, Any]:
    """Values of the fields the company made public, the empty ones left out (§ 3.5.3)."""
    data = {}
    for key in company.public_fields:
        if key in PUBLIC_FIELDS:
            value = getattr(company, key)
            if value:
                data[key] = value
    return data


def set_public_fields(
    ctx: CommandContext, company_id: UUID, fields: list[str], expected_version: int
) -> Company:
    """The caller checks the authority of § 3.1.8. The switch does not reach documents, owners or
    the internal history."""
    company = Company.objects.select_for_update(no_key=True).get(pk=company_id)
    if company.version != expected_version:
        raise errors.version_conflict()
    wanted = [key for key in PUBLIC_FIELDS if key in set(fields)]
    before = list(company.public_fields)
    if wanted == before:
        return company
    company.public_fields = wanted
    company.version += 1
    company.save(update_fields=["public_fields", "version", "updated_at"])
    ctx.audit(
        "company.public_fields_changed",
        object_type="company",
        object_id=company.public_id,
        before={"public_fields": before},
        after={"public_fields": wanted},
    )
    return company


PROFILE_HISTORY_ACTIONS = (
    "company.created",
    "company.profile_changed",
    "company.public_fields_changed",
)


def profile_history(company: Company) -> QuerySet[AuditEvent]:
    """Changes of the profile: initiator, time, changed fields with old and new values
    (§ 3.5.2), newest first."""
    return AuditEvent.objects.filter(
        company_id=company.id,
        object_type="company",
        object_id=company.public_id,
        action__in=PROFILE_HISTORY_ACTIONS,
    ).order_by("-occurred_at", "-id")


def profile_snapshot(company: Company) -> dict[str, Any]:
    return {
        "display_name": company.display_name,
        "logo": str(company.logo_id) if company.logo_id else None,
        "timezone": company.timezone,
        "main_currency": company.main_currency_id,
        **{key: getattr(company, key) for key in REQUISITES},
        "identifiers": company.identifiers,
    }


def find_company(public_id: str) -> Company | None:
    """A company by its CMP ID, for a request to link with its profile (§ 6.2.5)."""
    return Company.objects.filter(public_id=public_id).first()


def get_company(company_id: UUID) -> Company | None:
    return Company.objects.filter(pk=company_id).first()


def shareable_profile(company: Company) -> dict[str, Any]:
    """The data a company may give another company that asks to link a card (§ 6.2.5, § 6.4.1)."""
    return {
        "display_name": company.display_name,
        "legal_name": company.legal_name,
        "country": company.country,
        "legal_form": company.legal_form,
        "identifiers": company.identifiers,
        "legal_address": company.legal_address,
        "actual_address": company.actual_address,
        "phone": company.phone,
        "email": company.email,
    }


def link_answerers(company_id: UUID) -> list[Employment]:
    """Who answers a request to link with the company's profile: the top-authority director and
    the employees with the right (§ 6.3.8). A share or a position gives nothing by itself."""
    from c4rsys.companies import access

    resolver = access.RightsResolver()
    found = []
    for e in Employment.objects.select_related("position").filter(
        company_id=company_id, state=Employment.State.ACTIVE, user__isnull=False
    ):
        actor = Actor(user_id=e.user_id or e.id, company_id=company_id, employment_id=e.id)
        if e.position.is_top_authority or resolver.has_permission(
            actor, "company.link.answer", whole_company=True
        ):
            found.append(e)
    return found


def setup_state(company: Company) -> dict[str, bool]:
    """Completion of the wizard sections handled here: profile needs the name and a confirmed time
    zone (§ 22.1.4), finance needs the main currency (§ 22.1.7)."""
    return {
        "profile": bool(company.display_name and company.timezone),
        "finance": company.main_currency_id is not None,
    }


def context_types(company: Company) -> list[str]:
    """Types of contexts present in the company's locations, deactivated ones included, so the
    history of a direction stays reachable. The interface shows sections by them (customer's
    decision of 24.10.2026)."""
    return sorted(
        set(ActivityContext.objects.filter(company=company).values_list("type", flat=True))
    )


SETUP_CHOICES = {"employees": ("alone",), "documents": ("none",)}


def set_setup_choice(
    ctx: CommandContext, company_id: UUID, section: str, value: str | None
) -> Company:
    """An explicit answer of a wizard section; None takes it back (§ 22.1.10–22.1.11)."""
    if section not in SETUP_CHOICES or (value is not None and value not in SETUP_CHOICES[section]):
        raise errors.validation_error("section")
    company = Company.objects.select_for_update(no_key=True).get(pk=company_id)
    choices = dict(company.setup_choices)
    before = choices.get(section)
    if value is None:
        choices.pop(section, None)
    else:
        choices[section] = value
    company.setup_choices = choices
    company.save(update_fields=["setup_choices", "updated_at"])
    ctx.audit(
        "company.setup_choice",
        object_type="company",
        object_id=company.public_id,
        before={section: before},
        after={section: value},
    )
    return company


def complete_setup(ctx: CommandContext, company_id: UUID) -> Company:
    """«Complete» of the setup wizard. The caller checks that a context is ready (§ 22.1.12)."""
    company = Company.objects.select_for_update(no_key=True).get(pk=company_id)
    if company.setup_completed_at is None:
        company.setup_completed_at = timezone.now()
        company.save(update_fields=["setup_completed_at", "updated_at"])
    ctx.audit(
        "company.setup_completed",
        object_type="company",
        object_id=company.public_id,
        after={"setup_completed_at": company.setup_completed_at},
    )
    return company


def update_profile(
    ctx: CommandContext, company_id: UUID, changes: dict[str, Any], expected_version: int
) -> Company:
    company = Company.objects.select_for_update(no_key=True).get(pk=company_id)
    if company.version != expected_version:
        raise errors.version_conflict()
    before = profile_snapshot(company)
    if "logo" in changes:
        logo = UUID(changes["logo"]) if changes["logo"] else None
        if logo is not None and logo != company.logo_id:
            files.usable(
                logo,
                purpose=StoredFile.Purpose.COMPANY_LOGO,
                user_id=ctx.actor.user_id,
                company_id=company.id,
            )
            files.attach_to_company(logo, company.id)
        company.logo_id = logo
    if "display_name" in changes:
        company.display_name = changes["display_name"]
    if "timezone" in changes:
        company.timezone = changes["timezone"]
    if "main_currency" in changes:
        company.main_currency = validate_currency(changes["main_currency"])
    for key in [*REQUISITES, "identifiers"]:
        if key in changes:
            setattr(company, key, changes[key])
    after = profile_snapshot(company)
    company.version += 1
    company.save(
        update_fields=[
            "display_name",
            "logo",
            "timezone",
            "main_currency",
            *REQUISITES,
            "identifiers",
            "version",
            "updated_at",
        ]
    )
    ctx.audit(
        "company.profile_changed",
        object_type="company",
        object_id=company.public_id,
        before={k: v for k, v in before.items() if before[k] != after[k]},
        after={k: v for k, v in after.items() if before[k] != after[k]},
    )
    return company
