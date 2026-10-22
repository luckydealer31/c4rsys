"""Vehicles (ТЗ § 8.1, § 8.2.9, § 8.2.12, § 8.5.1)."""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company, Location
from c4rsys.platform.ids import uuid7


class Vehicle(models.Model):
    """Global record of a vehicle with its c4rsys asset ID (§ 8.1.14). It belongs to no company;
    companies work with it through their own cards."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    public_id = models.CharField(max_length=40, unique=True)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle"

    def __str__(self) -> str:
        return self.public_id


class VehicleCard(models.Model):
    """Working card of a company for a global vehicle (§ 8.1.6). Own and managed vehicles are
    bound to an accounting location (§ 8.1.3); `accounting_location` is additionally bound to
    the same company by a composite foreign key (migration 0002)."""

    class Relation(models.TextChoices):
        OWN = "own"
        MANAGED = "managed"
        CLIENT = "client"

    class OdometerUnit(models.TextChoices):
        KM = "km"
        MI = "mi"

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="cards")
    name = models.CharField(max_length=200)
    relation = models.CharField(max_length=10, choices=Relation.choices)
    accounting_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    odometer_unit = models.CharField(max_length=2, choices=OdometerUnit.choices)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "vehicle_card"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="vehicle_card_company_id_uniq"),
            models.UniqueConstraint(
                fields=["company", "vehicle"], name="vehicle_card_company_vehicle_uniq"
            ),
            models.CheckConstraint(condition=~Q(name=""), name="vehicle_card_name_not_empty"),
            models.CheckConstraint(
                condition=Q(relation="client") | Q(accounting_location__isnull=False),
                name="vehicle_card_operational_has_location",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class VehicleCardContext(models.Model):
    """Context type the vehicle may be used in (§ 8.5.1)."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="contexts")
    context_type = models.CharField(max_length=20)

    class Meta:
        db_table = "vehicle_card_context"
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle_card", "context_type"], name="vehicle_card_context_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vehicle_card_id}:{self.context_type}"


class OdometerReading(models.Model):
    """A reading with the moment of the actual measurement; the moment of entry is kept apart
    (§ 8.2.12). The current reading is the latest measurement, not the latest entry."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="+")
    measured_at = models.DateTimeField()
    entered_at = models.DateTimeField(default=now)
    entered_by_user_id = models.UUIDField()
    value = models.DecimalField(max_digits=12, decimal_places=1)
    unit = models.CharField(max_length=2, choices=VehicleCard.OdometerUnit.choices)
    source_type = models.CharField(max_length=30)
    source_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "odometer_reading"
        constraints = [
            models.CheckConstraint(condition=Q(value__gte=0), name="odometer_reading_not_negative"),
        ]
        indexes = [
            models.Index(fields=["vehicle_card", "measured_at"], name="odometer_reading_card_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.value} {self.unit} @ {self.measured_at:%Y-%m-%d %H:%M}"
