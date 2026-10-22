import json

from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from c4rsys.platform.errors import business_rule
from c4rsys.platform.middleware import REQUEST_ID_HEADER, ApiMiddleware, get_request_id


def _ok(request: HttpRequest) -> HttpResponse:
    return HttpResponse("ok")


def test_every_response_gets_a_request_id(rf: RequestFactory) -> None:
    request = rf.get("/api/health")

    response = ApiMiddleware(_ok)(request)

    assert response[REQUEST_ID_HEADER] == get_request_id(request)
    assert len(response[REQUEST_ID_HEADER]) == 32


def test_api_error_becomes_single_json_format(rf: RequestFactory) -> None:
    middleware = ApiMiddleware(_ok)
    request = rf.post("/api/c/x/rentals/issue")
    middleware(request)

    response = middleware.process_exception(
        request, business_rule("VEHICLE_NOT_AVAILABLE", conflict="R-1")
    )

    assert response is not None
    assert response.status_code == 422
    assert json.loads(response.content) == {
        "error": {
            "code": "VEHICLE_NOT_AVAILABLE",
            "message_key": "errors.vehicle_not_available",
            "params": {"conflict": "R-1"},
            "field": None,
            "request_id": get_request_id(request),
        }
    }


def test_unexpected_error_does_not_leak_details(rf: RequestFactory) -> None:
    middleware = ApiMiddleware(_ok)
    request = rf.get("/api/anything")
    middleware(request)

    response = middleware.process_exception(request, KeyError("secret internals"))

    assert response is not None
    assert response.status_code == 500
    body = json.loads(response.content)
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in response.content.decode()


def test_non_api_paths_are_left_to_django(rf: RequestFactory) -> None:
    request = rf.get("/static/x.css")

    assert ApiMiddleware(_ok).process_exception(request, KeyError("x")) is None
