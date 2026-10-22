from typing import Any
from unittest import mock

import pytest
from django.core import mail as outbox
from django.test import Client, override_settings

from c4rsys.accounts import mail
from tests.accounts.helpers import CODE, post

EMAIL_MODE = override_settings(
    C4R_MAIL_MODE="email",
    DEFAULT_FROM_EMAIL="c4rsys <no-reply@c4rsys.com>",
)


@EMAIL_MODE
@pytest.mark.django_db
def test_code_is_sent_by_email_after_commit(
    client: Client, django_capture_on_commit_callbacks: Any
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        started = post(client, "/api/auth/register/start", {"email": "new@example.com"})

    assert started.status_code == 200
    assert len(outbox.outbox) == 1
    message = outbox.outbox[0]
    assert message.to == ["new@example.com"]
    assert message.from_email == "c4rsys <no-reply@c4rsys.com>"
    assert CODE in message.body


@EMAIL_MODE
@pytest.mark.django_db
def test_failed_delivery_keeps_the_flow_and_logs_no_code(
    client: Client, django_capture_on_commit_callbacks: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        mock.patch("c4rsys.accounts.mail.send_mail", side_effect=OSError("provider down")),
        django_capture_on_commit_callbacks(execute=True),
    ):
        started = post(client, "/api/auth/register/start", {"email": "new@example.com"})

    assert started.status_code == 200
    assert "Mail delivery failed" in caplog.text
    assert CODE not in caplog.text
    assert "new@example.com" not in caplog.text


@override_settings(
    C4R_MAIL_MODE="email",
    C4R_PUBLIC_URL="https://test.c4rsys.com",
)
def test_invitation_links_to_the_interface() -> None:
    mail.send_invitation("staff@example.com", "Тест Аренда")

    assert len(outbox.outbox) == 1
    assert "Тест Аренда" in outbox.outbox[0].body
    assert "https://test.c4rsys.com" in outbox.outbox[0].body
