"""Server errors to Sentry without personal data (audit item 21)."""

from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
import sentry_sdk
from django.test import Client
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from c4rsys.platform import monitoring

pytestmark = pytest.mark.django_db


class Captured(Transport):
    events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            event = item.get_event()
            if event is not None:
                self.events.append(dict(event))


@pytest.fixture
def sentry() -> Iterator[list[dict[str, Any]]]:
    Captured.events = []
    with mock.patch("sentry_sdk.init", wraps=sentry_sdk.init) as init:
        monitoring.init("https://key@o0.ingest.sentry.io/1", environment="test", release="abc")
        options = init.call_args.kwargs
    sentry_sdk.init(**{**options, "transport": Captured})
    yield Captured.events
    sentry_sdk.init()  # off again: no DSN


def test_options_keep_personal_data_out() -> None:
    with mock.patch("sentry_sdk.init") as init:
        monitoring.init("https://key@o0.ingest.sentry.io/1", environment="test", release="")
    options = init.call_args.kwargs
    collected = options["data_collection"]
    assert collected["user_info"] is False and collected["http_bodies"] == []
    assert collected["stack_frame_variables"] is False
    assert (
        all(collected[k]["mode"] == "off" for k in ("cookies", "url_query_params"))
        and collected["http_headers"]["request"]["mode"] == "off"
    )
    assert options["max_request_body_size"] == "never"
    assert options["traces_sample_rate"] == 0
    assert options["release"] is None


def test_unhandled_error_of_a_request_is_sent_without_personal_data(
    client: Client, sentry: list[dict[str, Any]]
) -> None:
    # A broken view: the URL of a signing link with its secret token.
    with mock.patch("c4rsys.documents.signing.by_token", side_effect=RuntimeError("signing broke")):
        response = client.get(
            "/api/sign/secret-token-123?x=1",
            HTTP_COOKIE="c4r_session=cookie-value-77",
            HTTP_USER_AGENT="Phone",
        )
    assert response.status_code == 500
    sentry_sdk.flush()
    errors = [e for e in sentry if e.get("exception")]
    assert errors, sentry
    text = str(errors)
    assert "signing broke" in text
    for secret in ("secret-token-123", "cookie-value-77", "Phone", "x=1"):
        assert secret not in text
    assert errors[0]["transaction"] == "/api/sign/[cut]"
    assert errors[0]["release"] == "abc"


def test_scrub_cuts_secret_paths() -> None:
    event = {
        "request": {"url": "https://t/api/recovery/password/tok", "data": {"email": "a@b.c"}},
        "transaction": "/api/sign/tok/decide",
        "breadcrumbs": {
            "values": [{"message": "GET /api/sign/tok", "data": {"url": "/api/sign/t"}}]
        },
    }
    out = monitoring.scrub(event, {})
    assert out is not None
    assert out["request"] == {"url": "https://t/api/recovery/password/[cut]"}
    assert out["transaction"] == "/api/sign/[cut]/decide"
    assert out["breadcrumbs"]["values"][0] == {
        "message": "GET /api/sign/[cut]",
        "data": {"url": "/api/sign/[cut]"},
    }
