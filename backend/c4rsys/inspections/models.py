"""Inspections, their templates and policies, damages (ТЗ гл. 10, § 14.3.1, § 16.5.4)."""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company, Location
from c4rsys.platform.ids import uuid7
from c4rsys.platform.models import Currency


class Inspection(models.Model):
    """Odometer and fuel are stored here even when no inspection was required (§ 14.3.1).
    A saved inspection is immutable; an error is handled by marking it invalid with a reason
    (§ 10.1.6) — a database trigger forbids any other change (migrations 0002, 0003).

    Kinds (§ 10.1.1): «at the acceptance» is `return` — the acceptance of the vehicle back from
    a rental and its acceptance into a service; «at the issue» is `issue`; «out of rental» and
    «intermediate» (during a rental or a service)."""

    class Kind(models.TextChoices):
        ISSUE = "issue"
        RETURN = "return"  # «при приёме» (§ 10.1.1)
        OUT_OF_RENTAL = "out_of_rental"
        INTERMEDIATE = "intermediate"

    class Application(models.TextChoices):
        RENTAL = "rental"
        SERVICE = "service"
        STANDALONE = "standalone"

    class State(models.TextChoices):
        DRAFT = "draft"
        PLANNED = "planned"
        IN_PROGRESS = "in_progress"
        SAVED = "saved"
        CANCELLED = "cancelled"  # planned and not started (§ 10.6.3)
        TERMINATED = "terminated"  # started; the data stay but are not a result (§ 10.6.3)
        INVALID = "invalid"

    class CheckStatus(models.TextChoices):
        """§ 10.1.5: the performer is not the same as the check having been done."""

        CONDUCTED = "conducted"
        NOT_REQUIRED = "not_required"
        REQUIRED_NOT_DONE = "required_not_done"

    class PlaceKind(models.TextChoices):
        LOCATION = "location"
        OTHER = "other"

    class Origin(models.TextChoices):
        RENTAL = "rental"  # readings entered in the rental card at the issue or return (§ 10.1.4)
        FORM = "form"  # the separate form of an inspection

    class Participant(models.TextChoices):
        NONE = ""
        CLIENT = "client"
        COUNTERPARTY = "counterparty"
        EMPLOYEE = "employee"
        OTHER = "other"  # described by text

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, related_name="+"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    application = models.CharField(
        max_length=10, choices=Application.choices, default=Application.STANDALONE
    )
    rental = models.ForeignKey(
        "rentals.Rental", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    service_operation = models.ForeignKey(
        "service.ServiceOperation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    origin = models.CharField(max_length=10, choices=Origin.choices, default=Origin.FORM)
    state = models.CharField(max_length=12, choices=State.choices)
    check_status = models.CharField(max_length=20, choices=CheckStatus.choices)
    # Whether the policy required the condition check when the record was made (§ 10.3.3).
    check_required = models.BooleanField(default=False)
    performed_at = models.DateTimeField()
    planned_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    # § 10.1.2: a location of the company, or «another place» described by any of the fields.
    place_kind = models.CharField(max_length=10, choices=PlaceKind.choices)
    place_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    place_note = models.CharField(max_length=500, blank=True, default="")
    place_address = models.CharField(max_length=500, blank=True, default="")
    place_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    place_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    # § 10.1.3: the second participant — a client card, a counterparty, an employee or text.
    participant_kind = models.CharField(
        max_length=12, choices=Participant.choices, blank=True, default=""
    )
    participant_client = models.ForeignKey(
        "parties.ClientCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    participant_counterparty = models.ForeignKey(
        "parties.Counterparty", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    participant_employment = models.ForeignKey(
        "companies.Employment", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    participant_text = models.CharField(max_length=300, blank=True, default="")
    performer_employment_id = models.UUIDField()
    author_user_id = models.UUIDField()
    entered_at = models.DateTimeField(default=now)
    template_version = models.ForeignKey(
        "InspectionTemplateVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    odometer_value = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True)
    odometer_unit = models.CharField(max_length=2)
    fuel_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # TODO(spec): the scale of cleanliness is not defined (§ 10.3.5); kept as text.
    cleanliness = models.CharField(max_length=200, blank=True, default="")
    # Damages being recorded; they become damage records when the inspection is saved (§ 10.5.2).
    damages_draft = models.JSONField(default=list, blank=True)
    saved_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.TextField(blank=True, default="")
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by_user_id = models.UUIDField(null=True, blank=True)
    invalidated_reason = models.TextField(blank=True, default="")
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidated_by_user_id = models.UUIDField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    # A local draft made on a device without connection (§ 10.4.3): the id the device gave it.
    # It is sent once; a repeated sending of the same draft is refused, never a second record.
    client_draft_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "inspection"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="inspection_company_id_uniq"),
            models.UniqueConstraint(
                fields=["company", "client_draft_id"],
                condition=Q(client_draft_id__isnull=False),
                name="inspection_offline_draft_once",
            ),
            models.CheckConstraint(
                condition=~Q(state__in=["saved", "invalid"]) | Q(saved_at__isnull=False),
                name="inspection_saved_has_time_v2",
            ),
            models.CheckConstraint(
                condition=Q(place_kind="location", place_location__isnull=False)
                | Q(place_kind="other")
                & (~Q(place_note="") | ~Q(place_address="") | Q(place_lat__isnull=False)),
                name="inspection_place_described",
            ),
            models.CheckConstraint(
                condition=Q(odometer_value__isnull=True) | Q(odometer_value__gte=0),
                name="inspection_odometer_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(fuel_value__isnull=True) | Q(fuel_value__gte=0),
                name="inspection_fuel_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(application="rental", rental__isnull=False, service_operation=None)
                | Q(application="service", rental=None, service_operation__isnull=False)
                | Q(application="standalone", rental=None, service_operation=None),
                name="inspection_application_link",
            ),
            models.CheckConstraint(
                condition=~Q(kind="intermediate") | Q(application__in=["rental", "service"]),
                name="inspection_intermediate_in_operation",
            ),
            models.CheckConstraint(
                condition=~Q(kind="out_of_rental") | ~Q(application="rental"),
                name="inspection_out_of_rental_not_in_rental",
            ),
            models.CheckConstraint(
                condition=~Q(state__in=["cancelled", "terminated"]) | ~Q(end_reason=""),
                name="inspection_end_has_reason",
            ),
            models.CheckConstraint(
                condition=~Q(state="invalid") | ~Q(invalidated_reason=""),
                name="inspection_invalid_has_reason",
            ),
            models.CheckConstraint(
                condition=Q(participant_kind="", participant_text="")
                | Q(participant_kind="client", participant_client__isnull=False)
                | Q(participant_kind="counterparty", participant_counterparty__isnull=False)
                | Q(participant_kind="employee", participant_employment__isnull=False)
                | Q(participant_kind="other") & ~Q(participant_text=""),
                name="inspection_participant_given",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "vehicle_card"], name="inspection_vehicle_idx"),
            models.Index(fields=["rental"], name="inspection_rental_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} inspection {self.id}"


class InspectionFile(models.Model):
    """A photo or video of an inspection: at a position of the template or an additional one
    (§ 10.3.5, § 10.4.1). Added while the inspection is a draft or in progress (trigger)."""

    class Kind(models.TextChoices):
        PHOTO = "photo"
        VIDEO = "video"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    inspection = models.ForeignKey(Inspection, on_delete=models.PROTECT, related_name="files")
    file = models.ForeignKey("files.StoredFile", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    # The code of the template position; empty — an additional photo or a video.
    position = models.CharField(max_length=60, blank=True, default="")
    added_by_user_id = models.UUIDField()
    added_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "inspection_file"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="inspection_file_company_id_uniq"
            ),
            models.UniqueConstraint(fields=["inspection", "file"], name="inspection_file_once"),
            models.CheckConstraint(
                condition=Q(kind="photo") | Q(position=""), name="inspection_file_video_extra"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.position or '-'}"


class InspectionTemplate(models.Model):
    """A ready template of the company, editable by it (§ 10.3.5): cars or motorcycles. Its
    composition is kept in versions; a change makes a new version."""

    class Transport(models.TextChoices):
        CAR = "car"
        MOTORCYCLE = "motorcycle"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    transport = models.CharField(max_length=12, choices=Transport.choices)
    current_number = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "inspection_template"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="inspection_template_company_id_uniq"
            ),
            models.UniqueConstraint(
                fields=["company", "transport"], name="inspection_template_one_per_transport"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.transport} template"


class InspectionTemplateVersion(models.Model):
    """An immutable version: the photo positions with their use, obligation and count. The base
    composition — odometer, fuel or charge, cleanliness, damages, photos — is the same in every
    template (§ 10.3.5)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    template = models.ForeignKey(
        InspectionTemplate, on_delete=models.PROTECT, related_name="versions"
    )
    number = models.PositiveIntegerField()
    # [{"code", "name", "enabled", "required", "count"}]; `name` only for own positions.
    positions = models.JSONField()
    created_by_user_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "inspection_template_version"
        constraints = [
            models.UniqueConstraint(fields=["template", "number"], name="inspection_template_ver"),
        ]

    def __str__(self) -> str:
        return f"{self.template_id} v{self.number}"


class InspectionPolicy(models.Model):
    """Required readings by kind and application of an inspection (§ 10.3.1). Odometer and fuel
    of an issue and a return in a rental are always required (§ 14.3.1); whether the condition
    check of these is required is the inspection policy of the issue (§ 14.3.6) and is not kept
    here (`check_required` is empty for them)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=20, choices=Inspection.Kind.choices)
    application = models.CharField(max_length=10, choices=Inspection.Application.choices)
    check_required = models.BooleanField(null=True, blank=True)
    odometer_required = models.BooleanField(default=False)
    fuel_required = models.BooleanField(default=False)
    cleanliness_required = models.BooleanField(default=False)
    updated_by_user_id = models.UUIDField()
    updated_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "inspection_policy"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "kind", "application"], name="inspection_policy_uniq"
            ),
            models.CheckConstraint(
                condition=~Q(application="rental", kind__in=["issue", "return"])
                | Q(check_required__isnull=True, odometer_required=True, fuel_required=True),
                name="inspection_policy_rental_readings",
            ),
            models.CheckConstraint(
                condition=Q(application="rental", kind__in=["issue", "return"])
                | Q(check_required__isnull=False),
                name="inspection_policy_check_given",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}/{self.application}"


class InspectionMissingReason(models.Model):
    """The recorded reason why a required inspection of a rental was not done, cancelled,
    terminated or invalidated; without it the rental is not closed (§ 10.3.3, § 10.6.3).
    Append-only."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    rental = models.ForeignKey("rentals.Rental", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=20, choices=Inspection.Kind.choices)
    reason = models.TextField()
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "inspection_missing_reason"
        constraints = [
            models.CheckConstraint(condition=~Q(reason=""), name="inspection_missing_reason_text"),
            models.CheckConstraint(
                condition=Q(kind__in=["issue", "return"]), name="inspection_missing_reason_kind"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.rental_id}:{self.kind}"


class Damage(models.Model):
    """A damage recorded through an inspection only (§ 10.5.2). It is an event of the vehicle's
    global history (`vehicle`), linked to the source inspection; the fixed facts — moment,
    odometer, zone, description — never change. Its state and estimate change with a history
    record each (§ 10.5.1, § 10.5.3)."""

    class State(models.TextChoices):
        ACTUAL = "actual"
        FIXED = "fixed"
        ERRONEOUS = "erroneous"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    vehicle_card = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, related_name="+"
    )
    vehicle = models.ForeignKey("fleet.Vehicle", on_delete=models.PROTECT, related_name="+")
    inspection = models.ForeignKey(Inspection, on_delete=models.PROTECT, related_name="+")
    rental = models.ForeignKey(
        "rentals.Rental", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # § 10.5.3: the damage is to be charged in the rental (chosen by the employee).
    attributed_to_rental = models.BooleanField(default=False)
    performed_at = models.DateTimeField()
    odometer_value = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True)
    odometer_unit = models.CharField(max_length=2)
    # § 10.3.6: a mark on the scheme of the vehicle in relative coordinates (decision В-8.2) or a
    # zone described by text.
    zone_scheme = models.CharField(max_length=30, blank=True, default="")
    zone_x = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    zone_y = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    zone_text = models.CharField(max_length=300, blank=True, default="")
    description = models.TextField(blank=True, default="")
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTUAL)
    # Empty — not estimated; 0 is an estimate (§ 10.5.5).
    estimate_amount = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    estimate_currency = models.ForeignKey(
        Currency, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "damage"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="damage_company_id_uniq"),
            models.CheckConstraint(
                condition=Q(estimate_amount__isnull=True, estimate_currency__isnull=True)
                | Q(estimate_amount__gte=0, estimate_currency__isnull=False),
                name="damage_estimate_valid",
            ),
            models.CheckConstraint(
                condition=Q(zone_scheme="", zone_x__isnull=True, zone_y__isnull=True)
                | ~Q(zone_scheme="")
                & Q(zone_x__gte=0, zone_x__lte=1, zone_y__gte=0, zone_y__lte=1),
                name="damage_zone_point",
            ),
            models.CheckConstraint(
                condition=~Q(description="") | ~Q(zone_text="") | ~Q(zone_scheme=""),
                name="damage_described",
            ),
            models.CheckConstraint(
                condition=Q(attributed_to_rental=False) | Q(rental__isnull=False),
                name="damage_attributed_has_rental",
            ),
        ]
        indexes = [models.Index(fields=["vehicle"], name="damage_vehicle_idx")]

    def __str__(self) -> str:
        return f"damage {self.id}"


class DamageEvent(models.Model):
    """The history of a damage, append-only (§ 10.5.1): creation, estimate, fixing, return after
    fixing, recognition as erroneous."""

    class Kind(models.TextChoices):
        CREATED = "created"
        ESTIMATED = "estimated"
        FIXED = "fixed"
        RETURNED = "returned"
        ERRONEOUS = "erroneous"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    damage = models.ForeignKey(Damage, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    state_before = models.CharField(max_length=10, blank=True, default="")
    state_after = models.CharField(max_length=10)
    occurred_at = models.DateTimeField()
    odometer_value = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True)
    # What the change is based on: an inspection or a service work.
    basis_type = models.CharField(max_length=20, blank=True, default="")
    basis_id = models.UUIDField(null=True, blank=True)
    estimate_amount = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    estimate_currency = models.CharField(max_length=3, blank=True, default="")
    comment = models.TextField(blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "damage_event"

    def __str__(self) -> str:
        return f"{self.damage_id}:{self.kind}"


class DamageFile(models.Model):
    """Photos chosen for a damage from its inspection, and photos of the result of fixing it
    (§ 10.5.1–10.5.2). Append-only."""

    class Role(models.TextChoices):
        SELECTED = "selected"
        FIX_RESULT = "fix_result"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    damage = models.ForeignKey(Damage, on_delete=models.PROTECT, related_name="files")
    event = models.ForeignKey(
        DamageEvent, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    file = models.ForeignKey("files.StoredFile", on_delete=models.PROTECT, related_name="+")
    role = models.CharField(max_length=12, choices=Role.choices)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "damage_file"

    def __str__(self) -> str:
        return f"{self.damage_id}:{self.role}"
