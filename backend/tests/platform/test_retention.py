"""Removal of old technical records (audit item 48; terms confirmed by the customer 04.11.2026):
code sends after 90 days, expired sign-in flows with their codes 30 days after expiry, stored
results of commands after 30 days."""

import io
import uuid
from datetime import datetime, timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from c4rsys.accounts import retention
from c4rsys.accounts.models import AuthFlow, OtpCode, OtpSendLog
from c4rsys.platform import commands
from c4rsys.platform.models import IdempotencyRecord

pytestmark = pytest.mark.django_db


def flow(expires_at: datetime) -> AuthFlow:
    item = AuthFlow.objects.create(
        purpose=AuthFlow.Purpose.LOGIN,
        token_hash=uuid.uuid4().hex,
        contact_value="a@example.com",
        created_at=expires_at - timedelta(minutes=15),
        expires_at=expires_at,
    )
    OtpCode.objects.create(
        flow=item,
        purpose="login",
        contact_value="a@example.com",
        code_hash="x",
        expires_at=expires_at,
    )
    return item


def test_code_sends_kept_for_90_days() -> None:
    now = timezone.now()
    old = OtpSendLog.objects.create(
        contact_value="a@example.com", purpose="login", sent_at=now - timedelta(days=91)
    )
    kept = OtpSendLog.objects.create(
        contact_value="a@example.com", purpose="login", sent_at=now - timedelta(days=89)
    )
    assert retention.remove_old_records(now)["sends"] == 1
    assert list(OtpSendLog.objects.values_list("pk", flat=True)) == [kept.pk]
    assert not OtpSendLog.objects.filter(pk=old.pk).exists()


def test_flows_and_their_codes_removed_30_days_after_expiry() -> None:
    now = timezone.now()
    old = flow(now - timedelta(days=31))
    kept = flow(now - timedelta(days=29))
    assert retention.remove_old_records(now)["flows"] == 1
    assert list(AuthFlow.objects.values_list("pk", flat=True)) == [kept.pk]
    assert not OtpCode.objects.filter(flow_id=old.pk).exists()
    assert OtpCode.objects.filter(flow_id=kept.pk).exists()


def record(created_at: datetime) -> IdempotencyRecord:
    return IdempotencyRecord.objects.create(
        user_id=uuid.uuid4(),
        key=uuid.uuid4().hex,
        command="x",
        request_hash="h",
        created_at=created_at,
    )


def test_stored_results_kept_for_30_days() -> None:
    now = timezone.now()
    record(now - timedelta(days=31))
    kept = record(now - timedelta(days=29))
    assert commands.remove_old_idempotency_records(now) == 1
    assert list(IdempotencyRecord.objects.values_list("pk", flat=True)) == [kept.pk]


def test_schedule_commands_run() -> None:
    out = io.StringIO()
    call_command("remove_old_auth_records", stdout=out)
    call_command("remove_old_idempotency_records", stdout=out)
    assert "Removed code sends: 0, flows: 0" in out.getvalue()
    assert "Removed stored results: 0" in out.getvalue()
