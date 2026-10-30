"""Registration, two-step login, sessions and confirmation of critical actions.

ТЗ гл. 2, § 24.4.2.

Login events, code requests, confirmation results and ended sessions are journaled (§ 24.4.2).
The journal entry is written in the same transaction as the change.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from c4rsys.accounts import otp, security
from c4rsys.accounts.models import AuthFlow, LoginContact, UserAccount, UserSession
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import AuditEvent
from c4rsys.platform.registry import allocate_public_id

# Technical security parameters of c4rsys (§ 2.4.4), accepted by the customer on 22.10.2026.
FLOW_TTL = timedelta(minutes=30)
PASSWORD_ATTEMPTS = 5

CRITICAL_ACTIONS = {
    "end_all_sessions",
    "change_personal_data",
    # Customer's decision of 24.10.2026: the cloud password is changed with the current
    # password and a code to the email.
    "change_password",
    "change_email",  # § 2.3.2
    "transfer_share",  # § 2.1.1, § 3.4.3: transfer of company ownership
    "deactivate_company",  # § 3.6.1
}


@dataclass(frozen=True)
class Client:
    ip: str | None
    user_agent: str


@dataclass(frozen=True)
class StartedFlow:
    token: str
    resend_after_seconds: int = int(otp.RESEND_INTERVAL.total_seconds())
    code_ttl_seconds: int = int(otp.CODE_TTL.total_seconds())

    def as_dict(self) -> dict[str, Any]:
        return {
            "flow": self.token,
            "resend_after_seconds": self.resend_after_seconds,
            "code_ttl_seconds": self.code_ttl_seconds,
        }


def journal(
    action: str,
    *,
    object_type: str,
    object_id: object,
    user: UserAccount | None = None,
    session: UserSession | None = None,
    result: str = "success",
    after: dict[str, Any] | None = None,
) -> None:
    """System journal entry (no company). Anonymous steps are attributed to process `auth`."""
    AuditEvent.objects.create(
        actor_user_id=user.id if user else None,
        system_process="" if user else "auth",
        session_id=session.id if session else None,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        result=result,
        after=after,
    )


def normalize_email(value: str) -> str:
    """Technical normalization (§ 26.1): trimmed and lower-cased; validated by format."""
    email = value.strip().lower()
    try:
        validate_email(email)
    except ValidationError as exc:
        raise errors.validation_error("email", code="EMAIL_INVALID") from exc
    return email


def _email_owner(email: str) -> UserAccount | None:
    contact = (
        LoginContact.objects.select_related("user")
        .filter(kind=LoginContact.Kind.EMAIL, value=email)
        .first()
    )
    return contact.user if contact else None


def _primary_email(user: UserAccount) -> str:
    contact = user.contacts.filter(kind=LoginContact.Kind.EMAIL).order_by("verified_at").first()
    if contact is None:
        raise RuntimeError(f"Account {user.public_id} has no confirmed email")
    return contact.value


def _new_flow(
    purpose: str,
    contact: str,
    client: Client,
    now: datetime,
    *,
    user: UserAccount | None = None,
    action: str = "",
) -> tuple[AuthFlow, str]:
    token, digest = security.new_token()
    flow = AuthFlow.objects.create(
        purpose=purpose,
        token_hash=digest,
        contact_value=contact,
        user=user,
        action=action,
        ip=client.ip,
        user_agent=client.user_agent,
        created_at=now,
        expires_at=now + FLOW_TTL,
    )
    return flow, token


def _locked_flow(token: str, purpose: str, now: datetime) -> AuthFlow:
    flow = (
        AuthFlow.objects.select_for_update()
        .filter(token_hash=security.token_hash(token), purpose=purpose, completed_at__isnull=True)
        .first()
    )
    if flow is None or flow.expires_at <= now:
        raise errors.business_rule("FLOW_EXPIRED")
    return flow


# --- Registration (§ 2.1.2–2.1.4) ---------------------------------------------------------


def start_registration(email_input: str, client: Client) -> StartedFlow:
    email = normalize_email(email_input)
    now = timezone.now()
    with transaction.atomic():
        flow, token = _new_flow(AuthFlow.Purpose.REGISTER, email, client, now)
        # The code is sent even if the address is taken: the mailbox owner learns it only after
        # entering the code, a stranger learns nothing (§ 24.4.2).
        otp.issue_code(flow, now, deliver=True, ip=client.ip)
        journal(
            "auth.code_requested",
            object_type="auth_flow",
            object_id=flow.id,
            after={"purpose": flow.purpose},
        )
    return StartedFlow(token)


def resend_code(token: str, purpose: str, client: Client) -> StartedFlow:
    now = timezone.now()
    with transaction.atomic():
        flow = _locked_flow(token, purpose, now)
        if flow.code_verified_at is not None:
            raise errors.business_rule("CODE_ALREADY_CONFIRMED")
        deliver = purpose != AuthFlow.Purpose.LOGIN or flow.user is not None
        otp.issue_code(flow, now, deliver=deliver, ip=client.ip)
        journal(
            "auth.code_requested",
            object_type="auth_flow",
            object_id=flow.id,
            user=flow.user,
            after={"purpose": flow.purpose},
        )
    return StartedFlow(token)


def _verify(token: str, purpose: str, code: str) -> AuthFlow:
    now = timezone.now()
    with transaction.atomic():
        flow = _locked_flow(token, purpose, now)
        if flow.code_verified_at is not None:
            return flow
        outcome = otp.check_code(flow, code, now)
        if outcome.result == otp.CodeCheck.OK:
            flow.code_verified_at = now
            flow.save(update_fields=["code_verified_at"])
        journal(
            "auth.code_checked",
            object_type="auth_flow",
            object_id=flow.id,
            user=flow.user,
            result=outcome.result.name.lower(),
        )
    if outcome.result != otp.CodeCheck.OK:
        raise outcome.error()
    return flow


def verify_registration_code(token: str, code: str) -> None:
    flow = _verify(token, AuthFlow.Purpose.REGISTER, code)
    if _email_owner(flow.contact_value) is not None:
        raise errors.business_rule("EMAIL_ALREADY_REGISTERED", field="email")


def _check_password_strength(password: str) -> None:
    try:
        validate_password(password)
    except ValidationError as exc:
        raise errors.validation_error(
            "password", code="PASSWORD_TOO_WEAK", reasons=[e.code for e in exc.error_list]
        ) from exc


@dataclass(frozen=True)
class SignedIn:
    user: UserAccount
    session: UserSession
    session_token: str
    backup_code: str | None = None


def complete_registration(token: str, password: str, client: Client) -> SignedIn:
    _check_password_strength(password)
    now = timezone.now()
    with transaction.atomic():
        flow = _locked_flow(token, AuthFlow.Purpose.REGISTER, now)
        if flow.code_verified_at is None:
            raise errors.business_rule("CODE_NOT_CONFIRMED")
        if _email_owner(flow.contact_value) is not None:
            raise errors.business_rule("EMAIL_ALREADY_REGISTERED", field="email")

        backup_code = security.generate_backup_code()
        user = UserAccount(
            password_hash=make_password(password),
            backup_code_hash=make_password(backup_code),
        )
        user.public_id = allocate_public_id("USR", entity_type="user_account", entity_id=user.id)
        user.save(force_insert=True)
        try:
            with transaction.atomic():
                LoginContact.objects.create(
                    user=user,
                    kind=LoginContact.Kind.EMAIL,
                    value=flow.contact_value,
                    verified_at=flow.code_verified_at,
                )
        except IntegrityError as exc:
            raise errors.business_rule("EMAIL_ALREADY_REGISTERED", field="email") from exc

        flow.user = user
        flow.completed_at = now
        flow.save(update_fields=["user", "completed_at"])
        session, session_token = _create_session(user, client, now)
        journal(
            "account.registered",
            object_type="user_account",
            object_id=user.public_id,
            user=user,
            session=session,
        )
    return SignedIn(user, session, session_token, backup_code)


# --- Two-step login (§ 2.1.1) ---------------------------------------------------------------


def start_login(email_input: str, client: Client) -> StartedFlow:
    email = normalize_email(email_input)
    now = timezone.now()
    with transaction.atomic():
        owner = _email_owner(email)
        active = owner if owner and owner.state == UserAccount.State.ACTIVE else None
        flow, token = _new_flow(AuthFlow.Purpose.LOGIN, email, client, now, user=active)
        # Same response and same limits whether or not the account exists (§ 24.4.2).
        otp.issue_code(flow, now, deliver=active is not None, ip=client.ip)
        journal(
            "auth.code_requested",
            object_type="auth_flow",
            object_id=flow.id,
            user=active,
            after={"purpose": flow.purpose},
        )
    return StartedFlow(token)


def verify_login_code(token: str, code: str) -> None:
    _verify(token, AuthFlow.Purpose.LOGIN, code)


def complete_login(token: str, password: str, client: Client) -> SignedIn:
    now = timezone.now()
    with transaction.atomic():
        flow = _locked_flow(token, AuthFlow.Purpose.LOGIN, now)
        if flow.code_verified_at is None or flow.user is None:
            raise errors.business_rule("CODE_NOT_CONFIRMED")
        user = flow.user
        if check_password(password, user.password_hash) and user.state == UserAccount.State.ACTIVE:
            flow.completed_at = now
            flow.save(update_fields=["completed_at"])
            session, session_token = _create_session(user, client, now)
            journal(
                "auth.login",
                object_type="user_session",
                object_id=session.id,
                user=user,
                session=session,
            )
            return SignedIn(user, session, session_token)
        flow.password_attempts_left = max(flow.password_attempts_left - 1, 0)
        if flow.password_attempts_left == 0:
            flow.completed_at = now  # the flow is closed; a new code is required
        flow.save(update_fields=["password_attempts_left", "completed_at"])
        journal(
            "auth.login",
            object_type="auth_flow",
            object_id=flow.id,
            user=user,
            result="wrong_password",
        )
        attempts_left = flow.password_attempts_left
    # Raised after commit, so the failed attempt stays counted.
    raise errors.business_rule("LOGIN_FAILED", field="password", attempts_left=attempts_left)


# --- Sessions (§ 2.3.3, § 24.4.2) -----------------------------------------------------------


def _create_session(user: UserAccount, client: Client, now: datetime) -> tuple[UserSession, str]:
    token, digest = security.new_token()
    session = UserSession.objects.create(
        user=user,
        token_hash=digest,
        created_at=now,
        last_activity_at=now,
        ip=client.ip,
        user_agent=client.user_agent,
    )
    return session, token


def expiry_reason(session: UserSession, user: UserAccount, now: datetime) -> str | None:
    """Limits are the account's current settings, counted per session from its own login and last
    activity; changing the settings does not restart the count (§ 24.4.2)."""
    if now > session.created_at + timedelta(minutes=user.session_total_limit_min):
        return UserSession.EndReason.TOTAL_EXPIRED
    if now > session.last_activity_at + timedelta(minutes=user.session_idle_limit_min):
        return UserSession.EndReason.IDLE_EXPIRED
    return None


def end_session(session: UserSession, reason: str, now: datetime) -> None:
    with transaction.atomic():
        updated = UserSession.objects.filter(pk=session.pk, ended_at__isnull=True).update(
            ended_at=now, end_reason=reason
        )
        if updated:
            journal(
                "auth.session_ended",
                object_type="user_session",
                object_id=session.id,
                user=session.user,
                session=session,
                after={"reason": reason},
            )


def active_sessions(user: UserAccount, now: datetime) -> list[UserSession]:
    result = []
    for session in user.sessions.filter(ended_at__isnull=True).order_by("-last_activity_at"):
        reason = expiry_reason(session, user, now)
        if reason:
            end_session(session, reason, now)
        else:
            result.append(session)
    return result


# --- Confirmation of critical actions (§ 2.1.1, § 2.4.5) ------------------------------------


def start_confirmation(user: UserAccount, action: str, client: Client) -> StartedFlow:
    if action not in CRITICAL_ACTIONS:
        raise errors.validation_error("action")
    now = timezone.now()
    with transaction.atomic():
        flow, token = _new_flow(
            AuthFlow.Purpose.CRITICAL, _primary_email(user), client, now, user=user, action=action
        )
        otp.issue_code(flow, now, deliver=True, ip=client.ip)
        journal(
            "auth.code_requested",
            object_type="auth_flow",
            object_id=flow.id,
            user=user,
            after={"purpose": flow.purpose, "action": action},
        )
    return StartedFlow(token)


def confirm_critical(user: UserAccount, action: str, token: str, code: str) -> None:
    """Checks and consumes a confirmation code in its own transaction, so wrong attempts stay
    counted. Call it before running the confirmed command."""
    now = timezone.now()
    with transaction.atomic():
        flow = _locked_flow(token, AuthFlow.Purpose.CRITICAL, now)
        if flow.user_id != user.id or flow.action != action:
            raise errors.business_rule("FLOW_EXPIRED")
        outcome = otp.check_code(flow, code, now)
        if outcome.result == otp.CodeCheck.OK:
            flow.code_verified_at = now
            flow.completed_at = now
            flow.save(update_fields=["code_verified_at", "completed_at"])
        journal(
            "auth.code_checked",
            object_type="auth_flow",
            object_id=flow.id,
            user=user,
            result=outcome.result.name.lower(),
            after={"action": action},
        )
    if outcome.result != otp.CodeCheck.OK:
        raise outcome.error()


# --- Cloud password and login email (§ 2.3.2, decision of 24.10.2026) ----------------------


def change_password(
    ctx: CommandContext, user: UserAccount, session: UserSession, current: str, new: str
) -> int:
    """Run inside the command after the code was confirmed. A wrong current password refuses
    the change; without it the password is only restored with the backup code (§ 2.3.5).
    Other sessions end after the change (decision of 24.10.2026). Returns how many ended."""
    locked = UserAccount.objects.select_for_update().get(pk=user.pk)
    if not check_password(current, locked.password_hash):
        raise errors.business_rule("CURRENT_PASSWORD_WRONG", field="current_password")
    _check_password_strength(new)
    if check_password(new, locked.password_hash):
        raise errors.business_rule("PASSWORD_SAME_AS_CURRENT", field="new_password")
    locked.password_hash = make_password(new)
    locked.save(update_fields=["password_hash", "updated_at"])
    now = timezone.now()
    others = list(
        locked.sessions.filter(ended_at__isnull=True)
        .exclude(pk=session.pk)
        .values_list("id", flat=True)
    )
    UserSession.objects.filter(pk__in=others).update(
        ended_at=now, end_reason=UserSession.EndReason.PASSWORD_CHANGED
    )
    ctx.audit(
        "account.password_changed",
        object_type="user_account",
        object_id=locked.public_id,
        after={"ended_sessions": [str(i) for i in others]},
    )
    return len(others)


def start_email_change(user: UserAccount, new_email_input: str, client: Client) -> StartedFlow:
    """Sends a code to the new address. Called after the code to the current one was confirmed.
    Whether the address is taken is told only after its code is entered (§ 24.4.2)."""
    email = normalize_email(new_email_input)
    if email in confirmed_emails(user):
        raise errors.business_rule("EMAIL_SAME_AS_CURRENT", field="email")
    now = timezone.now()
    with transaction.atomic():
        flow, token = _new_flow(AuthFlow.Purpose.CHANGE_EMAIL, email, client, now, user=user)
        otp.issue_code(flow, now, deliver=True, ip=client.ip)
        journal(
            "auth.code_requested",
            object_type="auth_flow",
            object_id=flow.id,
            user=user,
            after={"purpose": flow.purpose},
        )
    return StartedFlow(token)


def verify_email_change(user: UserAccount, token: str, code: str) -> str:
    """Checks the code of the new address; returns the address."""
    flow = _verify(token, AuthFlow.Purpose.CHANGE_EMAIL, code)
    if flow.user_id != user.id:
        raise errors.business_rule("FLOW_EXPIRED")
    return flow.contact_value


def replace_email(ctx: CommandContext, user: UserAccount, token: str) -> str:
    """Run inside the command: the confirmed new address replaces the login email, all sessions
    end (§ 2.3.3)."""
    now = timezone.now()
    flow = _locked_flow(token, AuthFlow.Purpose.CHANGE_EMAIL, now)
    if flow.user_id != user.id or flow.code_verified_at is None:
        raise errors.business_rule("CODE_NOT_CONFIRMED")
    if _email_owner(flow.contact_value) is not None:
        raise errors.business_rule("EMAIL_ALREADY_REGISTERED", field="email")
    contact = (
        LoginContact.objects.select_for_update()
        .filter(user=user, kind=LoginContact.Kind.EMAIL)
        .order_by("verified_at")
        .first()
    )
    if contact is None:
        raise RuntimeError(f"Account {user.public_id} has no confirmed email")
    before = contact.value
    try:
        with transaction.atomic():
            contact.value = flow.contact_value
            contact.verified_at = flow.code_verified_at
            contact.save(update_fields=["value", "verified_at"])
    except IntegrityError as exc:
        raise errors.business_rule("EMAIL_ALREADY_REGISTERED", field="email") from exc
    flow.completed_at = now
    flow.save(update_fields=["completed_at"])
    ended = list(user.sessions.filter(ended_at__isnull=True).values_list("id", flat=True))
    UserSession.objects.filter(pk__in=ended).update(
        ended_at=now, end_reason=UserSession.EndReason.CONTACT_CHANGED
    )
    ctx.audit(
        "account.email_changed",
        object_type="user_account",
        object_id=user.public_id,
        before={"email": before},
        after={"email": contact.value, "ended_sessions": [str(i) for i in ended]},
    )
    return contact.value


# --- Lookups for other modules ----------------------------------------------------------------


def find_active_user(public_id: str) -> UserAccount | None:
    """Active account by its USR ID (§ 2.1.4)."""
    return UserAccount.objects.filter(public_id=public_id, state=UserAccount.State.ACTIVE).first()


def find_active_user_by_username(username: str) -> UserAccount | None:
    """Active account by its optional username, without regard to case (§ 2.1.4, § 2.2.1)."""
    if not username:
        return None
    return UserAccount.objects.filter(
        username__iexact=username, state=UserAccount.State.ACTIVE
    ).first()


def find_user_by_email(email: str) -> UserAccount | None:
    """Active account with this confirmed email."""
    owner = _email_owner(email)
    return owner if owner and owner.state == UserAccount.State.ACTIVE else None


def confirmed_emails(user: UserAccount) -> list[str]:
    return list(user.contacts.filter(kind=LoginContact.Kind.EMAIL).values_list("value", flat=True))


def confirmed_emails_of(user_id: UUID) -> list[str]:
    """Confirmed emails of a profile, for a one-time code of another module."""
    return list(
        LoginContact.objects.filter(user_id=user_id, kind=LoginContact.Kind.EMAIL)
        .order_by("verified_at")
        .values_list("value", flat=True)
    )


def find_user_by_phone(phone: str) -> UserAccount | None:
    """Active account with this confirmed phone (digits with an optional leading «+»)."""
    contact = (
        LoginContact.objects.select_related("user")
        .filter(kind=LoginContact.Kind.PHONE, value=phone)
        .first()
    )
    user = contact.user if contact else None
    return user if user and user.state == UserAccount.State.ACTIVE else None


def confirmed_phones(user: UserAccount) -> list[str]:
    return list(user.contacts.filter(kind=LoginContact.Kind.PHONE).values_list("value", flat=True))


def get_user(user_id: UUID) -> UserAccount | None:
    return UserAccount.objects.filter(pk=user_id).first()


def shareable_profile(user: UserAccount) -> dict[str, Any]:
    """The data a person may give a company that asks to link a card (§ 6.2.5, § 6.4.1): names,
    birth date, the first confirmed email, the contact phone and the identity document."""
    email = (
        user.contacts.filter(kind=LoginContact.Kind.EMAIL)
        .order_by("verified_at")
        .values_list("value", flat=True)
        .first()
    )
    return {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "middle_name": user.middle_name,
        "birth_date": user.birth_date.isoformat() if user.birth_date else None,
        "email": email or "",
        "phone": user.contact_phone,
        "document": {
            "type": user.document_type,
            "number": user.document_number,
            "issued_on": user.document_issued_on.isoformat() if user.document_issued_on else None,
            "expires_on": (
                user.document_expires_on.isoformat() if user.document_expires_on else None
            ),
        }
        if user.document_number
        else None,
    }


def names(user_ids: set[UUID]) -> dict[UUID, str]:
    """«First Last» of users, for the history shown by other modules."""
    return {
        user.id: f"{user.first_name} {user.last_name}".strip()
        for user in UserAccount.objects.filter(pk__in=user_ids).only(
            "id", "first_name", "last_name"
        )
    }
