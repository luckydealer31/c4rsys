"""One-time codes and their limits.

ТЗ § 2.4.1: a code is valid 3 minutes, allows 5 attempts, may be re-requested after 60 seconds,
a new code annuls the previous one for the same action and contact.
ТЗ § 2.4.4: at most 5 sends per contact per hour and 10 per 24 hours; after the hourly limit
sending is blocked until the hour ends, after the daily limit for 24 hours from the last send.
The hour is counted as a sliding 60-minute window from the earliest send inside it.
Responses never reveal whether an account exists (§ 24.4.2).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from django.db import transaction

from c4rsys.accounts import mail, security
from c4rsys.accounts.models import AuthFlow, OtpCode, OtpSendLog
from c4rsys.platform.errors import ApiError

CODE_TTL = timedelta(minutes=3)
MAX_ATTEMPTS = 5
RESEND_INTERVAL = timedelta(seconds=60)
HOUR_WINDOW = timedelta(hours=1)
HOUR_LIMIT = 5
DAY_WINDOW = timedelta(hours=24)
DAY_LIMIT = 10


def rate_limited(retry_at: datetime) -> ApiError:
    return ApiError("OTP_RATE_LIMITED", status=429, params={"retry_at": retry_at.isoformat()})


def check_send_allowed(contact: str, purpose: str, now: datetime) -> None:
    sends = list(
        OtpSendLog.objects.filter(contact_value=contact, sent_at__gt=now - DAY_WINDOW)
        .order_by("sent_at")
        .values_list("sent_at", "purpose")
    )
    if len(sends) >= DAY_LIMIT:
        raise rate_limited(sends[-1][0] + DAY_WINDOW)
    last_hour = [sent_at for sent_at, _ in sends if sent_at > now - HOUR_WINDOW]
    if len(last_hour) >= HOUR_LIMIT:
        raise rate_limited(last_hour[0] + HOUR_WINDOW)
    same_purpose = [sent_at for sent_at, p in sends if p == purpose]
    if same_purpose and same_purpose[-1] > now - RESEND_INTERVAL:
        raise rate_limited(same_purpose[-1] + RESEND_INTERVAL)


def issue_code(flow: AuthFlow, now: datetime, *, deliver: bool, ip: str | None) -> None:
    """Create a new code for the flow. When `deliver` is false the code is stored but not sent:
    the caller must behave identically for a missing account (§ 24.4.2)."""
    check_send_allowed(flow.contact_value, flow.purpose, now)
    OtpCode.objects.filter(
        contact_value=flow.contact_value,
        purpose=flow.purpose,
        consumed_at__isnull=True,
        annulled_at__isnull=True,
    ).update(annulled_at=now)
    code = security.generate_code()
    OtpCode.objects.create(
        flow=flow,
        purpose=flow.purpose,
        contact_value=flow.contact_value,
        code_hash=security.code_hash(code),
        created_at=now,
        expires_at=now + CODE_TTL,
        attempts_left=MAX_ATTEMPTS,
    )
    OtpSendLog.objects.create(
        contact_value=flow.contact_value, purpose=flow.purpose, sent_at=now, ip=ip
    )
    if deliver:
        email, purpose = flow.contact_value, flow.purpose
        transaction.on_commit(lambda: mail.send_code(email, code, purpose))


def send_contact_code(contact: str, purpose: str, now: datetime, ip: str | None) -> str:
    """A code for a flow of another module (signing of a document): the same limits and
    delivery; the holder keeps the code's hash."""
    check_send_allowed(contact, purpose, now)
    code = security.generate_code()
    OtpSendLog.objects.create(contact_value=contact, purpose=purpose, sent_at=now, ip=ip)
    transaction.on_commit(lambda: mail.send_code(contact, code, purpose))
    return code


class CodeCheck(StrEnum):
    OK = "ok"
    INVALID = "CODE_INVALID"
    EXPIRED = "CODE_EXPIRED"
    EXHAUSTED = "CODE_ATTEMPTS_EXHAUSTED"


@dataclass(frozen=True)
class CodeOutcome:
    result: CodeCheck
    attempts_left: int = 0

    def error(self) -> ApiError:
        params = {"attempts_left": self.attempts_left} if self.result == CodeCheck.INVALID else {}
        return ApiError(self.result.value, status=422, field="code", params=params)


def check_code(flow: AuthFlow, code: str, now: datetime) -> CodeOutcome:
    """Must run inside a transaction with the flow row locked. Wrong attempts are recorded, so
    the caller commits first and raises the error afterwards."""
    current = (
        OtpCode.objects.select_for_update()
        .filter(flow=flow, consumed_at__isnull=True, annulled_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if current is None or current.attempts_left == 0:
        return CodeOutcome(CodeCheck.EXHAUSTED if current else CodeCheck.EXPIRED)
    if current.expires_at <= now:
        return CodeOutcome(CodeCheck.EXPIRED)
    if not security.code_matches(code.strip(), current.code_hash):
        current.attempts_left -= 1
        if current.attempts_left == 0:
            current.annulled_at = now
        current.save(update_fields=["attempts_left", "annulled_at"])
        if current.attempts_left == 0:
            return CodeOutcome(CodeCheck.EXHAUSTED)
        return CodeOutcome(CodeCheck.INVALID, current.attempts_left)
    current.consumed_at = now
    current.save(update_fields=["consumed_at"])
    return CodeOutcome(CodeCheck.OK)
