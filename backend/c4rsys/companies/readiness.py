"""Readiness of activity contexts (§ 4.3.1, § 4.4.3).

The conditions depend on other modules: a rental context needs a current rental tariff or a
standalone additional service available to the location, a service context needs an active
service work. Each module registers the check of its context type; until it does, contexts of
that type are not ready.
"""

from collections.abc import Callable
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
