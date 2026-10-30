"""Server-side session check on every request (A08, ТЗ § 24.4.2).

Activity is a user action that reaches the server. Requests the interface makes on its own
(polling, background refresh) send the header `X-C4R-Background: 1` and do not extend the session.
"""

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from c4rsys.accounts import security, services
from c4rsys.accounts.models import UserAccount, UserSession
from c4rsys.platform import errors, sites

SESSION_COOKIE = "c4r_session"
BACKGROUND_HEADER = "HTTP_X_C4R_BACKGROUND"
_SESSION_KEY = "c4rsys.session"
_CLEAR_COOKIE_KEY = "c4rsys.clear_session_cookie"


def current_session(request: HttpRequest) -> UserSession | None:
    session = request.META.get(_SESSION_KEY)
    return session if isinstance(session, UserSession) else None


def require_user(request: HttpRequest) -> tuple[UserAccount, UserSession]:
    session = current_session(request)
    if session is None:
        raise errors.ApiError("SESSION_REQUIRED", status=401)
    return session.user, session


def set_session_cookie(response: HttpResponse, token: str, user: UserAccount) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=user.session_total_limit_min * 60,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
    )


def clear_session_cookie(response: HttpResponse) -> None:
    response.delete_cookie(SESSION_COOKIE, samesite="Lax")


class SessionMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        token = request.COOKIES.get(SESSION_COOKIE)
        if token:
            self._load(request, token)
        response = self.get_response(request)
        if request.META.get(_CLEAR_COOKIE_KEY):
            clear_session_cookie(response)
        return response

    def _load(self, request: HttpRequest, token: str) -> None:
        session = (
            UserSession.objects.select_related("user")
            .filter(token_hash=security.token_hash(token), ended_at__isnull=True)
            .first()
        )
        if session is None:
            request.META[_CLEAR_COOKIE_KEY] = True
            return
        now = timezone.now()
        reason = services.expiry_reason(session, session.user, now)
        if reason is not None:
            services.end_session(session, reason, now)
            request.META[_CLEAR_COOKIE_KEY] = True
            return
        if session.user.state != UserAccount.State.ACTIVE:
            request.META[_CLEAR_COOKIE_KEY] = True
            return
        # A session of the internal interface of the system staff works only on its subdomain,
        # and only such a session works there (decision В-11.1). The cookie is not cleared: it
        # belongs to the other interface's host.
        if (session.site == sites.STAFF) != sites.is_staff_host(request):
            return
        if request.META.get(BACKGROUND_HEADER) != "1":
            UserSession.objects.filter(pk=session.pk).update(last_activity_at=now)
            session.last_activity_at = now
        request.META[_SESSION_KEY] = session


def mark_session_cleared(request: HttpRequest) -> None:
    request.META[_CLEAR_COOKIE_KEY] = True
    request.META.pop(_SESSION_KEY, None)
