"""Request ID and conversion of errors to the single JSON format (A08)."""

import logging
import time
import uuid
from collections.abc import Callable

from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, HttpResponse, JsonResponse

from c4rsys.platform import errors, logs
from c4rsys.platform.errors import ApiError
from c4rsys.platform.monitoring import cut

logger = logging.getLogger(__name__)
# One record per answered request (audit item 22, c4rsys.platform.logs).
requests_log = logging.getLogger("c4rsys.request")
# The check of Docker every 10 seconds; an answer other than 200 is still logged.
QUIET_PATHS = {"/api/health"}

REQUEST_ID_META_KEY = logs.REQUEST_ID_ENVIRON_KEY
REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id(request: HttpRequest) -> str:
    value = request.META.get(REQUEST_ID_META_KEY, "")
    return str(value)


class ApiMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Given at request_started (c4rsys.platform.logs); a request made without the signal
        # (RequestFactory in tests) gets one here.
        request_id = request.META.get(REQUEST_ID_META_KEY) or uuid.uuid4().hex
        request.META[REQUEST_ID_META_KEY] = request_id
        logs.current_request_id.set(request_id)
        started = time.monotonic()
        response = self.get_response(request)
        response[REQUEST_ID_HEADER] = request_id
        if not (response.status_code == 200 and request.path in QUIET_PATHS):
            match = getattr(request, "resolver_match", None)
            requests_log.info(
                "%s %s %s",
                request.method,
                cut(request.path),
                response.status_code,
                extra={
                    "method": request.method,
                    "path": request.path,
                    "route": match.route if match else None,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
        return response

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        if not request.path.startswith("/api/"):
            return None
        request_id = get_request_id(request)
        if isinstance(exception, ApiError):
            error = exception
            if error.status == 404:
                # § 2.8.1: an attempt to open an unavailable object is a security event.
                from c4rsys.platform import security

                security.on_not_found(request, request_id)
        elif isinstance(exception, RequestDataTooBig):
            # Audit item 35: Django refuses a body above its limit by the declared size, before
            # reading it; that is an answer to the client, not a failure of the server.
            error = ApiError("REQUEST_TOO_LARGE", status=413)
        else:
            logger.exception("Unhandled error (request %s)", request_id, exc_info=exception)
            error = errors.internal_error()
        return JsonResponse(error.payload(request_id), status=error.status)
