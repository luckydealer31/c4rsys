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
