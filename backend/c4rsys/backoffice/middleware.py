"""The internal interface of the system staff lives on its own subdomain (decision В-11.1).

* On the staff subdomain only its own API, the sign-in steps (no registration), `/api/site` and
  `/api/health` answer; the API of companies and of the personal cabinet does not, even to a
  signed-in user.
* On every other host the internal API does not exist (404).
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse

from c4rsys.platform import sites

EXACT = frozenset({"/api/health", "/api/site"})
PREFIXES = ("/api/auth/", "/api/staff/")
# A personal account is registered on the ordinary domain, not in the internal interface.
REFUSED = ("/api/auth/register/",)


def _refused(code: str) -> JsonResponse:
    return JsonResponse(
        {"error": {"code": code, "message_key": f"errors.{code.lower()}", "params": {}}},
        status=404,
    )


class StaffHostMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        path = request.path
        if sites.is_staff_host(request):
            allowed = (path in EXACT or path.startswith(PREFIXES)) and not path.startswith(REFUSED)
            if path.startswith("/api/") and not allowed:
                return _refused("STAFF_HOST")
        elif path.startswith("/api/staff/") or path == "/api/staff":
            return _refused("NOT_FOUND")
        return self.get_response(request)
