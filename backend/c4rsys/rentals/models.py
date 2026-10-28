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
    """A rental of a vehicle (ТЗ гл. 14): booked in advance (§ 14.1) or issued at once.

    A booking holds a concrete vehicle for its planned period; until the issue the client, the
    tariff and the issue location may be empty (§ 14.1.8–14.1.9). The issue turns the same row into
    an active rental (§ 14.4.1). Bookings of one vehicle never overlap (`booking_period`).

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
    client = models.ForeignKey(
        "parties.ClientCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    tariff = models.ForeignKey(
        Tariff, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    tariff_snapshot = models.JSONField(null=True, blank=True)
    calc_timezone = models.CharField(max_length=64)
    issue_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    issue_place_kind = models.CharField(
        max_length=10, choices=PlaceKind.choices, blank=True, default=""
    )
    issue_place_note = models.CharField(max_length=500, blank=True, default="")
    # «Other» place of the issue (§ 14.2.1): address, coordinates (also from the device's
    # geolocation) or a comment; it belongs to the issue location (customer's decision В1).
    issue_place_address = models.CharField(max_length=500, blank=True, default="")
    issue_place_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    issue_place_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
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
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)
    actual_period = DateTimeRangeField(null=True, blank=True)
    # Booking (§ 14.1): own number inside the company (customer's decision of 26.10.2026), the
    # planned start and the period it holds.
    booking_number = models.PositiveIntegerField(null=True, blank=True)
    planned_start = models.DateTimeField(null=True, blank=True)
    booking_period = DateTimeRangeField(null=True, blank=True)
    booking_contact = models.CharField(max_length=200, blank=True, default="")
    booking_comment = models.CharField(max_length=1000, blank=True, default="")
    # Auto-cancel at no-show (§ 14.8.1): the rule applied at creation and its moment.
    auto_cancel_rule = models.JSONField(null=True, blank=True)
    auto_cancel_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=500, blank=True, default="")
    # The deposit of this rental changed or cancelled before the issue without changing the
    # tariff (§ 14.7.3): {"required", "amount", "reason"}.
    deposit_override = models.JSONField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices)
    formalization_completed_at = models.DateTimeField(null=True, blank=True)
    # § 13.6.5: reopened for a correction; its actual period and return place may then change
    # until the formalization is completed again (decision П11).
    reopened_at = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "rental"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="rental_company_id_uniq"),
            ExclusionConstraint(
                name="rental_booking_no_overlap",
                expressions=[
                    ("vehicle_card", RangeOperators.EQUAL),
                    ("booking_period", RangeOperators.OVERLAPS),
                ],
                condition=Q(state="booked"),
            ),
            models.UniqueConstraint(
                fields=["company", "booking_number"],
                condition=Q(booking_number__isnull=False),
                name="rental_booking_number_uniq",
            ),
            # § 14.4.4: an issued rental has its client, tariff, issue location and start.
            models.CheckConstraint(
                condition=~Q(state__in=["active", "returned"])
                | (
                    Q(client__isnull=False)
                    & Q(tariff__isnull=False)
                    & Q(tariff_snapshot__isnull=False)
                    & Q(issue_location__isnull=False)
                    & ~Q(issue_place_kind="")
                    & Q(actual_start__isnull=False)
                    & Q(actual_period__isnull=False)
                ),
                name="rental_issued_complete",
            ),
            # § 14.1.9: a booking has a vehicle and planned start and end.
            models.CheckConstraint(
                condition=~Q(state="booked")
                | (
                    Q(planned_start__isnull=False)
                    & Q(planned_end__isnull=False)
                    & Q(booking_period__isnull=False)
                    & Q(planned_end__gt=models.F("planned_start"))
                ),
                name="rental_booking_complete",
            ),
            ExclusionConstraint(
                name="rental_vehicle_no_overlap",
                expressions=[
                    ("vehicle_card", RangeOperators.EQUAL),
                    ("actual_period", RangeOperators.OVERLAPS),
                ],
                condition=Q(state__in=["active", "returned"]),
            ),
            models.CheckConstraint(
                condition=Q(issue_place_kind__in=["location", ""])
                | ~Q(issue_place_note="")
                | ~Q(issue_place_address="")
                | Q(issue_place_lat__isnull=False),
                name="rental_other_place_described",
            ),
            models.CheckConstraint(
                condition=Q(issue_place_lat__isnull=True, issue_place_lng__isnull=True)
                | Q(
                    issue_place_lat__gte=-90,
                    issue_place_lat__lte=90,
                    issue_place_lng__gte=-180,
                    issue_place_lng__lte=180,
                ),
                name="rental_issue_place_coordinates",
            ),
            models.CheckConstraint(
                condition=Q(actual_end__isnull=True)
                | Q(actual_start__isnull=True)
                | Q(actual_end__gt=models.F("actual_start")),
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


class RentalCalculation(models.Model):
    """An immutable control version of the rental calculation, kept at payment and closing
    (§ 13.6.8). Recalculations while typing do not create versions. Append-only (trigger)."""

    class Reason(models.TextChoices):
        PAYMENT = "payment"
        CLOSING = "closing"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    rental = models.ForeignKey(Rental, on_delete=models.PROTECT, related_name="calculations")
    seq = models.PositiveIntegerField()
    reason = models.CharField(max_length=20, choices=Reason.choices)
    kind = models.CharField(max_length=10)  # current or actual (§ 13.6.7)
    inputs = models.JSONField()
    billable_minutes = models.PositiveIntegerField()
    units = models.PositiveIntegerField()
    rate = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    base_amount = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.CharField(max_length=3)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "rental_calculation"
        constraints = [
            models.UniqueConstraint(fields=["rental", "seq"], name="rental_calculation_seq_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.reason} #{self.seq}: {self.base_amount} {self.currency}"


class RentalPolicy(models.Model):
    """Auto-cancel of a booking at no-show (§ 14.8.1): off by default; turned on for the whole
    company (location empty) or for a rental location, with the wait after the planned start.
    A booking keeps the rule applied at its creation; a later change acts on new bookings."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    auto_cancel_enabled = models.BooleanField(default=False)
    auto_cancel_after_minutes = models.PositiveIntegerField(null=True, blank=True)
    updated_by_user_id = models.UUIDField()
    updated_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "rental_policy"
        constraints = [
            models.UniqueConstraint(
                fields=["company"],
                condition=Q(location__isnull=True),
                name="rental_policy_company_uniq",
            ),
            models.UniqueConstraint(
                fields=["company", "location"],
                condition=Q(location__isnull=False),
                name="rental_policy_location_uniq",
            ),
            models.CheckConstraint(
                condition=Q(auto_cancel_enabled=False) | Q(auto_cancel_after_minutes__isnull=False),
                name="rental_policy_wait_set",
            ),
        ]

    def __str__(self) -> str:
        return f"policy {self.company_id}:{self.location_id}"


class IssuePolicy(models.Model):
    """Policies of the issue (§ 14.3.2, § 14.3.5, § 14.3.6): for the company (location empty) or
    a rental location with its own setting (customer's decision В2). Without a row a new company
    has the non-blocking start policies. Insufficient deposit is never a policy (§ 14.3.3)."""

    class Debt(models.TextChoices):
        ALLOW = "allow"
        WARN = "warn"
        DECIDE = "decide"  # a decision of an authorized employee is required

    class Payment(models.TextChoices):
        WARN = "warn"
        REQUIRE = "require"

    class Documents(models.TextChoices):
        NONE = "none"
        REQUIRE = "require"

    class Inspection(models.TextChoices):
        NOT_REQUIRED = "not_required"
        REQUIRED = "required"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    debt = models.CharField(max_length=10, choices=Debt.choices, default=Debt.WARN)
    payment = models.CharField(max_length=10, choices=Payment.choices, default=Payment.WARN)
    documents = models.CharField(max_length=10, choices=Documents.choices, default=Documents.NONE)
    inspection = models.CharField(
        max_length=15, choices=Inspection.choices, default=Inspection.NOT_REQUIRED
    )
    updated_by_user_id = models.UUIDField()
    updated_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "rental_issue_policy"
        constraints = [
            models.UniqueConstraint(
                fields=["company"],
                condition=Q(location__isnull=True),
                name="rental_issue_policy_company_uniq",
            ),
            models.UniqueConstraint(
                fields=["company", "location"],
                condition=Q(location__isnull=False),
                name="rental_issue_policy_location_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"issue policy {self.company_id}:{self.location_id}"


class IssueException(models.Model):
    """An exception of the issue (§ 14.3.7): permitted in advance with a reason by an authorized
    employee, or a transfer already made recorded by the issuing employee, which then waits for a
    decision. Neither removes the unmet requirement; a rejection marks a violation and does not
    undo the issue. One per rental: one issue (customer's decision В6)."""

    class Mode(models.TextChoices):
        ADVANCE = "advance"
        AFTER_FACT = "after_fact"

    class State(models.TextChoices):
        GRANTED = "granted"  # advance permission, the issue has not happened yet
        APPLIED = "applied"  # advance permission used by the issue
        PENDING = "pending"  # «exception awaits confirmation»
        CONFIRMED = "confirmed"
        REJECTED = "rejected"  # a violation

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    rental = models.ForeignKey(Rental, on_delete=models.PROTECT, related_name="+")
    mode = models.CharField(max_length=10, choices=Mode.choices)
    state = models.CharField(max_length=10, choices=State.choices)
    # Unmet requirements it covers: "payment", "debt", "documents".
    requirements = models.JSONField()
    reason = models.CharField(max_length=500)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    decision_reason = models.CharField(max_length=500, blank=True, default="")
    decided_by_user_id = models.UUIDField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "rental_issue_exception"
        constraints = [
            models.UniqueConstraint(fields=["rental"], name="rental_issue_exception_one"),
            models.CheckConstraint(
                condition=Q(mode="advance", state__in=["granted", "applied"])
                | Q(mode="after_fact", state__in=["pending", "confirmed", "rejected"]),
                name="rental_issue_exception_state_of_mode",
            ),
            models.CheckConstraint(condition=~Q(reason=""), name="rental_issue_exception_reason"),
            models.CheckConstraint(
                condition=Q(state__in=["granted", "applied", "pending"])
                | (~Q(decision_reason="") & Q(decided_at__isnull=False)),
                name="rental_issue_exception_decided",
            ),
        ]

    def __str__(self) -> str:
        return f"issue exception {self.rental_id}: {self.state}"


class RentalFreeInterval(models.Model):
    """Free time entered by hand in an issued rental (§ 13.3.2): it reduces the billable time,
    the vehicle stays occupied. Given by its end or by a number of tariff units (`units`); added,
    changed and removed with history by the rental's rights (customer's decisions Т2–Т4)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    rental = models.ForeignKey(Rental, on_delete=models.PROTECT, related_name="free_intervals")
    start = models.DateTimeField()
    end = models.DateTimeField()
    units = models.PositiveIntegerField(null=True, blank=True)
    comment = models.CharField(max_length=500, blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "rental_free_interval"
        constraints = [
            models.CheckConstraint(
                condition=Q(end__gt=models.F("start")), name="rental_free_interval_not_empty"
            ),
            models.CheckConstraint(
                condition=Q(units__isnull=True) | Q(units__gte=1),
                name="rental_free_interval_units_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"free {self.start}–{self.end}"


class RentalManualCharge(models.Model):
    """A manual charge in a rental (§ 13.5.1): fuel or charge, or another one, apart from the
    base cost; in the currency of the rental's tariff with a reason. Cancelled by a reversing
    charge entry with a reason, never deleted (customer's decision Т6)."""

    class Kind(models.TextChoices):
        FUEL = "fuel"
        # Decision П10: an early return or a delay is charged only by hand, with its reason.
        EARLY_RETURN = "early_return"
        OVERDUE = "overdue"
        OTHER = "other"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    rental = models.ForeignKey(Rental, on_delete=models.PROTECT, related_name="manual_charges")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=500)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by_user_id = models.UUIDField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        db_table = "rental_manual_charge"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="rental_manual_charge_positive"),
            models.CheckConstraint(condition=~Q(reason=""), name="rental_manual_charge_reason"),
            models.CheckConstraint(
                condition=Q(cancelled_at__isnull=True) | ~Q(cancel_reason=""),
                name="rental_manual_charge_cancel_reason",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.amount} {self.currency_id}"


class ExtraService(models.Model):
    """An additional service of the rental catalog (§ 13.4.5–13.4.7): paid once, by quantity or by
    duration. Available to the whole company or selected locations, optionally limited to vehicles
    and tariffs (an empty list: no limit). Its price is in its own currency (customer's decision
    У2); the pricing, unit and currency do not change after creation. An added service keeps a
    snapshot (§ 13.4.8). Equipment by instance comes later (decision Т1)."""

    class Pricing(models.TextChoices):
        ONCE = "once"
        QUANTITY = "quantity"
        TIME = "time"

    class Unit(models.TextChoices):
        # Decisions Т7 and У12: a 24-hour day from the start of the period, no settlement days.
        MINUTE = "minute"
        HOUR = "hour"
        DAY = "day"
        MONTH = "month"

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=150)
    pricing = models.CharField(max_length=10, choices=Pricing.choices)
    unit = models.CharField(max_length=10, choices=Unit.choices, blank=True, default="")
    price = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    all_locations = models.BooleanField(default=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "rental_extra_service"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="rental_extra_service_company_id_uniq"
            ),
            models.CheckConstraint(condition=~Q(name=""), name="rental_extra_service_name"),
            models.CheckConstraint(condition=Q(price__gt=0), name="rental_extra_service_price"),
            models.CheckConstraint(
                condition=(Q(pricing="time") & ~Q(unit="")) | (~Q(pricing="time") & Q(unit="")),
                name="rental_extra_service_unit_of_time",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ExtraServiceLocation(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    service = models.ForeignKey(ExtraService, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "rental_extra_service_location"
        constraints = [
            models.UniqueConstraint(
                fields=["service", "location"], name="rental_extra_service_location_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.service_id}@{self.location_id}"


class ExtraServiceVehicle(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    service = models.ForeignKey(ExtraService, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "rental_extra_service_vehicle"
        constraints = [
            models.UniqueConstraint(
                fields=["service", "vehicle_card"], name="rental_extra_service_vehicle_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.service_id}:{self.vehicle_card_id}"


class ExtraServiceTariff(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    service = models.ForeignKey(ExtraService, on_delete=models.PROTECT, related_name="+")
    tariff = models.ForeignKey(Tariff, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "rental_extra_service_tariff"
        constraints = [
            models.UniqueConstraint(
                fields=["service", "tariff"], name="rental_extra_service_tariff_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.service_id}:{self.tariff_id}"


class ExtraServiceLine(models.Model):
    """An additional service added to an order (§ 12.1.4, § 13.4.5): each addition is its own
    line with a snapshot of the price and terms (§ 13.4.8).

    Linked to a rental (`rental`), it has the rental's period and follows its issue and return
    (customer's decisions У4–У5); the state stored then is «planned» and the shown one comes from
    the rental. Standalone, it has its own location of the service (§ 13.4.14), its own period for
    a time service and its own actual start and completion (§ 13.4.11). Unlinking changes the link
    of the same line (§ 13.4.9); `origin_rental` keeps the rental it was linked to."""

    class State(models.TextChoices):
        PLANNED = "planned"
        STARTED = "started"
        COMPLETED = "completed"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="+")
    service = models.ForeignKey(ExtraService, on_delete=models.PROTECT, related_name="+")
    pricing = models.CharField(max_length=10, choices=ExtraService.Pricing.choices)
    snapshot = models.JSONField()
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    rental = models.ForeignKey(
        Rental, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    origin_rental = models.ForeignKey(
        Rental, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    quantity = models.PositiveIntegerField(null=True, blank=True)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PLANNED)
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)
    unlinked_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=500, blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "rental_extra_service_line"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="rental_extra_service_line_company_id_uniq"
            ),
            # A standalone service has its location; a linked one takes the rental's.
            models.CheckConstraint(
                condition=Q(rental__isnull=False) | Q(location__isnull=False),
                name="rental_extra_service_line_location",
            ),
            models.CheckConstraint(
                condition=(Q(pricing="quantity") & Q(quantity__gte=1))
                | (~Q(pricing="quantity") & Q(quantity__isnull=True)),
                name="rental_extra_service_line_quantity",
            ),
            # § 13.4.9: a linked service has no period of its own; a standalone time service has.
            models.CheckConstraint(
                condition=(
                    Q(rental__isnull=False)
                    & Q(period_start__isnull=True)
                    & Q(period_end__isnull=True)
                )
                | (
                    Q(rental__isnull=True, pricing="time")
                    & Q(period_start__isnull=False)
                    & Q(period_end__gt=models.F("period_start"))
                )
                | (
                    Q(rental__isnull=True)
                    & ~Q(pricing="time")
                    & Q(period_start__isnull=True)
                    & Q(period_end__isnull=True)
                ),
                name="rental_extra_service_line_period",
            ),
            models.CheckConstraint(
                condition=Q(rental__isnull=True) | Q(state="planned"),
                name="rental_extra_service_line_linked_state",
            ),
            models.CheckConstraint(
                condition=~Q(state__in=["started", "completed"]) | Q(actual_start__isnull=False),
                name="rental_extra_service_line_started",
            ),
            models.CheckConstraint(
                condition=~Q(state="completed")
                | (Q(actual_end__isnull=False) & Q(actual_end__gte=models.F("actual_start"))),
                name="rental_extra_service_line_completed",
            ),
            models.CheckConstraint(
                condition=~Q(state="cancelled")
                | (Q(cancelled_at__isnull=False) & ~Q(cancel_reason="")),
                name="rental_extra_service_line_cancelled",
            ),
        ]

    def __str__(self) -> str:
        return f"service line {self.id}"


class ExtraServiceFreeInterval(models.Model):
    """Free time of a time service (§ 13.4.6), entered by hand by the rules of § 13.3.2
    (customer's decision У9)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    line = models.ForeignKey(
        ExtraServiceLine, on_delete=models.PROTECT, related_name="free_intervals"
    )
    start = models.DateTimeField()
    end = models.DateTimeField()
    units = models.PositiveIntegerField(null=True, blank=True)
    comment = models.CharField(max_length=500, blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "rental_extra_service_free_interval"
        constraints = [
            models.CheckConstraint(
                condition=Q(end__gt=models.F("start")),
                name="rental_extra_service_free_not_empty",
            ),
            models.CheckConstraint(
                condition=Q(units__isnull=True) | Q(units__gte=1),
                name="rental_extra_service_free_units",
            ),
        ]

    def __str__(self) -> str:
        return f"free {self.start}–{self.end}"
