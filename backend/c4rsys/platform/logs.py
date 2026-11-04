"""Structured logs (audit item 22): with C4R_LOG_JSON on, every record of the backend, of gunicorn
and of the background jobs is one line of JSON, so the container logs can be searched by field.

A line holds the time in UTC, the level, the logger, the message, the version of the code
(C4R_RELEASE), the request ID of the request being served (the X-Request-ID header of its answer,
also in the error JSON) and the traceback of an error. Fields given with extra={...} are added.
The secret parts of links (signing, password setting) are cut out, as in the reports to Sentry.

Each answered request is one record of c4rsys.request: method, path, route, status and duration;
no query string, no visitor data. It replaces the access log of gunicorn, which wrote whole links.

The module is read by gunicorn before Django starts (gunicorn.conf.py): it imports no settings."""

import json
import logging
import os
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from c4rsys.platform.monitoring import cut

current_request_id: ContextVar[str] = ContextVar("current_request_id", default="")
# The key of the request ID in the WSGI environ, which is request.META (c4rsys.platform.middleware).
REQUEST_ID_ENVIRON_KEY = "c4rsys.request_id"
RECORD_ATTRIBUTE = "c4r_request_id"

# The attributes every LogRecord has; the others came with extra={...}.
STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    RECORD_ATTRIBUTE,
}


def request_started(environ: dict[str, Any], **kwargs: Any) -> None:
    """Signal request_started: the ID is given before any middleware, so the records Django writes
    around the middleware (django.request, «Not Found») carry it too."""
    request_id = uuid.uuid4().hex
    environ[REQUEST_ID_ENVIRON_KEY] = request_id
    current_request_id.set(request_id)


def request_finished(**kwargs: Any) -> None:
    current_request_id.set("")


# The ID is fixed when a record is made, not when it is written: a handler may write it later.
_make_record = logging.getLogRecordFactory()
if not getattr(_make_record, "c4rsys", False):

    def _record(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = _make_record(*args, **kwargs)
        setattr(record, RECORD_ATTRIBUTE, current_request_id.get())
        return record

    _record.c4rsys = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(_record)


PLAIN = (str, int, float, bool, type(None))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": cut(record.getMessage()),
        }
        release = os.environ.get("C4R_RELEASE", "")
        if release:
            line["release"] = release
        request_id = getattr(record, RECORD_ATTRIBUTE, "")
        if request_id:
            line["request_id"] = request_id
        for key, value in vars(record).items():
            # Plain values only: Django adds the request object, whose text is the whole link.
            if key not in STANDARD and key not in line and isinstance(value, PLAIN):
                line[key] = cut(value)
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            line["exception"] = record.exc_text
        if record.stack_info:
            line["stack"] = self.formatStack(record.stack_info)
        return json.dumps(line, ensure_ascii=False, default=str)


def config(json_lines: bool, level: str) -> dict[str, Any]:
    """The LOGGING of Django: one handler on stderr, in JSON or as plain text."""
    text = {"format": "%(levelname)s %(name)s %(message)s"}
    formatter: dict[str, Any] = {"()": JsonFormatter} if json_lines else text
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"main": formatter},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "main"}},
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            # Django's own handlers (plain text while DEBUG is on) are taken off: its records go
            # to the root handler like all others.
            "django": {"handlers": [], "level": level, "propagate": True},
            # The answers of runserver are logged by c4rsys.request instead.
            "django.server": {"handlers": [], "level": "WARNING", "propagate": True},
        },
    }


def gunicorn_config() -> dict[str, Any]:
    """logconfig_dict of gunicorn: its own records (start, workers, timeouts) in JSON too."""
    handler = {
        "class": "logging.StreamHandler",
        "formatter": "json",
        "stream": "ext://sys.stderr",
    }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": "c4rsys.platform.logs.JsonFormatter"}},
        "handlers": {"console": handler, "error_console": handler},
        "root": {"level": "INFO", "handlers": ["console"]},
        "loggers": {
            "gunicorn.error": {
                "level": "INFO",
                "handlers": ["error_console"],
                "propagate": False,
                "qualname": "gunicorn.error",
            },
            "gunicorn.access": {"level": "INFO", "handlers": [], "propagate": False},
        },
    }
