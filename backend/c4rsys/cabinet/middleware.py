"""On the domain of the personal cabinet only the personal interface works (customer's decision
of 30.10.2026): the API of companies is not served there, even to a signed-in employee.

The domain serves an allowlist: the paths the cabinet interface calls (sign-in and registration,
the profile, requests to the person, the cabinet itself). Everything else answers CABINET_HOST.
"""

import re
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse

from c4rsys.cabinet.views import is_cabinet_host

EXACT = frozenset(
    {"/api/health", "/api/site", "/api/me", "/api/profile-links", "/api/owner-requests"}
)
PREFIXES = (
    "/api/auth/",
    "/api/me/",
    "/api/cabinet/",
    "/api/profile-links/",
    "/api/owner-requests/",
)
# The profile photo is the only file of the cabinet in the general file API: a document file is
# read through the cabinet (`/api/cabinet/documents/<id>/file`), company files are not served.
FILE_RE = re.compile(
    r"^/api/files/(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", re.I
)
# The owner uploads the files of the global documents of his vehicle (§ 8.2.3).
CABINET_PURPOSES = ("avatar", "vehicle_document")


def _file_allowed(request: HttpRequest) -> bool:
    from c4rsys.files.models import StoredFile

    if request.path == "/api/files":
        return request.method == "POST" and request.GET.get("purpose") in CABINET_PURPOSES
    match = FILE_RE.match(request.path)
    if match is None or request.method not in ("GET", "HEAD"):
        return False
    # Read only: the files view itself checks who may read it.
    found = StoredFile.objects.filter(pk=match["id"]).values_list("purpose", flat=True).first()
    return found in CABINET_PURPOSES


def allowed(request: HttpRequest) -> bool:
    path = request.path
    if path in EXACT or path.startswith(PREFIXES):
        return True
    return path.startswith("/api/files") and _file_allowed(request)


class CabinetHostMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if is_cabinet_host(request) and not allowed(request):
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
