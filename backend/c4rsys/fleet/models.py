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
        CARD = "card"  # entered later in the card over a value kept by one of the above

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
    ended_at = models.DateTimeField(null=True, blank=True)

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
