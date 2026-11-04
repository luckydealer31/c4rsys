"""Structured logs (audit item 22): one JSON line per record, with the request ID and the version,
without the secret parts of links; one record per answered request."""

import json
import logging
import subprocess
import sys
from typing import Any

import pytest
from django.test import Client

from c4rsys.platform import logs

pytestmark = pytest.mark.django_db


def line(record: logging.LogRecord) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(logs.JsonFormatter().format(record))
    return result


def make(message: str, *args: Any, **extra: Any) -> logging.LogRecord:
    make_record = logging.getLogRecordFactory()
    record = make_record("c4rsys.test", logging.WARNING, __file__, 1, message, args, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_record_is_one_json_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("C4R_RELEASE", "abc1234")
    token = logs.current_request_id.set("r1")
    try:
        record = make("Счёт %s: «%s»", 5, "ok", kind="rent")
    finally:
        logs.current_request_id.reset(token)
    # Written after the request: the ID was fixed when the record was made.
    text = logs.JsonFormatter().format(record)
    assert "\n" not in text
    found = json.loads(text)
    assert found["time"].endswith("+00:00")
    assert found["message"] == "Счёт 5: «ok»"
    assert found["level"] == "WARNING"
    assert found["logger"] == "c4rsys.test"
    assert found["release"] == "abc1234"
    assert found["request_id"] == "r1"
    assert found["kind"] == "rent"


def test_error_carries_its_traceback_in_the_line() -> None:
    try:
        raise ValueError("broken")
    except ValueError:
        record = logging.getLogger("c4rsys.test").makeRecord(
            "c4rsys.test", logging.ERROR, __file__, 1, "Failed", (), sys.exc_info()
        )
    found = line(record)
    assert "ValueError: broken" in found["exception"]
    assert "request_id" not in found


def test_secret_parts_of_links_are_cut() -> None:
    found = line(make("Not Found: %s", "/api/sign/SECRET123/code", path="/api/sign/SECRET123"))
    assert "SECRET123" not in json.dumps(found)
    assert found["message"] == "Not Found: /api/sign/[cut]/code"


def requests_of(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "c4rsys.request"]


def test_answered_request_is_logged_with_its_id(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    response = Client().get("/api/sign/SECRET123?x=1")
    [record] = requests_of(caplog)
    found = line(record)
    assert found["request_id"] == response["X-Request-ID"]
    assert found["status"] == response.status_code
    assert found["method"] == "GET"
    assert found["route"] == "api/sign/<str:token>"
    assert found["path"] == "/api/sign/[cut]"
    assert isinstance(found["duration_ms"], int)
    assert "SECRET123" not in json.dumps(found)
    assert "x=1" not in json.dumps(found)
    # Django's own record of the 404 (django.request) carries the request object; it is left out.
    [django] = [line(r) for r in caplog.records if r.name == "django.request"]
    assert django["request_id"] == response["X-Request-ID"]
    assert "SECRET123" not in json.dumps(django)
    assert "x=1" not in json.dumps(django)


def test_records_during_a_request_carry_its_id(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    response = Client().get("/api/no-such-thing")
    ids = {line(r).get("request_id") for r in caplog.records}
    assert ids == {response["X-Request-ID"]}
    assert logs.current_request_id.get() == ""


def test_successful_health_check_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="c4rsys.request")
    assert Client().get("/api/health").status_code == 200
    assert requests_of(caplog) == []


GUNICORN = """
import logging, logging.config
from gunicorn.glogging import CONFIG_DEFAULTS
from c4rsys.platform import logs
logging.config.dictConfig({**CONFIG_DEFAULTS, **logs.gunicorn_config()})
logging.getLogger("gunicorn.error").info("Booting worker")
"""


def test_gunicorn_writes_json_lines() -> None:
    # In a process of its own: dictConfig would replace the handlers of the tests.
    done = subprocess.run(  # noqa: S603 - the code is the constant above
        [sys.executable, "-c", GUNICORN], capture_output=True, text=True, check=True
    )
    found = json.loads(done.stderr)
    assert found["logger"] == "gunicorn.error"
    assert found["message"] == "Booting worker"
