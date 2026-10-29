"""Personal dashboards (ТЗ § 21.2.4–21.2.5; customer's decision О-8 of 29.10.2026).

A personal dashboard is a setting of one employee in one company: a name and the chosen system
metrics in their order. It never changes the dashboards of others and is not published to them;
it does not widen the access to data (§ 21.1.1).
"""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company, Employment
from c4rsys.platform.ids import uuid7


class PersonalDashboard(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    employment = models.ForeignKey(Employment, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=100)
    metrics = models.JSONField(default=list)
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "report_personal_dashboard"
        constraints = [
            models.CheckConstraint(condition=~Q(name=""), name="report_dashboard_name_not_empty"),
        ]

    def __str__(self) -> str:
        return self.name
