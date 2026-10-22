"""Request ID and conversion of errors to the single JSON format (A08)."""

import logging
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse

from c4rsys.platform import errors
from c4rsys.platform.errors import ApiError

logger = logging.getLogger(__name__)

REQUEST_ID_META_KEY = "c4rsys.request_id"
REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id(request: HttpRequest) -> str:
    value = request.META.get(REQUEST_ID_META_KEY, "")
    return str(value)


class ApiMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.META[REQUEST_ID_META_KEY] = uuid.uuid4().hex
        response = self.get_response(request)
        response[REQUEST_ID_HEADER] = get_request_id(request)
        return response

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        if not request.path.startswith("/api/"):
            return None
        request_id = get_request_id(request)
        if isinstance(exception, ApiError):
            error = exception
        else:
            logger.exception("Unhandled error (request %s)", request_id, exc_info=exception)
            error = errors.internal_error()
        return JsonResponse(error.payload(request_id), status=error.status)
