"""The starter dashboard «Service» (ТЗ § 21.7.1; customer's decision О-7 of 29.10.2026).

Counted by the right «view the service» in its locations (§ 21.1.1). The load of a post is its
occupied time against its working time by the weekly schedule of the location (О-7). Figures by
executors are not a record of working time or pay (§ 21.7.1).
"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from django.utils import timezone

from c4rsys.companies.models import WEEKDAYS, Company
from c4rsys.platform.commands import Actor
from c4rsys.reports.overview import Block
from c4rsys.reports.rental import Span, _hours, _money, _union
from c4rsys.reports.scope import Filters, area

OPEN = ("planned", "accepted", "in_progress")


def _working(location_id: UUID, start: datetime, end: datetime, zone: str) -> timedelta:
    """Working time of a location by its weekly schedule inside [start, end)."""
    from c4rsys.companies.models import LocationSchedule, schedule_24_7

    found = LocationSchedule.objects.filter(location_id=location_id).first()
    weekly = found.weekly if found else schedule_24_7()
    tz = ZoneInfo(zone)
    total = timedelta(0)
    day = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    while day <= last:
        for interval in weekly.get(WEEKDAYS[day.weekday()], []):
            a = datetime.combine(day, time.fromisoformat(interval["from"]), tzinfo=tz)
            to = interval["to"]
            b = (
                datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
                if to == "24:00"
                else datetime.combine(day, time.fromisoformat(to), tzinfo=tz)
            )
            part = Span(a, b).clip(start, end)
            if part is not None:
                total += part.end - part.start
        day += timedelta(days=1)
    return total


def build(actor: Actor, company: Company, filters: Filters) -> list[Block]:
    from c4rsys.companies import staff
    from c4rsys.finance.models import ChargeEntry, PaymentAllocation
    from c4rsys.orders import services as orders
    from c4rsys.orders.models import Order
    from c4rsys.service import maintenance
    from c4rsys.service.models import ServiceJob, ServiceMaterial, ServiceOperation, ServicePost

    where = area(actor, "service.view", company, filters)
    if where is None:
        return []
    now = timezone.now()
    start, end = filters.start_at, min(filters.end_at, now)
    ops = ServiceOperation.objects.filter(company=company)
    if not where.whole:
        ops = ops.filter(location_id__in=where.location_ids)
    if filters.vehicle_id:
        ops = ops.filter(vehicle_card_id=filters.vehicle_id)
    period = ops.filter(created_at__gte=filters.start_at, created_at__lt=filters.end_at)
    by_state: dict[str, int] = defaultdict(int)
    for state in period.values_list("state", flat=True):
        by_state[state] += 1
    jobs = ServiceJob.objects.filter(operation__in=ops)
    job_states: dict[str, int] = defaultdict(int)
    for state in jobs.filter(
        created_at__gte=filters.start_at, created_at__lt=filters.end_at
    ).values_list("state", flat=True):
        job_states[state] += 1
    day_start, day_end = filters.day_bounds(filters.today())
    held = ops.filter(state__in=("accepted", "in_progress"))
    finished = ops.filter(
        state="works_done", finished_at__gte=filters.start_at, finished_at__lt=filters.end_at
    )
    planned_hours: list[timedelta] = []
    actual_hours: list[timedelta] = []
    for op in finished:
        if op.planned_start and op.planned_end:
            planned_hours.append(op.planned_end - op.planned_start)
        if op.accepted_at and op.finished_at:
            actual_hours.append(op.finished_at - op.accepted_at)

    def average(values: list[timedelta]) -> str | None:
        if not values:
            return None
        return str(_hours(sum(values, timedelta(0)) / len(values)))

    # Posts: busy now; occupied against working time in the period (О-7).
    posts = ServicePost.objects.filter(company=company, state="active").select_related("location")
    if not where.whole:
        posts = posts.filter(location_id__in=where.location_ids)
    post_rows = []
    for post in posts.order_by("location__name", "name"):
        spans = []
        for op in ops.filter(post=post, accepted_at__isnull=False, accepted_at__lt=end).exclude(
            finished_at__lte=start
        ):
            if op.accepted_at is None:
                continue
            part = Span(op.accepted_at, op.finished_at or now).clip(start, end)
            if part is not None:
                spans.append(part)
        occupied = _union(spans)
        working = (
            _working(post.location_id, start, end, filters.zone) if start < end else timedelta(0)
        )
        post_rows.append(
            {
                "post": f"{post.location.name} · {post.name}",
                "busy": held.filter(post=post).exists(),
                "occupied_hours": str(_hours(occupied)),
                "working_hours": str(_hours(working)),
                "utilization": str(
                    (
                        Decimal(occupied.total_seconds()) / Decimal(working.total_seconds()) * 100
                    ).quantize(Decimal("0.1"))
                )
                if working.total_seconds() > 0
                else None,
            }
        )

    # Money (§ 21.7.1): charges to clients, payments, debt, contractors' costs.
    job_ids = list(jobs.values_list("id", flat=True))
    material_ids = list(
        ServiceMaterial.objects.filter(job_id__in=job_ids).values_list("id", flat=True)
    )
    charges: dict[str, Decimal] = defaultdict(Decimal)
    materials: dict[str, Decimal] = defaultdict(Decimal)
    for c in ChargeEntry.objects.filter(
        company=company,
        created_at__gte=filters.start_at,
        created_at__lt=filters.end_at,
        source_type__in=(
            "service_job",
            "service_material",
            "discount_service_job",
            "discount_service_material",
        ),
    ).filter(source_id__in=job_ids + material_ids):
        # Positions count net of their discounts (customer's decision of 30.10.2026).
        charges[c.currency_id] += c.amount
        if c.source_type.endswith("service_material"):
            materials[c.currency_id] += c.amount
    order_ids = set(ops.exclude(order__is_internal=True).values_list("order_id", flat=True))
    paid: dict[str, Decimal] = defaultdict(Decimal)
    for a in PaymentAllocation.objects.filter(
        company=company,
        order_id__in=order_ids,
        state="active",
        created_at__gte=filters.start_at,
        created_at__lt=filters.end_at,
    ):
        paid[a.currency_id] += a.amount
    debt: dict[str, Decimal] = defaultdict(Decimal)
    for order in Order.objects.filter(company=company, pk__in=order_ids):
        for currency, money in orders.amounts_of(order).items():
            if money.balance > 0:
                debt[currency] += money.balance
    contractors: dict[str, Decimal] = defaultdict(Decimal)
    for job in jobs.filter(
        contractor_cost__isnull=False,
        state__in=("done", "terminated"),
        actual_end__gte=filters.start_at,
        actual_end__lt=filters.end_at,
    ):
        contractors[job.contractor_currency_id] += job.contractor_cost  # type: ignore[index, operator]

    # Executors: number and duration of their works in the period.
    executors: dict[UUID, list[timedelta]] = defaultdict(list)
    for job in jobs.filter(
        executor__isnull=False,
        actual_start__isnull=False,
        actual_end__gte=filters.start_at,
        actual_end__lt=filters.end_at,
    ):
        if job.actual_end and job.actual_start:
            executors[job.executor_id].append(job.actual_end - job.actual_start)  # type: ignore[index]
    names = staff.employee_names(company.id, set(executors)) if executors else {}
    executor_rows = [
        {"executor": names.get(x, ""), "works": len(v), "hours": str(_hours(sum(v, timedelta(0))))}
        for x, v in sorted(executors.items(), key=lambda item: names.get(item[0], ""))
    ]

    due = [
        (card, status)
        for card, _rule, status in maintenance.due(company.id)
        if where.includes(card.accounting_location_id)
        and (filters.vehicle_id is None or card.id == filters.vehicle_id)
    ]
    blocks = [
        Block(
            "service.operations_by_state",
            "service",
            "table",
            None,
            [{"state": s, "count": n} for s, n in sorted(by_state.items())],
            where,
            "/service",
        ),
        Block(
            "service.jobs_by_state",
            "service",
            "table",
            None,
            [{"state": s, "count": n} for s, n in sorted(job_states.items())],
            where,
            "/service",
        ),
        Block(
            "service.planned_today",
            "service",
            "count",
            ops.filter(
                state="planned", planned_start__gte=day_start, planned_start__lt=day_end
            ).count(),
            area=where,
            link="/service",
        ),
        Block(
            "service.in_progress",
            "service",
            "count",
            ops.filter(state="in_progress").count(),
            area=where,
            link="/service",
        ),
        Block(
            "service.finished", "service", "count", finished.count(), area=where, link="/service"
        ),
        Block(
            "service.overdue",
            "service",
            "count",
            ops.filter(state__in=OPEN, planned_end__lt=now).count(),
            area=where,
            link="/service",
        ),
        Block(
            "service.general_zone",
            "service",
            "count",
            held.filter(post__isnull=True).count(),
            area=where,
            link="/service",
        ),
        Block(
            "service.on_posts",
            "service",
            "count",
            held.filter(post__isnull=False).count(),
            area=where,
            link="/service",
        ),
        Block("service.posts", "service", "table", None, post_rows, where),
        Block("service.planned_hours_avg", "service", "hours", average(planned_hours), area=where),
        Block("service.actual_hours_avg", "service", "hours", average(actual_hours), area=where),
        Block("service.charges", "service", "money", None, _money(charges), where, "/orders"),
        Block("service.materials", "service", "money", None, _money(materials), where, "/orders"),
        Block("service.payments", "service", "money", None, _money(paid), where, "/orders"),
        Block("service.debt", "service", "money", None, _money(debt), where, "/orders"),
        Block("service.contractor_costs", "service", "money", None, _money(contractors), where),
        Block("service.by_executor", "service", "table", None, executor_rows, where),
        Block(
            "service.maintenance_soon",
            "service",
            "count",
            len({c.id for c, st in due if st.state == "soon"}),
            area=where,
            link="/service",
        ),
        Block(
            "service.maintenance_overdue",
            "service",
            "count",
            len({c.id for c, st in due if st.state == "required"}),
            area=where,
            link="/service",
        ),
    ]
    for b in blocks:
        if b.unit == "money" and not b.rows:
            b.value = 0
    return blocks
