"""Personal dashboards (ТЗ § 21.2.4–21.2.5; customer's decision О-8 of 29.10.2026).

* The catalogue is the metrics of the starter dashboards; a metric is named by its dashboard and
  code (`finance:finance.result`). No formulas of the user.
* The common filters apply to the metrics they fit; each metric is computed by its own
  dashboard, so the rights and the area of the data stay as there (§ 21.1.1). Each metric is a
  block of its own: different units are never added up (О-8).
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.reports.models import PersonalDashboard
from c4rsys.reports.overview import Block
from c4rsys.reports.scope import Filters

MAX_METRICS = 40
CATALOG: dict[str, tuple[str, ...]] = {
    "overview": (
        "rental.issues_today",
        "rental.returns_today",
        "rental.active",
        "rental.overdue_returns",
        "fleet.total",
        "fleet.available",
        "fleet.in_rental",
        "fleet.in_service",
        "finance.receipts",
        "finance.expenses",
        "finance.balances",
        "finance.debt",
        "service.maintenance_overdue",
        "warehouse.below_minimum",
        "documents.unsigned",
    ),
    "rental": (
        "rental.bookings_by_state",
        "rental.started",
        "rental.finished",
        "rental.cancelled",
        "rental.hours",
        "rental.charges",
        "rental.payments",
        "rental.debt",
        "rental.daily_cost",
        "fleet.utilization",
        "fleet.idle_hours",
        "fleet.future_hours",
        "fleet.by_vehicle",
        "fleet.payback",
    ),
    "finance": (
        "finance.account_balances",
        "finance.refunds",
        "finance.transfers",
        "finance.result",
        "finance.deposits_received",
        "finance.deposits_returned",
        "finance.deposits_offset",
        "finance.by_article",
        "finance.by_location",
        "finance.by_context",
        "finance.by_client",
        "finance.by_counterparty",
        "finance.by_vehicle",
        "finance.orders_charged",
        "finance.orders_paid",
        "finance.receivable",
        "finance.payable",
    ),
    "service": (
        "service.operations_by_state",
        "service.jobs_by_state",
        "service.planned_today",
        "service.in_progress",
        "service.finished",
        "service.overdue",
        "service.general_zone",
        "service.on_posts",
        "service.posts",
        "service.planned_hours_avg",
        "service.actual_hours_avg",
        "service.charges",
        "service.materials",
        "service.payments",
        "service.debt",
        "service.contractor_costs",
        "service.by_executor",
        "service.maintenance_soon",
    ),
    "warehouse": (
        "warehouse.stock",
        "warehouse.value",
        "warehouse.issued",
        "warehouse.in_transit",
        "warehouse.expiring",
        "warehouse.period",
        "warehouse.purchases",
    ),
}
KNOWN = {f"{source}:{code}" for source, codes in CATALOG.items() for code in codes}


def catalog() -> list[dict[str, str]]:
    return [
        {"id": f"{source}:{code}", "dashboard": source, "code": code}
        for source, codes in CATALOG.items()
        for code in codes
    ]


def _mine(actor: Actor) -> QuerySet[PersonalDashboard]:
    if actor.company_id is None or actor.employment_id is None:
        raise errors.permission_denied("reports.view")
    return PersonalDashboard.objects.filter(
        company_id=actor.company_id, employment_id=actor.employment_id
    )


def dashboards_of(actor: Actor) -> list[PersonalDashboard]:
    return list(_mine(actor).order_by("created_at"))


def own(actor: Actor, dashboard_id: UUID, *, lock: bool = False) -> PersonalDashboard:
    """Only the employee's own: another's dashboard does not exist for them (§ 21.2.4)."""
    query = _mine(actor).filter(pk=dashboard_id)
    found = (query.select_for_update() if lock else query).first()
    if found is None:
        raise errors.not_found()
    return found


def _check(name: str, metrics: list[str]) -> tuple[str, list[str]]:
    if not name.strip():
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    if len(metrics) > MAX_METRICS or len(set(metrics)) != len(metrics):
        raise errors.validation_error("metrics")
    unknown = [m for m in metrics if m not in KNOWN]
    if unknown:
        raise errors.validation_error("metrics", code="REPORT_METRIC_UNKNOWN", metrics=unknown)
    return name.strip()[:100], metrics


def save(
    ctx: CommandContext,
    company: Company,
    dashboard_id: UUID | None,
    name: str,
    metrics: list[str],
    expected: int | None,
) -> PersonalDashboard:
    name, metrics = _check(name, metrics)
    if dashboard_id is None:
        found = PersonalDashboard.objects.create(
            company=company,
            employment_id=str(ctx.actor.employment_id),
            name=name,
            metrics=metrics,
        )
        ctx.audit(
            "report.dashboard_created",
            object_type="report_dashboard",
            object_id=found.id,
            after={"name": name, "metrics": metrics},
        )
        return found
    found = own(ctx.actor, dashboard_id, lock=True)
    if expected is not None and found.version != expected:
        raise errors.version_conflict()
    before = {"name": found.name, "metrics": found.metrics}
    found.name = name
    found.metrics = metrics
    found.version += 1
    found.updated_at = timezone.now()
    found.save()
    ctx.audit(
        "report.dashboard_changed",
        object_type="report_dashboard",
        object_id=found.id,
        before=before,
        after={"name": name, "metrics": metrics},
    )
    return found


def remove(ctx: CommandContext, dashboard_id: UUID) -> None:
    found = own(ctx.actor, dashboard_id, lock=True)
    before = {"name": found.name, "metrics": found.metrics}
    found.delete()
    ctx.audit(
        "report.dashboard_removed",
        object_type="report_dashboard",
        object_id=dashboard_id,
        before=before,
    )


Builder = Callable[[Actor, Company, Filters], list[Block]]


def builder_for(dashboard_id: UUID, builders: dict[str, Builder]) -> Builder:
    """The blocks of a personal dashboard: each source dashboard is computed once."""

    def build(actor: Actor, company: Company, filters: Filters) -> list[Block]:
        found = own(actor, dashboard_id)
        chosen = [(m.split(":", 1)[0], m.split(":", 1)[1]) for m in found.metrics]
        by_source: dict[str, dict[str, Block]] = {}
        for source in {s for s, _ in chosen}:
            by_source[source] = {b.code: b for b in builders[source](actor, company, filters)}
        return [by_source[s][c] for s, c in chosen if c in by_source[s]]

    return build


def payload(d: PersonalDashboard) -> dict[str, Any]:
    return {"id": str(d.id), "name": d.name, "metrics": d.metrics, "version": d.version}
