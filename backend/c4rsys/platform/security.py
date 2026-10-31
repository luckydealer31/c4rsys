"""Security events of attempts to open an object that is not available (ТЗ § 2.8.1).

An object of another company is answered exactly like a missing one (NOT_FOUND, existence and
content are not revealed). Every such answer to a signed-in employee on an address of his active
company that names an object by its ID is journaled centrally as a security event in the journal
of that company, with the employee, the route and the ID. The answer stays the same.

PROPOSAL: the event does not tell a missing object from an object of another company — telling
them apart would need a lookup in every module, and the journal must not reveal either. A denied
company itself is journaled by companies.resolve_company.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from django.http import HttpRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequestScope:
    user_id: UUID
    session_id: UUID | None
    company_id: UUID | None
    employment_id: UUID | None


# (request, company public ID or "") -> who asks and in which company, or None.
ScopeResolver = Callable[[HttpRequest, str], RequestScope | None]
_resolvers: list[ScopeResolver] = []


def register_scope_resolver(resolver: ScopeResolver) -> None:
    _resolvers.append(resolver)


def _object_ids(request: HttpRequest) -> list[str]:
    match = getattr(request, "resolver_match", None)
    if match is None:
        return []
    return [str(v) for v in match.kwargs.values() if isinstance(v, UUID)]


def on_not_found(request: HttpRequest, request_id: str) -> None:
    """Called for every NOT_FOUND answer of the API (c4rsys.platform.middleware)."""
    if request.path.startswith("/api/staff/"):
        return  # the internal interface journals its reads itself
    ids = _object_ids(request)
    if not ids or not _resolvers:
        return
    match = request.resolver_match
    cmp = str(match.kwargs.get("cmp", "")) if match is not None else ""
    scope = None
    for resolver in _resolvers:
        scope = resolver(request, cmp)
        if scope is not None:
            break
    if scope is None or (cmp and scope.company_id is None):
        return
    from c4rsys.platform.models import AuditEvent

    try:
        AuditEvent.objects.create(
            company_id=scope.company_id,
            actor_user_id=scope.user_id,
            actor_employment_id=scope.employment_id,
            session_id=scope.session_id,
            request_id=request_id,
            action="security.object_not_available",
            object_type="object",
            object_id=ids[-1][:64],
            result="denied",
            after={
                "route": match.route if match is not None else "",
                "method": request.method,
                "ids": ids,
            },
        )
    except Exception:
        # The answer is a refusal anyway; the failure is a controlled incident in the log.
        logger.exception("Security event not written (request %s)", request_id)
