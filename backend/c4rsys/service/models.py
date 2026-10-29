"""Service works (ТЗ гл. 16; customer's decisions С-1…С-7 of 29.10.2026).

* `SystemWork` — the base extensible reference of works, shared by all companies (§ 16.1.1,
  § 9.1.5). It grows by new migrations (decision С-1).
* `ServiceWork` — a work of the company catalog: chosen from the reference or its own; a fixed
  price or norm hours × the hourly rate; price and hours may be filled later (§ 16.1.1–16.1.5).
  Prices are in the main currency of the company (decision С-2).
* `ServiceRate` — the hourly rate of the company (no location) or of a service location; a work
  may have its own (§ 16.1.2).
* `ServicePost` — an optional post of a service location; the general zone of the location is
  not a row: an operation placed without a post is in it (§ 16.3).
* `ServiceOperation` — the works on one vehicle in an order (§ 16.1.6, § 16.4.8).
* `ServiceJob` — one work of an operation with its executor, snapshot of the price and result
  (§ 16.1.3, § 16.4).
"""

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import models
from django.db.models import F, Q
from django.utils.timezone import now

from c4rsys.companies.models import Company, Employment, Location
from c4rsys.platform.ids import uuid7
from c4rsys.platform.models import Currency


class SystemWork(models.Model):
    code = models.CharField(max_length=60, primary_key=True)
    names = models.JSONField()  # {"en": ..., "ru": ..., "th": ...}
    sort = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "service_system_work"

    def __str__(self) -> str:
        return self.code


class ServiceWork(models.Model):
    class Pricing(models.TextChoices):
        FIXED = "fixed"
        HOURS = "hours"

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=200)
    system_work = models.ForeignKey(
        SystemWork, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    pricing = models.CharField(max_length=10, choices=Pricing.choices)
    price = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    hours = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    rate = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "service_work"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="service_work_company_id_uniq"),
            models.CheckConstraint(condition=~Q(name=""), name="service_work_name_not_empty"),
            models.CheckConstraint(
                condition=Q(price__isnull=True) | Q(price__gte=0), name="service_work_price_gte_0"
            ),
            models.CheckConstraint(
                condition=Q(hours__isnull=True) | Q(hours__gt=0), name="service_work_hours_gt_0"
            ),
            models.CheckConstraint(
                condition=Q(rate__isnull=True) | Q(rate__gt=0), name="service_work_rate_gt_0"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ServiceRate(models.Model):
    """The hourly rate of the company (`location` empty) or of one service location."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    rate = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    updated_by_user_id = models.UUIDField()
    updated_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "service_rate"
        constraints = [
            models.UniqueConstraint(
                fields=["company"],
                condition=Q(location__isnull=True),
                name="service_rate_company_uniq",
            ),
            models.UniqueConstraint(
                fields=["company", "location"],
                condition=Q(location__isnull=False),
                name="service_rate_location_uniq",
            ),
            models.CheckConstraint(condition=Q(rate__gt=0), name="service_rate_gt_0"),
        ]

    def __str__(self) -> str:
        return f"rate {self.location_id or 'company'}"


class ServicePost(models.Model):
    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=100)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "service_post"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="service_post_company_id_uniq"),
            models.CheckConstraint(condition=~Q(name=""), name="service_post_name_not_empty"),
            models.UniqueConstraint(
                fields=["location", "name"],
                condition=Q(state="active"),
                name="service_post_active_name_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.name


# States in which an operation holds its planned post interval and, once the vehicle is accepted,
# the vehicle itself.
OPEN_STATES = ("planned", "accepted", "in_progress")


class ServiceOperation(models.Model):
    class State(models.TextChoices):
        PLANNED = "planned"
        ACCEPTED = "accepted"  # the vehicle is in the general zone or on a post, waiting for works
        IN_PROGRESS = "in_progress"
        WORKS_DONE = "works_done"
        CANCELLED = "cancelled"
        TERMINATED = "terminated"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    number = models.PositiveIntegerField()
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, related_name="+"
    )
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=2000)
    planned_start = models.DateTimeField(null=True, blank=True)
    planned_end = models.DateTimeField(null=True, blank=True)
    planned_period = DateTimeRangeField(null=True, blank=True)
    post = models.ForeignKey(
        ServicePost, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=2000, blank=True, default="")
    state = models.CharField(max_length=12, choices=State.choices, default=State.PLANNED)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "service_operation"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="service_operation_company_id_uniq"
            ),
            models.UniqueConstraint(
                fields=["company", "number"], name="service_operation_number_uniq"
            ),
            models.CheckConstraint(
                condition=~Q(reason=""), name="service_operation_reason_not_empty"
            ),
            # § 16.2.3: the planned start and end are entered together.
            models.CheckConstraint(
                condition=Q(
                    planned_start__isnull=True,
                    planned_end__isnull=True,
                    planned_period__isnull=True,
                )
                | Q(
                    planned_start__isnull=False,
                    planned_end__isnull=False,
                    planned_period__isnull=False,
                    planned_end__gt=F("planned_start"),
                ),
                name="service_operation_plan_complete",
            ),
            models.CheckConstraint(
                condition=Q(state="planned") | Q(state="cancelled") | Q(accepted_at__isnull=False),
                name="service_operation_accepted_has_time",
            ),
            # § 16.2.5: no overlapping records on the same post.
            ExclusionConstraint(
                name="service_operation_post_no_overlap",
                expressions=[
                    ("post", RangeOperators.EQUAL),
                    ("planned_period", RangeOperators.OVERLAPS),
                ],
                condition=Q(state__in=OPEN_STATES) & Q(post__isnull=False),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "order"], name="service_operation_order_idx"),
            models.Index(fields=["vehicle_card", "state"], name="service_operation_vehicle_idx"),
        ]

    def __str__(self) -> str:
        return f"service {self.number}"


class ServiceJob(models.Model):
    class State(models.TextChoices):
        PLANNED = "planned"
        IN_PROGRESS = "in_progress"
        DONE = "done"
        CANCELLED = "cancelled"  # before the start
        TERMINATED = "terminated"  # after the start

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    operation = models.ForeignKey(ServiceOperation, on_delete=models.PROTECT, related_name="jobs")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="+")
    work = models.ForeignKey(ServiceWork, on_delete=models.PROTECT, related_name="+")
    # The terms at the moment of adding (§ 16.1.3): name, system work, pricing, hours, rate and
    # where the rate came from; a later catalog change does not touch them.
    snapshot = models.JSONField()
    # The price for the client; empty — not known yet, which differs from zero (§ 16.1.4).
    price = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    executor = models.ForeignKey(
        Employment, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    state = models.CharField(max_length=12, choices=State.choices, default=State.PLANNED)
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)
    odometer = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True)
    comment = models.CharField(max_length=2000, blank=True, default="")
    end_reason = models.CharField(max_length=2000, blank=True, default="")
    # Who entered the result, apart from the executor (§ 16.4.2).
    result_by_user_id = models.UUIDField(null=True, blank=True)
    result_at = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "service_job"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="service_job_company_id_uniq"),
            models.CheckConstraint(
                condition=Q(price__isnull=True) | Q(price__gte=0), name="service_job_price_gte_0"
            ),
            models.CheckConstraint(
                condition=~Q(state__in=["in_progress", "done", "terminated"])
                | Q(actual_start__isnull=False),
                name="service_job_started_has_start",
            ),
            models.CheckConstraint(
                condition=~Q(state__in=["done", "terminated"])
                | Q(actual_end__isnull=False, actual_end__gte=F("actual_start")),
                name="service_job_finished_has_end",
            ),
            models.CheckConstraint(
                condition=~Q(state="done") | Q(odometer__isnull=False),
                name="service_job_done_has_odometer",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "order"], name="service_job_order_idx"),
            models.Index(fields=["executor", "state"], name="service_job_executor_idx"),
        ]

    def __str__(self) -> str:
        return f"job {self.id}"


class ServiceMaterial(models.Model):
    """A part or material issued from a warehouse to one work (§ 16.5.1). The warehouse line
    keeps what was returned and consumed; this row keeps the selling price for the order. The
    price is for `price_per` base units; empty — not known yet (§ 16.1.4)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    job = models.ForeignKey(ServiceJob, on_delete=models.PROTECT, related_name="materials")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="+")
    warehouse_line = models.OneToOneField(
        "warehouse.OperationLine", on_delete=models.PROTECT, related_name="+"
    )
    item_name = models.CharField(max_length=200)
    unit = models.CharField(max_length=20)
    label = models.CharField(max_length=100, blank=True, default="")  # unit or lot ID
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    price = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    price_per = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "service_material"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="service_material_company_id_uniq"
            ),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="service_material_qty_gt_0"),
            models.CheckConstraint(
                condition=Q(price__isnull=True) | Q(price__gte=0),
                name="service_material_price_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(price_per__gt=0), name="service_material_price_per_gt_0"
            ),
        ]

    def __str__(self) -> str:
        return f"material {self.item_name}"
