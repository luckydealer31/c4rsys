"""Cash desks and bank accounts of the company (ТЗ § 17.2.1, § 17.2.5, § 22.1.7)."""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company, Location
from c4rsys.platform.ids import uuid7
from c4rsys.platform.models import Currency


class CashAccount(models.Model):
    """A cash desk or a bank account, shared by the company or bound to one location (§ 17.2.5).

    Created only by hand (§ 22.1.7). A bank account is an accounting object, not a bank
    integration (§ 17.2.9). `location` is additionally bound to the same company by a composite
    foreign key (migration 0002).
    """

    class Type(models.TextChoices):
        CASH = "cash"
        BANK = "bank"

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=150)
    type = models.CharField(max_length=10, choices=Type.choices)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "cash_account"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="cash_account_company_id_uniq"),
            models.CheckConstraint(condition=~Q(name=""), name="cash_account_name_not_empty"),
        ]

    def __str__(self) -> str:
        return self.name


class ChargeEntry(models.Model):
    """An accrual to a client in an order, append-only (§ 12.2.4, § 17.2.6). The current charge
    of a source (for example the base cost of a rental) is the sum of its entries; a recalculation
    adds the difference as a new entry instead of changing an old one (§ 24.4.6)."""

    class SourceType(models.TextChoices):
        RENTAL_BASE = "rental_base"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="+")
    client = models.ForeignKey("parties.ClientCard", on_delete=models.PROTECT, related_name="+")
    source_type = models.CharField(max_length=30, choices=SourceType.choices)
    source_id = models.UUIDField()
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=60)
    calculation = models.JSONField(default=dict)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "charge_entry"
        indexes = [
            models.Index(fields=["company", "order"], name="charge_entry_order_idx"),
            models.Index(fields=["source_type", "source_id"], name="charge_entry_source_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.source_type}:{self.amount} {self.currency_id}"
