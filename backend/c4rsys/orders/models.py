"""Orders (ТЗ § 12.1–12.2)."""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company
from c4rsys.platform.ids import uuid7


class Order(models.Model):
    """An order of the company with a number sequential inside it (§ 12.1.1).

    Operations (rentals, later services) point to the order; they are not stored here. «Ready to
    close» is not a stored state: it follows from the operations (§ 12.1.9, § 12.2.3). Preliminary
    orders (§ 12.1.7, § 12.2.2) come later. The table is named `orders`: ORDER is an SQL keyword.
    """

    class State(models.TextChoices):
        OPEN = "open"
        CLOSED = "closed"
        CANCELLED = "cancelled"
        TERMINATED = "terminated"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    number = models.PositiveBigIntegerField()
    client = models.ForeignKey(
        "parties.ClientCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    is_internal = models.BooleanField(default=False)
    state = models.CharField(max_length=20, choices=State.choices, default=State.OPEN)
    responsible_employment_id = models.UUIDField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by_user_id = models.UUIDField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "orders"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="orders_company_id_uniq"),
            models.UniqueConstraint(
                fields=["company", "number"], name="orders_company_number_uniq"
            ),
            # A client order has a client; an internal one is the company's own (§ 12.1.3).
            models.CheckConstraint(
                condition=Q(is_internal=True, client__isnull=True)
                | Q(is_internal=False, client__isnull=False),
                name="orders_client_or_internal",
            ),
            models.CheckConstraint(
                condition=~Q(state="closed") | Q(closed_at__isnull=False),
                name="orders_closed_has_time",
            ),
        ]

    def __str__(self) -> str:
        return f"order {self.number}"
