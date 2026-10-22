"""Inspection records (ТЗ § 10.1, § 14.3.1)."""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company, Location
from c4rsys.platform.ids import uuid7


class Inspection(models.Model):
    """Odometer and fuel are stored here even when no inspection was required (§ 14.3.1).
    A saved inspection is immutable; an error is handled by marking it invalid with a reason
    (§ 10.1.6) — a database trigger forbids any other change (migration 0002)."""

    class Kind(models.TextChoices):
        ISSUE = "issue"
        RETURN = "return"
        OUT_OF_RENTAL = "out_of_rental"
        INTERMEDIATE = "intermediate"

    class State(models.TextChoices):
        DRAFT = "draft"
        SAVED = "saved"
        INVALID = "invalid"

    class CheckStatus(models.TextChoices):
        """§ 10.1.5: the performer is not the same as the check having been done."""

        CONDUCTED = "conducted"
        NOT_REQUIRED = "not_required"
        REQUIRED_NOT_DONE = "required_not_done"

    class PlaceKind(models.TextChoices):
        LOCATION = "location"
        OTHER = "other"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, related_name="+"
    )
    rental = models.ForeignKey(
        "rentals.Rental", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    state = models.CharField(max_length=10, choices=State.choices)
    check_status = models.CharField(max_length=20, choices=CheckStatus.choices)
    performed_at = models.DateTimeField()
    place_kind = models.CharField(max_length=10, choices=PlaceKind.choices)
    place_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    place_note = models.CharField(max_length=500, blank=True, default="")
    performer_employment_id = models.UUIDField()
    author_user_id = models.UUIDField()
    entered_at = models.DateTimeField(default=now)
    odometer_value = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True)
    odometer_unit = models.CharField(max_length=2)
    fuel_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    saved_at = models.DateTimeField(null=True, blank=True)
    invalidated_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "inspection"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="inspection_company_id_uniq"),
            models.CheckConstraint(
                condition=Q(state="draft") | Q(saved_at__isnull=False),
                name="inspection_saved_has_time",
            ),
            models.CheckConstraint(
                condition=Q(place_kind="location") | ~Q(place_note=""),
                name="inspection_other_place_described",
            ),
            models.CheckConstraint(
                condition=Q(odometer_value__isnull=True) | Q(odometer_value__gte=0),
                name="inspection_odometer_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(fuel_value__isnull=True) | Q(fuel_value__gte=0),
                name="inspection_fuel_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} inspection {self.id}"
