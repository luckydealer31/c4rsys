"""Vehicles (ТЗ § 8.1, § 8.2.9, § 8.2.12, § 8.5.1)."""

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
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
    # Numbers of the body, chassis and frame (§ 8.1.12; customer's decision of 30.10.2026),
    # compared each within its own kind (§ 8.6.1).
    body_number = models.CharField(max_length=40, blank=True, default="", db_index=True)
    chassis_number = models.CharField(max_length=40, blank=True, default="", db_index=True)
    frame_number = models.CharField(max_length=40, blank=True, default="", db_index=True)
    # The engine number (§ 8.1.12; customer's decision of 30.10.2026). § 8.6.1 does not compare
    # it with other vehicles.
    engine_number = models.CharField(max_length=40, blank=True, default="")
    color = models.CharField(max_length=60, blank=True, default="")
    body_type = models.CharField(max_length=30, blank=True, default="")
    fuel_type = models.CharField(max_length=30, blank=True, default="")
    transmission = models.CharField(max_length=30, blank=True, default="")
    drive = models.CharField(max_length=30, blank=True, default="")
    engine_volume_l = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    power_hp = models.PositiveSmallIntegerField(null=True, blank=True)
    seats = models.PositiveSmallIntegerField(null=True, blank=True)
    doors = models.PositiveSmallIntegerField(null=True, blank=True)
    # The rest of the optional characteristics of § 8.1.12 (block 12, part Б).
    manufacturer = models.CharField(max_length=150, blank=True, default="")
    manufacture_country = models.CharField(max_length=2, blank=True, default="")
    modification = models.CharField(max_length=150, blank=True, default="")
    tank_volume_l = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    battery_kwh = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    # The declared range in kilometres (PROPOSAL: the unit of a range is not the odometer's).
    range_km = models.PositiveIntegerField(null=True, blank=True)
    gears = models.PositiveSmallIntegerField(null=True, blank=True)
    curb_weight_kg = models.PositiveIntegerField(null=True, blank=True)
    gross_weight_kg = models.PositiveIntegerField(null=True, blank=True)
    payload_kg = models.PositiveIntegerField(null=True, blank=True)
    cargo_volume_m3 = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    # Factory characteristics confirmed by the c4rsys staff (§ 8.2.1): not changed by a direct
    # edit of a company; the history of each change is `VehicleCharacteristicEvent`.
    confirmed_fields = models.JSONField(default=list, blank=True)
    # A system duplicate linked to the main entity after the verification found one physical
    # vehicle (§ 8.6.2): its ID stays as an archive reference, its history is not deleted.
    merged_into = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    merged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "vehicle"
        constraints = [
            models.CheckConstraint(
                condition=Q(merged_into__isnull=True) | ~Q(merged_into=models.F("id")),
                name="vehicle_not_merged_into_itself",
            ),
        ]

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
    # The region or the registration authority, where it applies (§ 8.6.1).
    region = models.CharField(max_length=60, blank=True, default="")
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
        # A duplicate merged into the main card of the company (§ 8.6.6): closed for new
        # operations, redirects to the main card; its ID is kept and never reused.
        MERGED = "merged"

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
    merged_into = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    merged_at = models.DateTimeField(null=True, blank=True)
    # The inventory number and the barcode / QR of § 8.1.12 are labels of the company, kept in
    # its card (PROPOSAL).
    inventory_number = models.CharField(max_length=60, blank=True, default="")
    barcode = models.CharField(max_length=120, blank=True, default="")
    # The archive of the card (§ 8.7.5): who, when and why; a sale or a write-off archives it too.
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by_user_id = models.UUIDField(null=True, blank=True)
    archive_reason = models.CharField(max_length=1000, blank=True, default="")
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
            models.CheckConstraint(
                condition=Q(state="merged", merged_into__isnull=False)
                | (~Q(state="merged") & Q(merged_into__isnull=True)),
                name="vehicle_card_merged_has_main",
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
    # «ownership_change» — declared by the system when the ownership passed to another owner
    # (§ 8.3.10): the company does not learn who the new owner is.
    source = models.CharField(max_length=20, blank=True, default="")
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
    # A wrong reading is not changed: the corrected one replaces it and both stay (§ 16.8.2).
    replaced_by = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

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


class VehicleOwnerRequest(models.Model):
    """A request to the declared owner of a managed vehicle (§ 8.3.1, § 8.3.5, § 8.3.13–8.3.14).

    It is sent to the profile of the current declaration: a person by USR ID, email or phone, a
    company by its CMP ID. The three answers are kept apart (`VehicleOwnerAnswer`). A new
    request is allowed after a refusal or an incomplete answer and keeps the earlier answers;
    the earlier request becomes «superseded»."""

    class State(models.TextChoices):
        OPEN = "open"  # waiting for all or for the rest of the answers
        COMPLETE = "complete"
        CANCELLED = "cancelled"
        SUPERSEDED = "superseded"
        EXPIRED = "expired"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(
        VehicleCard, on_delete=models.PROTECT, related_name="owner_requests"
    )
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    claim = models.ForeignKey(VehicleOwnerClaim, on_delete=models.PROTECT, related_name="+")
    target_label = models.CharField(max_length=254)
    target_user_id = models.UUIDField(null=True, blank=True)
    target_company_id = models.UUIDField(null=True, blank=True)
    target_email = models.CharField(max_length=254, blank=True, default="")
    target_phone = models.CharField(max_length=40, blank=True, default="")
    state = models.CharField(max_length=12, choices=State.choices, default=State.OPEN)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    # Why a request was closed without the company's cancel: another owner was declared.
    closed_reason = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        db_table = "vehicle_owner_request"
        constraints = [
            models.CheckConstraint(
                condition=Q(target_user_id__isnull=False)
                | Q(target_company_id__isnull=False)
                | ~Q(target_email="")
                | ~Q(target_phone=""),
                name="vehicle_owner_request_has_target",
            ),
            models.UniqueConstraint(
                fields=["vehicle_card"],
                condition=Q(state="open"),
                name="vehicle_owner_request_one_open",
            ),
        ]
        indexes = [
            models.Index(fields=["target_user_id", "state"], name="owner_request_user_idx"),
            models.Index(fields=["target_company_id", "state"], name="owner_request_company_idx"),
            models.Index(fields=["target_email", "state"], name="owner_request_email_idx"),
        ]

    def __str__(self) -> str:
        return f"owner request {self.id}"


class VehicleOwnerAnswer(models.Model):
    """One of the three answers of § 8.3.1 with the profile that answered and the moment
    (§ 8.3.14). An answer is never changed; a new decision is a new request."""

    class Question(models.TextChoices):
        LINK = "link"  # the link with the company
        OWNERSHIP = "ownership"  # the vehicle belongs to the owner
        MANAGEMENT = "management"  # consent to the management by this company

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    request = models.ForeignKey(
        VehicleOwnerRequest, on_delete=models.PROTECT, related_name="answers"
    )
    question = models.CharField(max_length=12, choices=Question.choices)
    answer = models.BooleanField()
    user_id = models.UUIDField()
    company_id = models.UUIDField(null=True, blank=True)
    answered_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_owner_answer"
        constraints = [
            models.UniqueConstraint(
                fields=["request", "question"], name="vehicle_owner_answer_once"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.question}={self.answer}"


class VehicleOwnership(models.Model):
    """A confirmed period of ownership of the global vehicle (§ 8.3.8, § 8.3.13, § 8.3.16). In
    the MVP one current owner — a person or a company; the current period has no end."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="ownerships")
    owner_user_id = models.UUIDField(null=True, blank=True)
    owner_company_id = models.UUIDField(null=True, blank=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    # An approximate date of old incomplete history, with its mark (§ 8.3.16).
    started_approx = models.BooleanField(default=False)
    ended_approx = models.BooleanField(default=False)
    request = models.ForeignKey(
        VehicleOwnerRequest, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # How the period began: the answer to a request, a sale through an order (§ 8.3.9), a
    # decision of the support (§ 8.3.12), a return to the seller after a cancelled sale.
    basis = models.CharField(max_length=20, blank=True, default="request")
    order_id = models.UUIDField(null=True, blank=True)
    seller_company_id = models.UUIDField(null=True, blank=True)
    # A transfer cancelled after the new owner confirmed it was wrong (§ 8.3.9).
    reversed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_ownership"
        constraints = [
            models.CheckConstraint(
                condition=Q(owner_user_id__isnull=False, owner_company_id__isnull=True)
                | Q(owner_user_id__isnull=True, owner_company_id__isnull=False),
                name="vehicle_ownership_one_owner",
            ),
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=Q(ended_at__isnull=True),
                name="vehicle_ownership_one_current",
            ),
        ]

    def __str__(self) -> str:
        return f"ownership {self.vehicle_id}"


class VehicleOwnershipDecision(models.Model):
    """The decision of the system verification on a dispute of ownership (§ 8.3.12, § 8.3.16,
    § 24.1.7): «the vehicle is mine» answered while another owner was confirmed. The decision
    keeps the current owner or ends his period and starts the claimant's one; the earlier period
    stays as history with the initiator, the reason and the ticket."""

    class Kind(models.TextChoices):
        KEEP = "keep"
        TRANSFER = "transfer"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    request = models.OneToOneField(
        VehicleOwnerRequest, on_delete=models.PROTECT, related_name="dispute_decision"
    )
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    reason = models.TextField()
    previous_ownership = models.ForeignKey(
        VehicleOwnership, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    new_ownership = models.ForeignKey(
        VehicleOwnership, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    effective_at = models.DateTimeField(null=True, blank=True)
    ticket_id = models.UUIDField(null=True, blank=True)
    decided_by_user_id = models.UUIDField()
    decided_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_ownership_decision"

    def __str__(self) -> str:
        return f"{self.kind}:{self.request_id}"


# --- Identity of vehicles: conflicts of identifiers (ТЗ § 8.6.1–8.6.3) ----------------------


class IdentifierConflict(models.Model):
    """A recorded match of an identifier between different global vehicles (§ 8.6.1): a reason
    to check, never a block of a card. It keeps the kind, the normalized value, the participating
    cards (`IdentifierConflictParty`, each with its original value), the moment of detection and
    the state of the review. Until it is resolved another company sees only the fact of a match
    and the number of the conflict."""

    class Kind(models.TextChoices):
        VIN = "vin"
        PLATE = "plate"
        BODY = "body_number"
        CHASSIS = "chassis_number"
        FRAME = "frame_number"

    class Review(models.TextChoices):
        NEW = "new"  # not reviewed yet
        # § 8.6.2, R3: the data are wrong; the conflict stays on the records until corrected.
        AWAITING_CORRECTION = "awaiting_correction"
        INSUFFICIENT = "insufficient"  # not enough evidence: stays open
        SAME_VEHICLE = "same_vehicle"  # the cards are linked to one global entity
        EXCEPTION = "exception"  # different vehicles; confirmed exception
        CAUSE_REMOVED = "cause_removed"  # the values no longer match

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    # The ID shown to the companies (PROPOSAL: a number, № 17).
    number = models.PositiveIntegerField(unique=True)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    normalized_value = models.CharField(max_length=40)
    # For a plate: the country and the region of registration (§ 8.6.1).
    country = models.CharField(max_length=2, blank=True, default="")
    region = models.CharField(max_length=60, blank=True, default="")
    is_open = models.BooleanField(default=True)
    review = models.CharField(max_length=20, choices=Review.choices, default=Review.NEW)
    detected_at = models.DateTimeField(default=now)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "vehicle_identifier_conflict"
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "normalized_value", "country", "region"],
                condition=Q(is_open=True),
                name="vehicle_identifier_conflict_one_open",
            ),
            models.CheckConstraint(
                condition=Q(is_open=True, closed_at__isnull=True)
                | Q(is_open=False, closed_at__isnull=False),
                name="vehicle_identifier_conflict_closed_at",
            ),
        ]

    def __str__(self) -> str:
        return f"conflict {self.number}"


class IdentifierConflictParty(models.Model):
    """A card (or a global vehicle without a card) in a conflict with the value it was entered
    with. A party whose value no longer matches leaves the conflict; the row stays."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    conflict = models.ForeignKey(
        IdentifierConflict, on_delete=models.PROTECT, related_name="parties"
    )
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    vehicle_card = models.ForeignKey(
        VehicleCard, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    original_value = models.CharField(max_length=60)
    normalized_value = models.CharField(max_length=40)
    joined_at = models.DateTimeField(default=now)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "vehicle_identifier_conflict_party"
        constraints = [
            models.UniqueConstraint(
                fields=["conflict", "vehicle", "vehicle_card"],
                condition=Q(left_at__isnull=True),
                name="vehicle_identifier_conflict_party_once",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=Q(vehicle_card__isnull=True, company__isnull=True)
                | Q(vehicle_card__isnull=False, company__isnull=False),
                name="vehicle_identifier_conflict_party_card_company",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.conflict_id}:{self.vehicle_card_id or self.vehicle_id}"


class IdentifierConflictDecision(models.Model):
    """A result of the check (§ 8.6.2) with its basis, evidence, staff member and moment; a
    revision is a new decision, the earlier one is never deleted. A decision of the system — the
    cause removed — has no staff member."""

    class Outcome(models.TextChoices):
        SAME_VEHICLE = "same_vehicle"
        EXCEPTION = "exception"
        ERROR = "error"
        INSUFFICIENT = "insufficient"
        CAUSE_REMOVED = "cause_removed"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    conflict = models.ForeignKey(
        IdentifierConflict, on_delete=models.PROTECT, related_name="decisions"
    )
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    reason = models.TextField(blank=True, default="")
    evidence = models.TextField(blank=True, default="")
    ticket_id = models.UUIDField(null=True, blank=True)
    primary_vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    details = models.JSONField(default=dict, blank=True)
    revises = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    decided_by_user_id = models.UUIDField(null=True, blank=True)
    decided_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_identifier_conflict_decision"

    def __str__(self) -> str:
        return f"{self.outcome}:{self.conflict_id}"


class IdentifierException(models.Model):
    """Different vehicles with the same identifier, confirmed by the system staff (§ 8.6.1,
    § 8.6.2): the same match between them does not open a new conflict. A revision revokes it."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    kind = models.CharField(max_length=20, choices=IdentifierConflict.Kind.choices)
    normalized_value = models.CharField(max_length=40)
    country = models.CharField(max_length=2, blank=True, default="")
    region = models.CharField(max_length=60, blank=True, default="")
    vehicle_a = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    vehicle_b = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    decision = models.ForeignKey(
        IdentifierConflictDecision, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(default=now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        IdentifierConflictDecision,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "vehicle_identifier_exception"
        constraints = [
            models.CheckConstraint(
                condition=Q(vehicle_a__lt=models.F("vehicle_b")),
                name="vehicle_identifier_exception_ordered_pair",
            ),
        ]
        indexes = [
            models.Index(fields=["kind", "normalized_value"], name="identifier_exception_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.normalized_value}"


# --- Confirmed characteristics (ТЗ § 8.2.1) ---------------------------------------------------


class VehicleCharacteristicProposal(models.Model):
    """A correction of a confirmed value proposed by the owner or the managing company with a
    basis or a supporting document (§ 8.2.1); decided by an authorized c4rsys staff member."""

    class State(models.TextChoices):
        OPEN = "open"
        ACCEPTED = "accepted"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="+")
    field = models.CharField(max_length=30)
    current_value = models.JSONField(null=True, blank=True)
    proposed_value = models.JSONField(null=True, blank=True)
    reason = models.TextField()
    file_id = models.UUIDField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.OPEN)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    decided_by_user_id = models.UUIDField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "vehicle_characteristic_proposal"
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle_card", "field"],
                condition=Q(state="open"),
                name="vehicle_characteristic_proposal_one_open",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.field}:{self.state}"


class VehicleCharacteristicEvent(models.Model):
    """The history of a confirmed factory characteristic: the earlier and the new value, the
    initiator, the basis, the decision, the moment (§ 8.2.1). Append-only."""

    class Action(models.TextChoices):
        CONFIRMED = "confirmed"  # the staff confirmed (and possibly set) the value
        CORRECTED = "corrected"  # a proposal of a company was accepted
        REJECTED = "rejected"  # a proposal was rejected; the value stays

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    field = models.CharField(max_length=30)
    action = models.CharField(max_length=12, choices=Action.choices)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    proposal = models.ForeignKey(
        VehicleCharacteristicProposal,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    initiator_company_id = models.UUIDField(null=True, blank=True)
    initiator_user_id = models.UUIDField(null=True, blank=True)
    reason = models.TextField()
    ticket_id = models.UUIDField(null=True, blank=True)
    decided_by_user_id = models.UUIDField()
    decided_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_characteristic_event"

    def __str__(self) -> str:
        return f"{self.field}:{self.action}"


class VehicleCardWorkingValue(models.Model):
    """A working value of a characteristic in the card of one company with an explicit mark of
    the discrepancy from the global value (§ 8.2.1). Ended rows stay as history."""

    class Origin(models.TextChoices):
        PROPOSAL = "proposal"  # used until the decision on the company's proposal
        LINK = "link"  # the card's own value kept when it was linked to another entity
        # Entered in the card over a value kept by one of the above, or by a company that is
        # not the owner of a shared vehicle (decision В-12.2).
        CARD = "card"
        # В-12.2: the value the card showed before the owner changed the global one; it stays
        # until the company applies the owner's data.
        PREVIOUS = "previous"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(
        VehicleCard, on_delete=models.PROTECT, related_name="working_values"
    )
    field = models.CharField(max_length=30)
    value = models.JSONField(null=True, blank=True)
    origin = models.CharField(max_length=10, choices=Origin.choices)
    proposal = models.ForeignKey(
        VehicleCharacteristicProposal,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    created_by_user_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)
    # В-12.2: the owner changed the global value after this one; the card shows that the owner's
    # data are newer until the company applies them («update the data»).
    owner_newer_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    # Why it ended: «applied» — the company applied the owner's data (В-12.2).
    ended_reason = models.CharField(max_length=12, blank=True, default="")

    class Meta:
        db_table = "vehicle_card_working_value"
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle_card", "field"],
                condition=Q(ended_at__isnull=True),
                name="vehicle_card_working_value_one_current",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vehicle_card_id}:{self.field}"


class VehicleOperationalDispute(models.Model):
    """A dispute of ownership or management for the system verification (decision В-12.3): the
    verification found one physical vehicle while it is in the own or managed fleet of two
    different companies (§ 8.3.2). The cards are linked to one global vehicle; the companies are
    told the number of the conflict only (§ 8.6.1). The decision keeps the reason, the evidence,
    the staff member, the ticket and the moment."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    conflict = models.ForeignKey(
        IdentifierConflict, on_delete=models.PROTECT, related_name="operational_disputes"
    )
    conflict_decision = models.OneToOneField(
        IdentifierConflictDecision, on_delete=models.PROTECT, related_name="operational_dispute"
    )
    is_open = models.BooleanField(default=True)
    opened_at = models.DateTimeField(default=now)
    closed_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True, default="")
    evidence = models.TextField(blank=True, default="")
    ticket_id = models.UUIDField(null=True, blank=True)
    decided_by_user_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "vehicle_operational_dispute"
        constraints = [
            models.CheckConstraint(
                condition=Q(is_open=True, closed_at__isnull=True)
                | Q(is_open=False, closed_at__isnull=False),
                name="vehicle_operational_dispute_closed_at",
            ),
        ]

    def __str__(self) -> str:
        return f"operational dispute {self.conflict_id}"


class VehicleOperationalDisputeParty(models.Model):
    """A company in a dispute of В-12.3 with its own or managed card at the moment of the link."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    dispute = models.ForeignKey(
        VehicleOperationalDispute, on_delete=models.PROTECT, related_name="parties"
    )
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="+")
    relation = models.CharField(max_length=10)

    class Meta:
        db_table = "vehicle_operational_dispute_party"
        constraints = [
            models.UniqueConstraint(
                fields=["dispute", "vehicle_card"], name="vehicle_operational_dispute_party_once"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.dispute_id}:{self.vehicle_card_id}"


class VehicleOwnershipRevision(models.Model):
    """A correction of a confirmed period of ownership by the system verification (§ 8.3.16):
    a new version of the history; the earlier one, the initiator, the evidence and the decision
    are kept."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    ownership = models.ForeignKey(VehicleOwnership, on_delete=models.PROTECT, related_name="+")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    before = models.JSONField()
    after = models.JSONField()
    initiator = models.CharField(max_length=300)
    reason = models.TextField()
    evidence = models.TextField(blank=True, default="")
    ticket_id = models.UUIDField(null=True, blank=True)
    decided_by_user_id = models.UUIDField()
    decided_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_ownership_revision"

    def __str__(self) -> str:
        return f"revision {self.ownership_id}"


# --- Merge and split of cards inside a company (ТЗ § 8.6.5–8.6.6) -----------------------------


class VehicleCardMerge(models.Model):
    """A duplicate card merged into the main card of the same company. The duplicate is not
    rewritten: it gets the state «merged» and refers to the main card; the records of other
    modules keep their origin. `chosen` keeps the values taken from the duplicate and `before`
    the values of both cards, so a split restores them (§ 8.6.5)."""

    class State(models.TextChoices):
        MERGED = "merged"
        SPLIT = "split"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    primary = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="+")
    duplicate = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="+")
    state = models.CharField(max_length=10, choices=State.choices, default=State.MERGED)
    reason = models.TextField(blank=True, default="")
    chosen = models.JSONField(default=dict, blank=True)
    before = models.JSONField(default=dict, blank=True)
    merged_by_user_id = models.UUIDField()
    merged_at = models.DateTimeField(default=now)
    split_by_user_id = models.UUIDField(null=True, blank=True)
    split_at = models.DateTimeField(null=True, blank=True)
    split_reason = models.TextField(blank=True, default="")
    # The records made after the merge and the card each was assigned to by hand at the split.
    assignments = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "vehicle_card_merge"
        constraints = [
            models.UniqueConstraint(
                fields=["duplicate"],
                condition=Q(state="merged"),
                name="vehicle_card_merge_one_current",
            ),
            models.CheckConstraint(
                condition=~Q(primary=models.F("duplicate")), name="vehicle_card_merge_two_cards"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.duplicate_id} → {self.primary_id}"


class VehicleActionCode(models.Model):
    """The repeated one-time code of a protected action on a vehicle card (§ 8.6.6: the merge of
    duplicates). Only its hash is kept."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    user_id = models.UUIDField()
    purpose = models.CharField(max_length=30)
    subject = models.CharField(max_length=120)
    code_hash = models.CharField(max_length=200, blank=True, default="")
    code_expires_at = models.DateTimeField(null=True, blank=True)
    code_attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_action_code"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "user_id", "purpose", "subject"],
                name="vehicle_action_code_once",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.purpose}:{self.subject}"


# --- Lifecycle of a vehicle card (ТЗ § 8.1.6, § 8.3.10, § 8.3.17, § 8.4.2, § 8.7.5–8.7.6) ------


class VehicleCardPeriod(models.Model):
    """A period of work of the company with the vehicle under one relation (§ 8.7.6): a change of
    the relation, the end of the management, an archive and a restore of the card end one period
    and may begin another. The history of the card is shown by these periods."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="periods")
    relation = models.CharField(max_length=10, choices=VehicleCard.Relation.choices)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    start_basis = models.CharField(max_length=30)
    start_reason = models.TextField(blank=True, default="")
    end_basis = models.CharField(max_length=30, blank=True, default="")
    end_reason = models.TextField(blank=True, default="")
    started_by_user_id = models.UUIDField(null=True, blank=True)
    ended_by_user_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "vehicle_card_period"
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle_card"],
                condition=Q(ended_at__isnull=True),
                name="vehicle_card_period_one_open",
            ),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gte=models.F("started_at")),
                name="vehicle_card_period_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vehicle_card_id}:{self.relation}"


class VehicleDisposal(models.Model):
    """A write-off or a disposal of an own vehicle from the warehouse (§ 8.4.2)."""

    class Kind(models.TextChoices):
        WRITE_OFF = "write_off"
        DISPOSAL = "disposal"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    reason = models.TextField()
    storage_context = models.ForeignKey(ActivityContext, on_delete=models.PROTECT, related_name="+")
    done_at = models.DateTimeField()
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_disposal"
        constraints = [
            models.UniqueConstraint(fields=["vehicle_card"], name="vehicle_disposal_once"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.vehicle_card_id}"


class VehicleTransferReversal(models.Model):
    """The seller's request to the new owner to confirm that the transfer of ownership by a sale
    was wrong and to cancel it (§ 8.3.9). Until the confirmation (or a decision of the support)
    the ownership and the personal garage stay."""

    class State(models.TextChoices):
        OPEN = "open"
        ACCEPTED = "accepted"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="+")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    ownership = models.ForeignKey(VehicleOwnership, on_delete=models.PROTECT, related_name="+")
    order_id = models.UUIDField()
    reason = models.TextField()
    state = models.CharField(max_length=10, choices=State.choices, default=State.OPEN)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    answered_by_user_id = models.UUIDField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    answer_comment = models.TextField(blank=True, default="")

    class Meta:
        db_table = "vehicle_transfer_reversal"
        constraints = [
            models.UniqueConstraint(
                fields=["ownership"],
                condition=Q(state="open"),
                name="vehicle_transfer_reversal_one_open",
            ),
        ]

    def __str__(self) -> str:
        return f"reversal {self.ownership_id}"


# --- Manual restriction of a vehicle (ТЗ § 8.8, § 23.5.5–23.5.6) ------------------------------


class VehicleRestrictionReason(models.Model):
    """A reason of the company added to the system list (§ 8.8.7)."""

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=200)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_restriction_reason"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="vehicle_restriction_reason_company_id_uniq"
            ),
            models.UniqueConstraint(
                fields=["company", "name"], name="vehicle_restriction_reason_name_uniq"
            ),
            models.CheckConstraint(
                condition=~Q(name=""), name="vehicle_restriction_reason_name_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class VehicleRestriction(models.Model):
    """A manual ban of chosen actions with a vehicle, apart from the occupancy by operations
    (§ 8.8.1, § 8.7.2): the reason from the list with a comment, the start, an end or none, the
    forbidden actions. With the end reached it ends by itself (§ 8.8.3). In the MVP one at a time
    (§ 8.8.5): the periods of a card do not overlap; dropping that exclusion allows several
    restrictions later (§ 8.8.6). `period` is [start, the end or the lift) and is kept by the
    commands."""

    class Reason(models.TextChoices):
        TECHNICAL = "technical_fault"
        SAFETY = "safety"
        DOCUMENT = "document_missing"
        ACCIDENT = "accident_damage"
        LEGAL = "legal"
        OTHER = "other"
        CUSTOM = "custom"  # a reason of the company

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(
        VehicleCard, on_delete=models.PROTECT, related_name="restrictions"
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    custom_reason = models.ForeignKey(
        VehicleRestrictionReason, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    comment = models.CharField(max_length=2000, blank=True, default="")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    # The forbidden actions (§ 8.8.7): the issue into a new rental; the start of a delivery or of
    # a usual move; the start of use in the chosen contexts.
    forbid_rental_issue = models.BooleanField(default=False)
    forbid_moves = models.BooleanField(default=False)
    forbidden_contexts = models.JSONField(default=list, blank=True)
    period = DateTimeRangeField()
    lifted_at = models.DateTimeField(null=True, blank=True)
    lifted_by_user_id = models.UUIDField(null=True, blank=True)
    lift_comment = models.CharField(max_length=2000, blank=True, default="")
    # The automatic end was journaled (§ 8.8.3, § 8.8.7).
    end_journaled_at = models.DateTimeField(null=True, blank=True)
    # The operations running at the start were told (§ 8.8.7: a notification and a task).
    start_checked_at = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "vehicle_restriction"
        constraints = [
            ExclusionConstraint(
                name="vehicle_restriction_one_at_a_time",
                expressions=[
                    ("vehicle_card", RangeOperators.EQUAL),
                    ("period", RangeOperators.OVERLAPS),
                ],
            ),
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gt=models.F("starts_at")),
                name="vehicle_restriction_period_valid",
            ),
            models.CheckConstraint(
                condition=Q(reason="custom", custom_reason__isnull=False)
                | (~Q(reason="custom") & Q(custom_reason__isnull=True)),
                name="vehicle_restriction_custom_reason",
            ),
            models.CheckConstraint(
                condition=Q(forbid_rental_issue=True)
                | Q(forbid_moves=True)
                | ~Q(forbidden_contexts=[]),
                name="vehicle_restriction_forbids_something",
            ),
        ]

    def __str__(self) -> str:
        return f"restriction {self.vehicle_card_id}"


# --- Odometer: unit change and replacement (ТЗ § 8.2.10, § 8.2.13) ---------------------------


class OdometerReplacement(models.Model):
    """The system work «Odometer replacement» (§ 8.2.13): the moment, the last reading of the old
    odometer and the first of the new one (not necessarily zero). The accumulated mileage goes on
    from the old final reading; `reading` is the reading of the new odometer at that moment."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="+")
    replaced_at = models.DateTimeField()
    old_final = models.DecimalField(max_digits=12, decimal_places=1)
    new_start = models.DecimalField(max_digits=12, decimal_places=1)
    unit = models.CharField(max_length=2, choices=VehicleCard.OdometerUnit.choices)
    reading = models.OneToOneField(OdometerReading, on_delete=models.PROTECT, related_name="+")
    source_type = models.CharField(max_length=30)
    source_id = models.UUIDField(null=True, blank=True)
    entered_by_user_id = models.UUIDField()
    entered_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "odometer_replacement"
        constraints = [
            models.CheckConstraint(
                condition=Q(old_final__gte=0) & Q(new_start__gte=0),
                name="odometer_replacement_not_negative",
            ),
            models.UniqueConstraint(
                fields=["vehicle_card", "replaced_at"], name="odometer_replacement_one_moment"
            ),
        ]

    def __str__(self) -> str:
        return f"replacement {self.vehicle_card_id} @ {self.replaced_at:%Y-%m-%d}"


class OdometerUnitChange(models.Model):
    """A change of the odometer unit (§ 8.2.10): the method chosen explicitly — the label only or
    a conversion by 1.609344 — with the original numbers and units of every converted record,
    the employee and the moment."""

    class Method(models.TextChoices):
        RELABEL = "relabel"
        CONVERT = "convert"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="+")
    method = models.CharField(max_length=10, choices=Method.choices)
    from_unit = models.CharField(max_length=2, choices=VehicleCard.OdometerUnit.choices)
    to_unit = models.CharField(max_length=2, choices=VehicleCard.OdometerUnit.choices)
    reason = models.CharField(max_length=2000, blank=True, default="")
    # {"readings": [...], "replacements": [...], "<module>": [...]}: the values before and after.
    details = models.JSONField(default=dict)
    changed_by_user_id = models.UUIDField()
    changed_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "odometer_unit_change"
        constraints = [
            models.CheckConstraint(
                condition=~Q(from_unit=models.F("to_unit")), name="odometer_unit_change_differs"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.from_unit}→{self.to_unit} {self.method}"


# --- What the owner of a managed vehicle sees (ТЗ § 8.3.4, § 8.3.6–8.3.7, § 8.3.15) ----------


class VehicleManagementConfirmation(models.Model):
    """A period of the confirmed management (§ 8.3.1): from the third «yes» to a new declaration
    of the owner, a change of the owner (§ 8.3.10) or the end of the management (§ 8.3.17). Only
    within such a period the owner receives the history of the company; what he received stays."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="+")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    request = models.OneToOneField(VehicleOwnerRequest, on_delete=models.PROTECT, related_name="+")
    owner_user_id = models.UUIDField(null=True, blank=True)
    owner_company_id = models.UUIDField(null=True, blank=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=30, blank=True, default="")

    class Meta:
        db_table = "vehicle_management_confirmation"
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle_card"],
                condition=Q(ended_at__isnull=True),
                name="vehicle_management_confirmation_one_open",
            ),
            models.CheckConstraint(
                condition=Q(owner_user_id__isnull=False, owner_company_id__isnull=True)
                | Q(owner_user_id__isnull=True, owner_company_id__isnull=False),
                name="vehicle_management_confirmation_one_owner",
            ),
        ]

    def __str__(self) -> str:
        return f"management {self.vehicle_card_id}"


class VehicleDisclosureSetting(models.Model):
    """The categories of history and finance the company passes to the owner of the managed
    vehicle (§ 8.3.7, § 8.3.15), with the moment they take effect. A new row is a new version;
    the latest effective one is current."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(VehicleCard, on_delete=models.PROTECT, related_name="+")
    categories = models.JSONField(default=list)
    effective_from = models.DateTimeField()
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_disclosure_setting"
        indexes = [
            models.Index(fields=["vehicle_card", "effective_from"], name="vehicle_disclosure_idx"),
        ]

    def __str__(self) -> str:
        return f"disclosure {self.vehicle_card_id}"


# --- Global documents of a vehicle (ТЗ § 8.2.3–8.2.4, § 8.2.7) -------------------------------


class VehicleDocument(models.Model):
    """A changeable document of the global vehicle — the vehicle passport, the registration
    certificate, an insurance policy or another one — kept by versions (§ 8.2.3). Not public:
    the confirmed owner manages the access."""

    class Type(models.TextChoices):
        PASSPORT = "passport"  # ПТС
        REGISTRATION = "registration"  # СТС
        INSURANCE = "insurance"
        OTHER = "other"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    doc_type = models.CharField(max_length=20, choices=Type.choices)
    title = models.CharField(max_length=300, blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_document"

    def __str__(self) -> str:
        return f"{self.doc_type}:{self.vehicle_id}"


class VehicleDocumentVersion(models.Model):
    """A version never overwrites the earlier one (§ 8.2.3): the details, the date of issue, the
    term of validity and the file. The source is the owner or a company's copy the owner accepted
    (§ 8.2.7)."""

    class Source(models.TextChoices):
        OWNER = "owner"
        COMPANY_COPY = "company_copy"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    document = models.ForeignKey(VehicleDocument, on_delete=models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField()
    details = models.CharField(max_length=1000, blank=True, default="")
    issued_on = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    file_id = models.UUIDField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices)
    source_company_id = models.UUIDField(null=True, blank=True)
    source_version_id = models.UUIDField(null=True, blank=True)
    owner_user_id = models.UUIDField(null=True, blank=True)
    owner_company_id = models.UUIDField(null=True, blank=True)
    added_by_user_id = models.UUIDField()
    added_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_document_version"
        constraints = [
            models.UniqueConstraint(
                fields=["document", "number"], name="vehicle_document_version_number_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document_id} v{self.number}"


class VehicleDocumentGrant(models.Model):
    """A separate access of a company to the global documents given by the owner (§ 8.2.3)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    owner_user_id = models.UUIDField(null=True, blank=True)
    owner_company_id = models.UUIDField(null=True, blank=True)
    granted_by_user_id = models.UUIDField()
    granted_at = models.DateTimeField(default=now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "vehicle_document_grant"
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle", "company"],
                condition=Q(revoked_at__isnull=True),
                name="vehicle_document_grant_one_active",
            ),
        ]

    def __str__(self) -> str:
        return f"grant {self.vehicle_id}:{self.company_id}"


class VehicleCopyDecision(models.Model):
    """The owner's decision on a copy uploaded by a company before the confirmation (§ 8.2.7):
    accepted as a new version of the global document, or left in the closed history of that
    company only."""

    class Decision(models.TextChoices):
        ACCEPTED = "accepted"
        KEPT_PRIVATE = "kept_private"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    # The version of the company's document (documents module).
    company_version_id = models.UUIDField(unique=True)
    decision = models.CharField(max_length=15, choices=Decision.choices)
    global_version = models.ForeignKey(
        VehicleDocumentVersion, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    owner_user_id = models.UUIDField(null=True, blank=True)
    owner_company_id = models.UUIDField(null=True, blank=True)
    decided_by_user_id = models.UUIDField()
    decided_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "vehicle_copy_decision"

    def __str__(self) -> str:
        return f"{self.decision}:{self.company_version_id}"
