"""Delivery and internal move of a vehicle (ТЗ гл. 15; customer's decisions Д-1…Д-11 of
29.10.2026).

One kind of operation for a client delivery, a pickup from a client and an internal move
(§ 15.1.1). A point is a location of the company or «A / Other» — an external address in the
context of the location A, which does not create a location (§ 8.5.2, § 15.1.2). States and
results are kept apart (§ 15.3.9).
"""

from django.db import models
from django.db.models import F, Q
from django.utils.timezone import now

from c4rsys.companies.models import Company, Employment, Location
from c4rsys.platform.ids import uuid7


class Delivery(models.Model):
    class State(models.TextChoices):
        PLANNED = "planned"
        IN_TRANSIT = "in_transit"
        COMPLETED = "completed"
        CANCELLED = "cancelled"  # before the movement started
        TERMINATED = "terminated"  # the movement started, the route was not finished

    class Result(models.TextChoices):
        HANDED_TO_CLIENT = "handed_to_client"
        TAKEN_FROM_CLIENT = "taken_from_client"
        ACCEPTED_IN_LOCATION = "accepted_in_location"
        NOT_HANDED = "not_handed"  # the route is done, the transfer did not happen
        OTHER = "other"

    class PointKind(models.TextChoices):
        LOCATION = "location"
        OTHER = "other"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    number = models.PositiveIntegerField()
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, related_name="+"
    )
    from_kind = models.CharField(max_length=10, choices=PointKind.choices)
    from_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    from_name = models.CharField(max_length=200, blank=True, default="")
    from_address = models.CharField(max_length=500, blank=True, default="")
    from_comment = models.CharField(max_length=1000, blank=True, default="")
    from_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    from_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    to_kind = models.CharField(max_length=10, choices=PointKind.choices)
    to_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    to_name = models.CharField(max_length=200, blank=True, default="")
    to_address = models.CharField(max_length=500, blank=True, default="")
    to_comment = models.CharField(max_length=1000, blank=True, default="")
    to_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    to_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    planned_start = models.DateTimeField(null=True, blank=True)
    planned_end = models.DateTimeField(null=True, blank=True)
    # One responsible driver, an employee of the company (§ 15.2.4–15.2.5).
    driver = models.ForeignKey(
        Employment, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    state = models.CharField(max_length=12, choices=State.choices, default=State.PLANNED)
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)
    result = models.CharField(max_length=24, choices=Result.choices, blank=True, default="")
    result_comment = models.CharField(max_length=2000, blank=True, default="")
    end_reason = models.CharField(max_length=2000, blank=True, default="")
    # Who entered the start and the end, apart from the driver (§ 15.2.5, § 23.6.2).
    started_by_user_id = models.UUIDField(null=True, blank=True)
    finished_by_user_id = models.UUIDField(null=True, blank=True)
    finished_entered_at = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "delivery"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="delivery_company_id_uniq"),
            models.UniqueConstraint(fields=["company", "number"], name="delivery_number_uniq"),
            # § 15.1.3: a planned move has its planned start and end; entered afterwards it may
            # have none.
            models.CheckConstraint(
                condition=Q(planned_start__isnull=True, planned_end__isnull=True)
                | Q(
                    planned_start__isnull=False,
                    planned_end__isnull=False,
                    planned_end__gt=F("planned_start"),
                ),
                name="delivery_plan_complete",
            ),
            models.CheckConstraint(
                condition=~Q(state="planned")
                | Q(planned_start__isnull=False, actual_start__isnull=True),
                name="delivery_planned_has_plan",
            ),
            # § 15.1.3: the start needs the driver and the actual start.
            models.CheckConstraint(
                condition=~Q(state__in=["in_transit", "completed", "terminated"])
                | Q(driver__isnull=False, actual_start__isnull=False),
                name="delivery_started_has_driver",
            ),
            # § 15.1.3: the end needs the actual end and, for a completed one, the result.
            models.CheckConstraint(
                condition=~Q(state__in=["completed", "terminated"])
                | Q(actual_end__isnull=False, actual_end__gte=F("actual_start")),
                name="delivery_finished_has_end",
            ),
            models.CheckConstraint(
                condition=~Q(state="completed") | ~Q(result=""),
                name="delivery_completed_has_result",
            ),
            models.CheckConstraint(
                condition=~Q(from_kind="location", to_kind="location")
                | ~Q(from_location=F("to_location")),
                name="delivery_locations_differ",
            ),
            models.CheckConstraint(
                condition=~Q(from_kind="other")
                | ~Q(from_address="")
                | ~Q(from_name="")
                | ~Q(from_comment="")
                | Q(from_lat__isnull=False),
                name="delivery_from_other_described",
            ),
            models.CheckConstraint(
                condition=~Q(to_kind="other")
                | ~Q(to_address="")
                | ~Q(to_name="")
                | ~Q(to_comment="")
                | Q(to_lat__isnull=False),
                name="delivery_to_other_described",
            ),
            # One movement of a vehicle at a time.
            models.UniqueConstraint(
                fields=["vehicle_card"],
                condition=Q(state="in_transit"),
                name="delivery_one_in_transit_per_vehicle",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "order"], name="delivery_order_idx"),
            models.Index(fields=["vehicle_card", "state"], name="delivery_vehicle_idx"),
            models.Index(fields=["driver", "state"], name="delivery_driver_idx"),
        ]

    def __str__(self) -> str:
        return f"delivery {self.number}"
