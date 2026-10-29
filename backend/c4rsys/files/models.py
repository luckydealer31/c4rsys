"""Details of an uploaded file (A10). The content lives outside PostgreSQL under its id."""

from django.db import models
from django.db.models import Q
from django.utils import timezone

from c4rsys.platform.ids import uuid7


class StoredFile(models.Model):
    """A file is closed by default (A10): who may read it is decided by its purpose.

    Uploader and company are plain UUIDs: the module does not own accounts or companies.
    """

    class Purpose(models.TextChoices):
        AVATAR = "avatar"  # profile photo (§ 2.1.5)
        COMPANY_LOGO = "company_logo"  # § 3.1.1, § 3.1.5
        ITEM_PHOTO = "item_photo"  # photo of a catalog item (§ 22.4.1)
        IMPORT_FILE = "import_file"  # source file of an import (§ 22.2.4)
        # Invoice, receipt or another document of a warehouse receipt (§ 11.2.8).
        WAREHOUSE_DOCUMENT = "warehouse_document"
        # A file of a company document or of its version (§ 18.1.3).
        COMPANY_DOCUMENT = "company_document"
        # An attachment of a work task (§ 19.1.8; decision З-10).
        TASK_ATTACHMENT = "task_attachment"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    purpose = models.CharField(max_length=30, choices=Purpose.choices)
    uploaded_by = models.UUIDField()
    company_id = models.UUIDField(null=True, blank=True)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "stored_file"
        constraints = [
            models.CheckConstraint(condition=Q(size__gt=0), name="stored_file_size_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.purpose}:{self.id}"
