"""Vehicles (ТЗ § 8.1, § 8.2.9, § 8.2.12, § 8.5.1)."""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import ActivityContext, Company, Location
from c4rsys.platform.ids import uuid7


class VehicleMake(models.Model):
    """Catalog of makes loaded with the system (§ 8.1.11); for passenger cars — the customer's
    catalog `catalog/cars.csv` (24.10.2026). A value missing here is entered as «Other» text and
    does not create a make."""

    code = models.CharField(max_length=60, primary_key=True)
    vehicle_type = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    name_cyrillic = models.CharField(max_length=100, blank=True, default="")
    popular = models.BooleanField(default=False)
    country = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        db_table = "vehicle_make"

    def __str__(self) -> str:
        return self.name


class VehicleModel(models.Model):
    """Catalog of models of a make (§ 8.1.11)."""

    code = models.CharField(max_length=120, primary_key=True)
    make = models.ForeignKey(VehicleMake, on_delete=models.PROTECT, related_name="models")
    name = models.CharField(max_length=150)
    name_cyrillic = models.CharField(max_length=150, blank=True, default="")
    car_class = models.CharField(max_length=5, blank=True, default="")
    year_from = models.PositiveSmallIntegerField(null=True, blank=True)
    year_to = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "vehicle_model"

    def __str__(self) -> str:
        return self.name


class Vehicle(models.Model):
    """Global record of a vehicle with its c4rsys asset ID (§ 8.1.14). It belongs to no company;
    companies work with it through their own cards. Identification data and characteristics of
    § 8.1.8–8.1.12 are all optional; values outside the catalog are kept as text."""

    class TransportClass(models.TextChoices):
        GROUND = "ground"  # the only class of the first launch (§ 8.1.8)
        AIR = "air"
        WATER = "water"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    public_id = models.CharField(max_length=40, unique=True)
    created_at = models.DateTimeField(default=now)
    transport_class = models.CharField(
        max_length=10, choices=TransportClass.choices, default=TransportClass.GROUND
    )
    vehicle_type = models.CharField(max_length=30, blank=True, default="")
    make = models.ForeignKey(
        VehicleMake, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    make_other = models.CharField(max_length=100, blank=True, default="")
    model = models.ForeignKey(
        VehicleModel, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    model_other = models.CharField(max_length=150, blank=True, default="")
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    # Compared globally after upper-casing and trimming (§ 8.6.1).
    vin = models.CharField(max_length=40, blank=True, default="", db_index=True)
    color = models.CharField(max_length=60, blank=True, default="")
    body_type = models.CharField(max_length=30, blank=True, default="")
    fuel_type = models.CharField(max_length=30, blank=True, default="")
    transmission = models.CharField(max_length=30, blank=True, default="")
    drive = models.CharField(max_length=30, blank=True, default="")
    engine_volume_l = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    power_hp = models.PositiveSmallIntegerField(null=True, blank=True)
    seats = models.PositiveSmallIntegerField(null=True, blank=True)
    doors = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "vehicle"

    def __str__(self) -> str:
        return self.public_id


class VehiclePlate(models.Model):
    """A registration plate with its country and period (§ 8.2.11). A plate is not globally
    unique; a match in the same country is a reason to check, not proof (§ 8.6.1)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="plates")
    number = models.CharField(max_length=20)
    number_normalized = models.CharField(max_length=20, db_index=True)
    country = models.CharField(max_length=2, blank=True, default="")
    valid_from = models.DateTimeField(default=now)
    valid_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "vehicle_plate"
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=Q(valid_to__isnull=True),
                name="vehicle_plate_one_current",
            ),
        ]

    def __str__(self) -> str:
        return self.number


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
    # An own vehicle transferred to a warehouse is an individual stock item of a storage context
    # (§ 8.1.5, § 8.1.7); it keeps its ID and history. Empty — the vehicle is in the garage.
    storage_context = models.ForeignKey(
        ActivityContext, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    stored_at = models.DateTimeField(null=True, blank=True)
    # The cost of acquisition of an own vehicle, for its payback (§ 21.4.2; decision О-3).
    # Empty — unknown, never taken for zero.
    acquisition_cost = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    acquisition_currency = models.ForeignKey(
        "platform.Currency", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Created by an import as a possible duplicate of another card (§ 22.2.3).
    possible_duplicate = models.BooleanField(default=False)
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
            models.CheckConstraint(
                condition=Q(storage_context__isnull=True) | Q(relation="own"),
                name="vehicle_card_only_own_in_storage",
            ),
            models.CheckConstraint(
                condition=Q(acquisition_cost__isnull=True, acquisition_currency__isnull=True)
                | Q(relation="own", acquisition_cost__gte=0, acquisition_currency__isnull=False),
                name="vehicle_card_acquisition_of_own",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class VehicleOwnerClaim(models.Model):
    """The owner as declared by the company (§ 8.3.13). With «own» the company itself is the
    declared owner; with «managed» the owner is a profile found by ID, email or phone, an owner
    described by hand, or not given yet. The data stay unconfirmed until the owner accepts the
    request of § 8.3.1. A new declaration is a new row; the latest one is current.

    `owner_user_id` is resolved silently from the lookup: the company is not told whether such
    a profile exists, nor its name (§ 2.2.3)."""

    class Kind(models.TextChoices):
        COMPANY = "company"
        PROFILE = "profile"
        PERSON = "person"
        ORGANIZATION = "organization"
        CLIENT = "client"  # a client of the company, for a client vehicle (§ 8.1.2)
        NONE = "none"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(
        "VehicleCard", on_delete=models.PROTECT, related_name="owner_claims"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    lookup = models.CharField(max_length=254, blank=True, default="")
    owner_user_id = models.UUIDField(null=True, blank=True)
    client = models.ForeignKey(
        "parties.ClientCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    manual_name = models.CharField(max_length=300, blank=True, default="")
    manual_contact = models.CharField(max_length=254, blank=True, default="")
    created_at = models.DateTimeField(default=now)
    created_by_user_id = models.UUIDField()

    class Meta:
        db_table = "vehicle_owner_claim"

    def __str__(self) -> str:
        return f"{self.kind}:{self.vehicle_card_id}"


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


class VehicleMove(models.Model):
    """An internal move A → B recorded after it happened in one action (§ 8.5.2, § 15.1.3,
    § 15.1.5, § 23.6.2; decision 23.10.2026). It belongs to an internal order of the company.
    Planning, «in transit», expenses and delivery to clients come later.

    Links are additionally bound to the same company by composite foreign keys (migration 0004).
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="moves")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="+")
    from_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    to_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    driver_employment = models.ForeignKey(
        "companies.Employment", on_delete=models.PROTECT, related_name="+"
    )
    actual_start = models.DateTimeField()
    actual_end = models.DateTimeField()
    note = models.CharField(max_length=500, blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_move"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="vehicle_move_company_id_uniq"),
            models.CheckConstraint(
                condition=Q(actual_end__gt=models.F("actual_start")),
                name="vehicle_move_period_valid",
            ),
            models.CheckConstraint(
                condition=~Q(from_location=models.F("to_location")),
                name="vehicle_move_other_location",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vehicle_card_id}: {self.from_location_id} → {self.to_location_id}"
