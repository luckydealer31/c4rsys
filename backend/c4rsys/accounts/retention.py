"""Removal of old technical records of sign-in (audit item 48). Terms CONFIRMED by the customer
04.11.2026. Technical records, like the counters of c4rsys.platform.ratelimit: removed without a
journal entry; sessions are not touched (their list is shown to the user, § 24.4.2)."""

from datetime import datetime, timedelta

from django.utils import timezone

from c4rsys.accounts.models import AuthFlow, OtpSendLog

# Code sends with the address and IP: 90 days. The limits of § 2.4.4 read the last day only.
KEEP_SEND_LOG = timedelta(days=90)
# Registration, login and confirmation flows with their codes: 30 days after the flow expired.
KEEP_FLOWS = timedelta(days=30)


def remove_old_records(now: datetime | None = None) -> dict[str, int]:
    moment = now or timezone.now()
    sends, _ = OtpSendLog.objects.filter(sent_at__lt=moment - KEEP_SEND_LOG).delete()
    # The codes of a flow go with it (on_delete=CASCADE).
    _, by_model = AuthFlow.objects.filter(expires_at__lt=moment - KEEP_FLOWS).delete()
    return {
        "sends": sends,
        "flows": by_model.get(AuthFlow._meta.label, 0),
    }
