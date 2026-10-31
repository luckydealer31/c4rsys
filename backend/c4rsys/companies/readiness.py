"""Readiness of activity contexts (§ 4.3.1, § 4.4.3).

The conditions depend on other modules: a rental context needs a current rental tariff or a
standalone additional service available to the location, a service context needs an active
service work. Each module registers the check of its context type; until it does, contexts of
that type are not ready.
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

# (company_id, location_id) -> the module's own conditions of § 4.3.1 are met
ReadinessCheck = Callable[[UUID, UUID], bool]

_checks: dict[str, ReadinessCheck] = {}


def register(context_type: str, check: ReadinessCheck) -> None:
    _checks[context_type] = check


def module_conditions_met(context_type: str, company_id: UUID, location_id: UUID) -> bool:
    # TODO(spec): readiness of a warehouse context is not defined in § 4.3.1.
    check = _checks.get(context_type)
    return check is not None and check(company_id, location_id)


def ready_contexts(company_id: UUID, context_type: str) -> set[UUID]:
    """The enabled contexts of the type whose conditions of § 4.3.1 are met now."""
    from c4rsys.companies.models import ActivityContext

    return {
        c.id
        for c in ActivityContext.objects.filter(
            company_id=company_id, type=context_type, state=ActivityContext.State.ENABLED
        )
        if module_conditions_met(context_type, company_id, c.location_id)
    }


def check_still_ready(
    ctx: Any, company_id: UUID, context_type: str, before: set[UUID], confirmed: bool
) -> list[str]:
    """§ 4.3.2 (block 16): disabling the last tariff, standalone service or service work is
    allowed after a warning. Called in the command after the change: when a context stops being
    configured and the employee has not confirmed, the command is refused with the contexts
    (nothing is saved); when confirmed, the journal names them. Existing operations go on."""
    from c4rsys.companies.models import ActivityContext
    from c4rsys.platform import errors

    lost = before - ready_contexts(company_id, context_type)
    if not lost:
        return []
    names = sorted(
        str(c.location.name)
        for c in ActivityContext.objects.filter(pk__in=lost).select_related("location")
    )
    if not confirmed:
        raise errors.business_rule("CONTEXT_WOULD_BE_UNCONFIGURED", locations=names)
    ctx.audit(
        "location.context_unconfigured",
        object_type="activity_context",
        object_id=sorted(str(x) for x in lost)[0],
        after={"contexts": sorted(str(x) for x in lost), "locations": names},
    )
    return names
