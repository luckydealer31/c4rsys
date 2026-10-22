"""Personal account data (ТЗ § 2.1–2.4, § 24.4.2).

Secrets (password, backup code, one-time codes, session and flow tokens) are stored only as
hashes (§ 24.4.3).
"""

from django.db import models
from django.db.models import Q
from django.utils import timezone

from c4rsys.platform.ids import uuid7

DEFAULT_IDLE_MINUTES = 7 * 24 * 60  # 7 days of inactivity (§ 24.4.2)
DEFAULT_TOTAL_MINUTES = 30 * 24 * 60  # 30 days since login (§ 24.4.2)


class UserAccount(models.Model):
    class Theme(models.TextChoices):
        SYSTEM = "system"
        LIGHT = "light"
        DARK = "dark"

    class State(models.TextChoices):
        ACTIVE = "active"
        DEACTIVATED = "deactivated"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    public_id = models.CharField(max_length=40, unique=True)
    first_name = models.CharField(max_length=150, blank=True, default="")
    last_name = models.CharField(max_length=150, blank=True, default="")
    middle_name = models.CharField(max_length=150, blank=True, default="")
    password_hash = models.CharField(max_length=255)
    backup_code_hash = models.CharField(max_length=255, blank=True, default="")
    language = models.CharField(max_length=10, blank=True, default="")
    theme = models.CharField(max_length=10, choices=Theme.choices, default=Theme.SYSTEM)
    session_idle_limit_min = models.PositiveIntegerField(default=DEFAULT_IDLE_MINUTES)
    session_total_limit_min = models.PositiveIntegerField(default=DEFAULT_TOTAL_MINUTES)
    state = models.CharField(max_length=20, choices=State.choices, default=State.ACTIVE)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_account"
        constraints = [
            models.CheckConstraint(
                condition=Q(session_idle_limit_min__gte=1)
                & Q(session_idle_limit_min__lte=DEFAULT_IDLE_MINUTES),
                name="user_account_idle_limit_range",
            ),
            models.CheckConstraint(
                condition=Q(session_total_limit_min__gte=1)
                & Q(session_total_limit_min__lte=DEFAULT_TOTAL_MINUTES),
                name="user_account_total_limit_range",
            ),
        ]

    def __str__(self) -> str:
        return self.public_id

    @property
    def has_full_name(self) -> bool:
        """First and last name are required to create a company or accept employment (§ 2.1.3)."""
        return bool(self.first_name.strip() and self.last_name.strip())


class LoginContact(models.Model):
    """A confirmed email or phone. One confirmed contact belongs to one profile (§ 2.1.2)."""

    class Kind(models.TextChoices):
        EMAIL = "email"
        PHONE = "phone"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(UserAccount, on_delete=models.PROTECT, related_name="contacts")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    value = models.CharField(max_length=254)
    verified_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "login_contact"
        constraints = [
            models.UniqueConstraint(fields=["kind", "value"], name="login_contact_kind_value_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}"


class AuthFlow(models.Model):
    """One registration, login or critical-action confirmation in progress.

    The client holds the random flow token; the server stores only its hash.
    """

    class Purpose(models.TextChoices):
        REGISTER = "register"
        LOGIN = "login"
        CRITICAL = "critical"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    contact_value = models.CharField(max_length=254)
    user = models.ForeignKey(
        UserAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    action = models.CharField(max_length=60, blank=True, default="")
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    code_verified_at = models.DateTimeField(null=True, blank=True)
    password_attempts_left = models.PositiveSmallIntegerField(default=5)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "auth_flow"

    def __str__(self) -> str:
        return f"{self.purpose}:{self.id}"


class OtpCode(models.Model):
    """One-time code: 3 minutes, 5 attempts; a new code annuls the previous one (§ 2.4.1)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    flow = models.ForeignKey(AuthFlow, on_delete=models.CASCADE, related_name="codes")
    purpose = models.CharField(max_length=20)
    contact_value = models.CharField(max_length=254)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    attempts_left = models.PositiveSmallIntegerField(default=5)
    consumed_at = models.DateTimeField(null=True, blank=True)
    annulled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "otp_code"
        indexes = [
            models.Index(fields=["contact_value", "purpose"], name="otp_code_contact_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.purpose}:{self.contact_value}"


class OtpSendLog(models.Model):
    """Every code send attempt, for the per-contact limits of § 2.4.4."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    contact_value = models.CharField(max_length=254)
    purpose = models.CharField(max_length=20)
    sent_at = models.DateTimeField(default=timezone.now)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "otp_send_log"
        indexes = [
            models.Index(fields=["contact_value", "sent_at"], name="otp_send_log_contact_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.contact_value}@{self.sent_at:%Y-%m-%d %H:%M}"


class UserSession(models.Model):
    """Server-side session (A08). Limits are checked per session on every request (§ 24.4.2)."""

    class EndReason(models.TextChoices):
        LOGOUT = "logout"
        ENDED_BY_USER = "ended_by_user"
        ENDED_ALL = "ended_all"
        IDLE_EXPIRED = "idle_expired"
        TOTAL_EXPIRED = "total_expired"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(UserAccount, on_delete=models.PROTECT, related_name="sessions")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_activity_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=20, choices=EndReason.choices, blank=True, default="")
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "user_session"
        indexes = [
            models.Index(fields=["user", "ended_at"], name="user_session_active_idx"),
        ]

    def __str__(self) -> str:
        return f"session {self.id}"
