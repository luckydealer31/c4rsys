"""A full export of the company's data (ТЗ § 22.6.1, § 2.8.1; decision Б-6)."""

from django.db import models
from django.utils.timezone import now

from c4rsys.companies.models import Company
from c4rsys.platform.ids import uuid7


class DataExport(models.Model):
    """One prepared ZIP: a CSV file per table and the files of the documents the employee may
    view. The link works 24 hours and checks the sign-in and the current right when opened
    (§ 2.8.1); after that the file is removed and a new export is needed."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField()
    size = models.PositiveBigIntegerField(default=0)
    # {table: number of rows} and the number of document files, for the list of exports.
    tables = models.JSONField(default=dict)
    documents = models.PositiveIntegerField(default=0)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "data_export"
        indexes = [models.Index(fields=["company", "created_at"], name="data_export_company_idx")]

    def __str__(self) -> str:
        return f"export {self.id}"
