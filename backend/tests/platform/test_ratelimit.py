"""Limits of requests from one address to the open forms (audit items 11 and 37)."""

import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.test import Client, RequestFactory, override_settings
from django.utils import timezone

from c4rsys.platform import ratelimit
from c4rsys.platform.http import client_ip
from c4rsys.platform.models import IpRequestCount

pytestmark = pytest.mark.django_db


def start(client: Client, **extra: str) -> str | None:
    """The error code of a code request; the per-contact limits answer 429 too, so the code is
    what tells the address limit apart."""
    response = client.post(
        "/api/auth/login/start",
        {"email": f"{uuid.uuid4().hex}@example.com"},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
        **extra,  # type: ignore[arg-type]
    )
    return response.json().get("error", {}).get("code") if response.status_code >= 400 else None


@pytest.fixture
def small_limits() -> object:
    with mock.patch.dict(ratelimit.LIMITS, {"code_send": 2, "code_check": 2}):
        yield


def test_over_limit_refused_with_retry_time(client: Client, small_limits: object) -> None:
    assert start(client) is None
    assert start(client) is None
    response = client.post(
        "/api/auth/login/start", {"email": "a@example.com"}, content_type="application/json"
    )
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "IP_RATE_LIMITED"
    assert error["message_key"] == "errors.ip_rate_limited"
    assert error["params"]["retry_at"]
    assert int(response["Retry-After"]) > 0


def test_other_address_and_other_scope_count_apart(client: Client, small_limits: object) -> None:
    for _ in range(3):
        start(client)
    assert start(client) == "IP_RATE_LIMITED"
    assert start(client, REMOTE_ADDR="203.0.113.7") is None
    check = client.post("/api/auth/login/verify", {}, content_type="application/json")
    assert check.json()["error"]["code"] != "IP_RATE_LIMITED"


def test_only_listed_posts_count(client: Client, small_limits: object) -> None:
    for _ in range(5):
        client.get("/api/auth/csrf")
        client.post("/api/auth/logout", {}, content_type="application/json")
    assert not IpRequestCount.objects.exists()


@pytest.mark.parametrize(
    ("path", "scope"),
    [
        ("/api/auth/register/start", "code_send"),
        ("/api/auth/code/resend", "code_send"),
        ("/api/recovery/email/start", "code_send"),
        ("/api/sign/abc/code", "code_send"),
        ("/api/auth/login/complete", "code_check"),
        ("/api/recovery/password/abc", "code_check"),
        ("/api/sign/abc/decide", "code_check"),
        ("/api/me", None),
        ("/api/sign/abc", None),
    ],
)
def test_scopes(path: str, scope: str | None) -> None:
    assert ratelimit.scope_of(path) == scope


def test_old_windows_cleared_by_a_new_one() -> None:
    IpRequestCount.objects.create(
        ip="198.51.100.1",
        scope="code_send",
        window_start=timezone.now() - timedelta(days=2),
        count=3,
    )
    ratelimit.count("198.51.100.2", "code_send")
    assert list(IpRequestCount.objects.values_list("ip", flat=True)) == ["198.51.100.2"]


def test_forwarded_header_ignored_without_proxy() -> None:
    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="10.0.0.5")
    assert client_ip(request) == "10.0.0.5"


@override_settings(C4R_TRUST_PROXY=True)
def test_behind_proxy_last_forwarded_entry_is_the_visitor() -> None:
    factory = RequestFactory()
    forged = factory.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.9", REMOTE_ADDR="172.18.0.5")
    assert client_ip(forged) == "203.0.113.9"
    broken = factory.get("/", HTTP_X_FORWARDED_FOR="not-an-ip", REMOTE_ADDR="172.18.0.5")
    assert client_ip(broken) == "172.18.0.5"
