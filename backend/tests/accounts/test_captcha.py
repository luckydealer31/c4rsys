"""Cloudflare Turnstile before a code goes to an address typed on an open form (customer
decision 02.11.2026)."""

import urllib.error
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
from django.test import Client, override_settings

from c4rsys.accounts.models import OtpSendLog
from tests.accounts.helpers import post

pytestmark = pytest.mark.django_db

KEYS = {"C4R_TURNSTILE_SITE_KEY": "site-key", "C4R_TURNSTILE_SECRET_KEY": "secret-key"}


@pytest.fixture
def turnstile() -> Iterator[mock.MagicMock]:
    with override_settings(**KEYS), mock.patch("c4rsys.accounts.captcha._verify") as verify:
        yield verify


def start(client: Client, path: str, **extra: Any) -> Any:
    return post(client, path, {"email": "owner@example.com", **extra})


def test_off_without_keys_sends_code(client: Client) -> None:
    assert start(client, "/api/auth/register/start").status_code == 200
    assert client.get("/api/site").json()["captcha_site_key"] is None


def test_site_gives_public_key_only(client: Client, turnstile: mock.MagicMock) -> None:
    body = client.get("/api/site").json()
    assert body["captcha_site_key"] == "site-key"
    assert "secret-key" not in str(body)


@pytest.mark.parametrize("path", ["/api/auth/register/start", "/api/auth/login/start"])
def test_missing_token_refused_before_any_code(
    client: Client, turnstile: mock.MagicMock, path: str
) -> None:
    response = start(client, path)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CAPTCHA_REQUIRED"
    turnstile.assert_not_called()
    assert not OtpSendLog.objects.exists()


def test_rejected_token_refused(client: Client, turnstile: mock.MagicMock) -> None:
    turnstile.return_value = {"success": False, "error-codes": ["invalid-input-response"]}
    response = start(client, "/api/auth/login/start", captcha="bad")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CAPTCHA_FAILED"
    assert not OtpSendLog.objects.exists()


def test_accepted_token_sends_code(client: Client, turnstile: mock.MagicMock) -> None:
    turnstile.return_value = {"success": True}
    response = start(client, "/api/auth/register/start", captcha="good")
    assert response.status_code == 200
    turnstile.assert_called_once_with("good")
    assert OtpSendLog.objects.count() == 1


def test_cloudflare_unreachable_refuses(client: Client, turnstile: mock.MagicMock) -> None:
    turnstile.side_effect = urllib.error.URLError("down")
    response = start(client, "/api/auth/register/start", captcha="good")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CAPTCHA_UNAVAILABLE"
    assert not OtpSendLog.objects.exists()


@override_settings(C4R_SUPPORT_RECOVERY_ENABLED=True)
def test_recovery_email_needs_token(client: Client, turnstile: mock.MagicMock) -> None:
    response = post(client, "/api/recovery/email/start", {"email": "new@example.com"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CAPTCHA_REQUIRED"
