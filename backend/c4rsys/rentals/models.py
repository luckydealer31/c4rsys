"""Rental tariffs (ТЗ § 13.1–13.2)."""

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company, Location
from c4rsys.platform.ids import uuid7
from c4rsys.platform.models import Currency


class Tariff(models.Model):
    """A tariff of the company catalog (§ 13.1.1, § 13.1.3).

    Available to the whole company or to selected locations. Rentals keep a snapshot of the tariff,
    so later changes apply only to new rentals (§ 13.6.1).
    """

    class Unit(models.TextChoices):
        MINUTE = "minute"
        HOUR = "hour"
        DAY = "day"
        MONTH = "month"

    class DayMode(models.TextChoices):
        FROM_ISSUE = "from_issue"
        FIXED_BOUNDARY = "fixed_boundary"

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=150)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    unit = models.CharField(max_length=10, choices=Unit.choices)
    day_mode = models.CharField(max_length=20, choices=DayMode.choices, blank=True, default="")
    day_boundary_time = models.TimeField(null=True, blank=True)
    all_locations = models.BooleanField(default=True)
    locations = models.ManyToManyField(Location, through="TariffLocation", related_name="+")
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "tariff"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="tariff_company_id_uniq"),
            models.CheckConstraint(condition=~Q(name=""), name="tariff_name_not_empty"),
            # § 13.2.2: a daily tariff has one of the two day modes.
            models.CheckConstraint(
                condition=~Q(unit="day") | ~Q(day_mode=""), name="tariff_day_has_mode"
            ),
            models.CheckConstraint(
                condition=~Q(day_mode="fixed_boundary") | Q(day_boundary_time__isnull=False),
                name="tariff_fixed_boundary_has_time",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class TariffRange(models.Model):
    """Range of billable units with its rate (§ 13.2.5–13.2.8). Bounds are inclusive; the last
    range has no upper bound. The whole structure is checked by the command that saves it."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    tariff = models.ForeignKey(Tariff, on_delete=models.PROTECT, related_name="ranges")
    from_units = models.PositiveIntegerField()
    to_units = models.PositiveIntegerField(null=True, blank=True)
    rate = models.DecimalField(max_digits=20, decimal_places=4)

    class Meta:
        db_table = "tariff_range"
        constraints = [
            models.UniqueConstraint(
                fields=["tariff", "from_units"], name="tariff_range_tariff_from_uniq"
            ),
            models.CheckConstraint(condition=Q(from_units__gte=1), name="tariff_range_from_min"),
            models.CheckConstraint(
                condition=Q(to_units__isnull=True) | Q(to_units__gte=models.F("from_units")),
                name="tariff_range_to_not_below_from",
            ),
            models.CheckConstraint(condition=Q(rate__gt=0), name="tariff_range_rate_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.from_units}-{self.to_units or '∞'}: {self.rate}"


class TariffLocation(models.Model):
    """Selected location of a tariff with limited availability (§ 13.1.3)."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    tariff = models.ForeignKey(Tariff, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "tariff_location"
        constraints = [
            models.UniqueConstraint(fields=["tariff", "location"], name="tariff_location_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.tariff_id}@{self.location_id}"


class TariffVehicleTerms(models.Model):
    """Assignment of a tariff to a vehicle with its own mileage and deposit terms (§ 13.1.2,
    § 13.1.6, § 13.4.1). Both choices are explicit (§ 22.1.8). Mileage values are in the
    vehicle's odometer unit, money in the tariff's currency. Rentals keep a snapshot (§ 13.6.1).
    """

    class MileageMode(models.TextChoices):
        UNLIMITED = "unlimited"
        INCLUDED = "included"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    tariff = models.ForeignKey(Tariff, on_delete=models.PROTECT, related_name="vehicle_terms")
    vehicle_card = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, related_name="+"
    )
    mileage_mode = models.CharField(max_length=10, choices=MileageMode.choices)
    included_per_unit = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True)
    overage_price = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    mileage_unit = models.CharField(max_length=2)
    deposit_required = models.BooleanField()
    deposit_amount = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    deposit_hold_days = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "tariff_vehicle_terms"
        constraints = [
            models.UniqueConstraint(
                fields=["tariff", "vehicle_card"], name="tariff_vehicle_terms_uniq"
            ),
            models.CheckConstraint(
                condition=(
                    Q(mileage_mode="unlimited")
                    & Q(included_per_unit__isnull=True)
                    & Q(overage_price__isnull=True)
                )
                | (
                    Q(mileage_mode="included") & Q(included_per_unit__gt=0) & Q(overage_price__gt=0)
                ),
                name="tariff_vehicle_terms_mileage_consistent",
            ),
            models.CheckConstraint(
                condition=(Q(deposit_required=True) & Q(deposit_amount__gt=0))
                | (
                    Q(deposit_required=False)
                    & Q(deposit_amount__isnull=True)
                    & Q(deposit_hold_days__isnull=True)
                ),
                name="tariff_vehicle_terms_deposit_consistent",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tariff_id}:{self.vehicle_card_id}"


class Rental(models.Model):
    """A rental of a vehicle (ТЗ гл. 14). Stage 1: issue without a booking.

    `actual_period` is [actual_start, actual_end); open while the vehicle is with the client. The
    database excludes two non-cancelled rentals of one vehicle with overlapping periods, so two
    concurrent issues cannot both succeed (§ 14.6, acceptance criterion 5). Links to the order,
    vehicle, client, tariff and location are bound to the same company by composite foreign keys.
    """

    class State(models.TextChoices):
        BOOKED = "booked"
        ACTIVE = "active"
        RETURNED = "returned"
        CANCELLED = "cancelled"
        ANNULLED = "annulled"

    class PlaceKind(models.TextChoices):
        LOCATION = "location"
        OTHER = "other"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, related_name="+"
    )
    client = models.ForeignKey("parties.ClientCard", on_delete=models.PROTECT, related_name="+")
    tariff = models.ForeignKey(Tariff, on_delete=models.PROTECT, related_name="+")
    tariff_snapshot = models.JSONField()
    calc_timezone = models.CharField(max_length=64)
    issue_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    issue_place_kind = models.CharField(max_length=10, choices=PlaceKind.choices)
    issue_place_note = models.CharField(max_length=500, blank=True, default="")
    planned_end = models.DateTimeField(null=True, blank=True)
    # Any location of the company may be named at issue, without rights there (decision 22.10).
    planned_return_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    return_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    return_place_kind = models.CharField(
        max_length=10, choices=PlaceKind.choices, blank=True, default=""
    )
    return_place_note = models.CharField(max_length=500, blank=True, default="")
    actual_start = models.DateTimeField()
    actual_end = models.DateTimeField(null=True, blank=True)
    actual_period = DateTimeRangeField()
    state = models.CharField(max_length=10, choices=State.choices)
    formalization_completed_at = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "rental"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="rental_company_id_uniq"),
            ExclusionConstraint(
                name="rental_vehicle_no_overlap",
                expressions=[
                    ("vehicle_card", RangeOperators.EQUAL),
                    ("actual_period", RangeOperators.OVERLAPS),
                ],
                condition=Q(state__in=["active", "returned"]),
            ),
            models.CheckConstraint(
                condition=Q(issue_place_kind="location") | ~Q(issue_place_note=""),
                name="rental_other_place_described",
            ),
            models.CheckConstraint(
                condition=Q(actual_end__isnull=True) | Q(actual_end__gt=models.F("actual_start")),
                name="rental_end_after_start",
            ),
            models.CheckConstraint(
                condition=~Q(state="active") | Q(actual_end__isnull=True),
                name="rental_active_has_no_end",
            ),
            # § 14.5.1: a returned rental has its actual end and place of acceptance.
            models.CheckConstraint(
                condition=~Q(state="returned")
                | (
                    Q(actual_end__isnull=False)
                    & Q(return_location__isnull=False)
                    & ~Q(return_place_kind="")
                ),
                name="rental_returned_has_end_and_place",
            ),
        ]

    def __str__(self) -> str:
        return f"rental {self.id}"
