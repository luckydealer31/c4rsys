"""On the domain of the personal cabinet only the personal interface works (customer's decision
of 30.10.2026): the API of companies is not served there, even to a signed-in employee."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse

from c4rsys.cabinet.views import is_cabinet_host

# Paths of the B2B interface: a company and its data, creating a company, employee invitations.
B2B_PREFIXES = ("/api/c/", "/api/companies", "/api/invitations")


class CabinetHostMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if is_cabinet_host(request) and request.path.startswith(B2B_PREFIXES):
            return JsonResponse(
                {
                    "error": {
                        "code": "CABINET_HOST",
                        "message_key": "errors.cabinet_host",
                        "params": {},
                    }
                },
                status=404,
            )
        return self.get_response(request)
