"""Access checks shared by all modules (A08, ТЗ § 2.8.1, § 23.3.8).

* Every read, list and command is limited to the active company. A passed company ID does not
  grant rights; an object of another company is answered as NOT_FOUND.
* Permissions are checked on the server against the current assignments at the moment of the
  action. The concrete resolver (sets, scopes, overrides) is implemented in T05; until then the
  module only defines the contract.
"""

from typing import Any, Protocol
from uuid import UUID

from django.db.models import Model, QuerySet

from c4rsys.platform import errors
from c4rsys.platform.commands import Actor


def for_company[M: Model](queryset: QuerySet[M], company_id: UUID) -> QuerySet[M]:
    """Restrict a queryset to one company. Every model of company data has `company_id`."""
    return queryset.filter(company_id=company_id)


def get_for_company[M: Model](queryset: QuerySet[M], company_id: UUID, **lookup: Any) -> M:
    """Fetch one object of the active company or raise NOT_FOUND (existence is not revealed)."""
    obj = for_company(queryset, company_id).filter(**lookup).first()
    if obj is None:
        raise errors.not_found()
    return obj


class PermissionResolver(Protocol):
    def has_permission(
        self,
        actor: Actor,
        permission: str,
        *,
        location_id: UUID | None = None,
        context_id: UUID | None = None,
        resource_id: UUID | None = None,
        whole_company: bool = False,
    ) -> bool: ...


def require_permission(
    resolver: PermissionResolver,
    actor: Actor,
    permission: str,
    *,
    location_id: UUID | None = None,
    context_id: UUID | None = None,
    resource_id: UUID | None = None,
    whole_company: bool = False,
) -> None:
    if actor.company_id is None:
        raise errors.permission_denied(permission)
    allowed = resolver.has_permission(
        actor,
        permission,
        location_id=location_id,
        context_id=context_id,
        resource_id=resource_id,
        whole_company=whole_company,
    )
    if not allowed:
        raise errors.permission_denied(permission)
