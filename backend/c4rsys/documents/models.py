"""Documents of a company (ТЗ гл. 18; customer's decisions Р-1…Р-11 of 29.10.2026).

* A document belongs to the company and has a title, a kind, a date, a number, an author and the
  state of signing; it may be linked to several objects and seen from each without copies, or
  have no links and live in the registry only (§ 18.1.9–18.1.10).
* The scope of access is set on the document: the whole company or chosen locations; links do not
  widen it (§ 18.5.3–18.5.4).
* Each change of the content is a new version; the former versions stay (§ 18.1.14, § 18.3.5).
* A term of validity gives one lasting warning some days before the end, until the cause is
  removed: a new version or the archive (§ 18.1.13–18.1.14).
"""

from django.db import models
from django.db.models import F, Q
from django.utils.timezone import now

from c4rsys.companies.models import Company, Location
from c4rsys.platform.ids import uuid7


class DocumentKind(models.Model):
    """A kind of document: a starting one of the system (no company, a code) or the company's
    own (§ 18.1.11)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    code = models.CharField(max_length=30, blank=True, default="")
    name = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "document_kind"
        constraints = [
            models.CheckConstraint(
                condition=Q(company__isnull=True, name="") & ~Q(code="")
                | Q(company__isnull=False, code="") & ~Q(name=""),
                name="document_kind_system_or_own",
            ),
            models.UniqueConstraint(
                fields=["code"], condition=Q(company__isnull=True), name="document_kind_code_uniq"
            ),
        ]

    def __str__(self) -> str:
        return self.code or self.name


class Document(models.Model):
    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=300)
    kind = models.ForeignKey(DocumentKind, on_delete=models.PROTECT, related_name="+")
    document_date = models.DateField()
    author_user_id = models.UUIDField()
    # The scope (§ 18.5.4): the whole company, or the listed locations.
    all_locations = models.BooleanField(default=True)
    valid_until = models.DateField(null=True, blank=True)
    warn_days = models.PositiveIntegerField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    archive_reason = models.CharField(max_length=1000, blank=True, default="")
    archived_at = models.DateTimeField(null=True, blank=True)
    # «Show to the client»: a document linked to the client's card or operations appears in the
    # personal cabinet (decision Б-2). A document the client signs is shown without it.
    show_to_client = models.BooleanField(default=False)
    # Made by a template: which one and which version of it (§ 18.5.2).
    template = models.ForeignKey(
        "documents.DocumentTemplate",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    template_version = models.PositiveIntegerField(null=True, blank=True)
    # The object the template's data came from, to make the document again (§ 18.5.2).
    source_type = models.CharField(max_length=20, blank=True, default="")
    source_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "document"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="document_company_id_uniq"),
            models.UniqueConstraint(fields=["company", "number"], name="document_number_uniq"),
            models.CheckConstraint(condition=~Q(title=""), name="document_title_not_empty"),
            models.CheckConstraint(
                condition=Q(warn_days__isnull=True) | Q(valid_until__isnull=False),
                name="document_warning_needs_term",
            ),
            models.CheckConstraint(
                condition=Q(state="active") | ~Q(archive_reason=""),
                name="document_archived_has_reason",
            ),
        ]

    def __str__(self) -> str:
        return f"document {self.number}"


class DocumentLocation(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="locations")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "document_location"
        constraints = [
            models.UniqueConstraint(fields=["document", "location"], name="document_location_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.document_id}@{self.location_id}"


class DocumentVersion(models.Model):
    """One version of the content: an uploaded file or, later, content made by a template
    (§ 18.1.3–18.1.4). Versions are not changed; a change is a new version."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField()
    file = models.ForeignKey(
        "files.StoredFile", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    file_name = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField(blank=True, default="")
    valid_until = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=1000, blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "document_version"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="document_version_company_id_uniq"
            ),
            models.UniqueConstraint(
                fields=["document", "number"], name="document_version_number_uniq"
            ),
            models.CheckConstraint(
                condition=Q(file__isnull=False) | ~Q(content=""),
                name="document_version_has_content",
            ),
            models.CheckConstraint(
                condition=Q(number__gte=1), name="document_version_number_gte_1"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document_id} v{self.number}"


class DocumentLink(models.Model):
    """A link of a document to an object of the company (§ 18.1.9)."""

    class ObjectType(models.TextChoices):
        CLIENT = "client"
        COUNTERPARTY = "counterparty"
        VEHICLE = "vehicle"
        ORDER = "order"
        RENTAL = "rental"
        SERVICE_OPERATION = "service_operation"
        DELIVERY = "delivery"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="links")
    object_type = models.CharField(max_length=20, choices=ObjectType.choices)
    object_id = models.UUIDField()
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by_user_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "document_link"
        constraints = [
            models.UniqueConstraint(
                fields=["document", "object_type", "object_id"],
                condition=Q(removed_at__isnull=True),
                name="document_link_active_uniq",
            ),
            models.CheckConstraint(
                condition=Q(removed_at__isnull=True) | Q(removed_at__gte=F("created_at")),
                name="document_link_removed_after",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "object_type", "object_id"], name="document_link_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.document_id}->{self.object_type}:{self.object_id}"


class DocumentTemplate(models.Model):
    """A template: a starting one of the system in its language, or the company's own (§ 18.1.5).
    The content is text with simple formatting and fields `{{source.field}}` and tables
    `{{table:source.collection}}` (§ 18.2.2; decision Р-1). A change makes a new version of the
    template; made documents keep theirs (§ 18.5.2)."""

    class Source(models.TextChoices):
        RENTAL = "rental"
        ORDER = "order"
        SERVICE_OPERATION = "service_operation"
        DELIVERY = "delivery"
        CLIENT = "client"
        VEHICLE = "vehicle"
        NONE = "none"

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    code = models.CharField(max_length=40, blank=True, default="")
    language = models.CharField(max_length=5, blank=True, default="")
    title = models.CharField(max_length=300)
    kind = models.ForeignKey(DocumentKind, on_delete=models.PROTECT, related_name="+")
    source = models.CharField(max_length=20, choices=Source.choices)
    content = models.TextField()
    required_fields = models.JSONField(default=list, blank=True)
    # Who signs a document of this template: the company, the client, a counterparty (§ 18.3.3).
    signers = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    template_version = models.PositiveIntegerField(default=1)
    created_by_user_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "document_template"
        constraints = [
            models.CheckConstraint(
                condition=~Q(title=""), name="document_template_title_not_empty"
            ),
            models.CheckConstraint(
                condition=Q(company__isnull=True) & ~Q(code="") | Q(company__isnull=False),
                name="document_template_system_has_code",
            ),
            models.UniqueConstraint(
                fields=["code", "language"],
                condition=Q(company__isnull=True),
                name="document_template_system_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.title


class DocumentSigner(models.Model):
    """A required signer of one version of a document: the company's representative, the client
    or a counterparty, with their own answer and time (§ 18.3.3, § 18.3.6). Signatures belong to
    the version; a new version does not inherit them (§ 18.3.5)."""

    class Role(models.TextChoices):
        COMPANY = "company"
        CLIENT = "client"
        COUNTERPARTY = "counterparty"

    class State(models.TextChoices):
        PENDING = "pending"
        SIGNED = "signed"
        DECLINED = "declined"

    class Method(models.TextChoices):
        IN_APP = "in_app"  # a signer with an account inside c4rsys, confirmed by a code
        LINK = "link"  # a personal link, confirmed by a code to the same contact (§ 18.3.9)
        PAPER = "paper"  # a scan of the paper signature entered by an employee (§ 18.3.7)

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="signers")
    document_version = models.ForeignKey(
        DocumentVersion, on_delete=models.PROTECT, related_name="signers"
    )
    role = models.CharField(max_length=15, choices=Role.choices)
    employment = models.ForeignKey(
        "companies.Employment", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    client = models.ForeignKey(
        "parties.ClientCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    counterparty = models.ForeignKey(
        "parties.Counterparty", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    name = models.CharField(max_length=300)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    method = models.CharField(max_length=10, choices=Method.choices, blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by_user_id = models.UUIDField(null=True, blank=True)
    confirmed_contact = models.CharField(max_length=254, blank=True, default="")
    comment = models.CharField(max_length=2000, blank=True, default="")
    paper_file = models.ForeignKey(
        "files.StoredFile", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    paper_signed_on = models.DateField(null=True, blank=True)
    entered_by_user_id = models.UUIDField(null=True, blank=True)
    entered_at = models.DateTimeField(null=True, blank=True)
    code_hash = models.CharField(max_length=128, blank=True, default="")
    code_expires_at = models.DateTimeField(null=True, blank=True)
    code_attempts = models.PositiveSmallIntegerField(default=0)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "document_signer"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="document_signer_company_id_uniq"
            ),
            models.CheckConstraint(
                condition=Q(
                    role="company",
                    employment__isnull=False,
                    client__isnull=True,
                    counterparty__isnull=True,
                )
                | Q(
                    role="client",
                    client__isnull=False,
                    employment__isnull=True,
                    counterparty__isnull=True,
                )
                | Q(
                    role="counterparty",
                    counterparty__isnull=False,
                    employment__isnull=True,
                    client__isnull=True,
                ),
                name="document_signer_person_of_role",
            ),
            models.CheckConstraint(
                condition=Q(state="pending") | (Q(decided_at__isnull=False) & ~Q(method="")),
                name="document_signer_decided_has_method",
            ),
            models.CheckConstraint(
                condition=~Q(method="paper")
                | Q(paper_file__isnull=False, paper_signed_on__isnull=False),
                name="document_signer_paper_has_scan",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.role} {self.name}"


class SigningInvitation(models.Model):
    """A personal link to sign one version, to the email of the signer's working card; it has a
    term, may be revoked and is used once (§ 18.3.8–18.3.10). Only the hash of the token is kept."""

    class State(models.TextChoices):
        ACTIVE = "active"
        USED = "used"
        REVOKED = "revoked"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    signer = models.ForeignKey(DocumentSigner, on_delete=models.PROTECT, related_name="invitations")
    token_hash = models.CharField(max_length=128, unique=True)
    email = models.CharField(max_length=254)
    expires_at = models.DateTimeField()
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    revoked_at = models.DateTimeField(null=True, blank=True)
    code_hash = models.CharField(max_length=128, blank=True, default="")
    code_expires_at = models.DateTimeField(null=True, blank=True)
    code_attempts = models.PositiveSmallIntegerField(default=0)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "document_signing_invitation"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="document_signing_invitation_company_id_uniq"
            ),
            models.UniqueConstraint(
                fields=["signer"],
                condition=Q(state="active"),
                name="document_signing_one_active_link",
            ),
        ]

    def __str__(self) -> str:
        return f"invitation {self.id}"


class KindSigning(models.Model):
    """Allowed ways of signing of a kind of document in a company (§ 18.4.2); none — both."""

    class Methods(models.TextChoices):
        IN_APP = "in_app"
        PAPER = "paper"
        BOTH = "both"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    kind = models.ForeignKey(DocumentKind, on_delete=models.PROTECT, related_name="+")
    methods = models.CharField(max_length=10, choices=Methods.choices, default=Methods.BOTH)

    class Meta:
        db_table = "document_kind_signing"
        constraints = [
            models.UniqueConstraint(fields=["company", "kind"], name="document_kind_signing_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.kind_id}: {self.methods}"


class DocumentRequirement(models.Model):
    """A document required at a stage of the company's rentals: before the issue, at the issue
    or at the return, present or signed (§ 18.4.1; decision Р-8). It is named by a template or by
    a kind; a document counts when it is linked to the rental."""

    class Stage(models.TextChoices):
        BEFORE_ISSUE = "before_issue"
        AT_ISSUE = "at_issue"
        AT_RETURN = "at_return"

    class Need(models.TextChoices):
        PRESENT = "present"
        SIGNED = "signed"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    stage = models.CharField(max_length=15, choices=Stage.choices)
    template = models.ForeignKey(
        DocumentTemplate, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    kind = models.ForeignKey(
        DocumentKind, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    need = models.CharField(max_length=10, choices=Need.choices)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "document_requirement"
        constraints = [
            models.CheckConstraint(
                condition=Q(template__isnull=False, kind__isnull=True)
                | Q(template__isnull=True, kind__isnull=False),
                name="document_requirement_template_or_kind",
            ),
            models.UniqueConstraint(
                fields=["company", "stage", "template"],
                condition=Q(template__isnull=False),
                name="document_requirement_template_uniq",
            ),
            models.UniqueConstraint(
                fields=["company", "stage", "kind"],
                condition=Q(kind__isnull=False),
                name="document_requirement_kind_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.stage}: {self.template_id or self.kind_id}"


class ServiceActSetting(models.Model):
    """The act of completed works of a client's service (§ 16.7.6; decision Р-9): required and
    signed by the client; both off by default. An internal order needs no act (§ 16.6.4)."""

    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="+")
    act_required = models.BooleanField(default=False)
    act_signed = models.BooleanField(default=False)
    updated_by_user_id = models.UUIDField()
    updated_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "document_service_act_setting"
        constraints = [
            models.CheckConstraint(
                condition=Q(act_signed=False) | Q(act_required=True),
                name="document_service_act_signed_needs_required",
            ),
        ]

    def __str__(self) -> str:
        return f"service act: {self.act_required}/{self.act_signed}"
