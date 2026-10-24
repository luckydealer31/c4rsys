"""Catalog of the warehouse (ТЗ § 11.1).

The catalog is shared by the whole company; stock belongs to contexts with storage (§ 11.1.1).
The accounting method follows the base unit (§ 11.1.11): a piece or an instance is accounted
individually, divisible units by lots. The base unit never changes after creation.
"""

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company
from c4rsys.fleet.models import VehicleMake, VehicleModel
from c4rsys.platform.ids import uuid7


class Manufacturer(models.Model):
    """System catalog of manufacturers (§ 11.1.12). Spellings of one manufacturer are kept as
    aliases for search. The first list is prepared separately; an empty one does not block
    work: a missing manufacturer is entered as «Other» text."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    name = models.CharField(max_length=150, unique=True)
    aliases = ArrayField(models.CharField(max_length=150), default=list, blank=True)

    class Meta:
        db_table = "manufacturer"

    def __str__(self) -> str:
        return self.name


class Item(models.Model):
    """An item of the catalog (§ 11.1.11–11.1.13)."""

    class Unit(models.TextChoices):
        # Units named by the ТЗ (§ 11.1.3, § 11.1.11); the list is extendable.
        PIECE = "piece"
        INSTANCE = "instance"
        LITER = "liter"
        KILOGRAM = "kilogram"
        METER = "meter"

    class Accounting(models.TextChoices):
        INDIVIDUAL = "individual"  # each unit with its own AST ID
        BATCH = "batch"  # lots (LOT ID), quantities up to hundredths

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=20, choices=Unit.choices)
    accounting = models.CharField(max_length=20, choices=Accounting.choices)
    manufacturer = models.ForeignKey(
        Manufacturer, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    manufacturer_other = models.CharField(max_length=150, blank=True, default="")
    # Attributes of spare parts (§ 11.1.13); empty — «Not given», not universal.
    side = models.CharField(max_length=10, blank=True, default="")
    longitudinal = models.CharField(max_length=10, blank=True, default="")
    vertical = models.CharField(max_length=10, blank=True, default="")
    placement = models.CharField(max_length=10, blank=True, default="")
    originality = models.CharField(max_length=10, blank=True, default="")
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "warehouse_item"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="warehouse_item_company_id_uniq"
            ),
            models.CheckConstraint(condition=~Q(name=""), name="warehouse_item_name_not_empty"),
            models.CheckConstraint(
                condition=(
                    Q(unit__in=["piece", "instance"], accounting="individual")
                    | (~Q(unit__in=["piece", "instance"]) & Q(accounting="batch"))
                ),
                name="warehouse_item_accounting_by_unit",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ItemApplicability(models.Model):
    """Applicability of an item to makes and models of vehicles (§ 11.1.13); several are allowed.
    It helps search and does not guarantee fitness for a particular modification."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="applicability")
    make = models.ForeignKey(VehicleMake, on_delete=models.PROTECT, related_name="+")
    model = models.ForeignKey(
        VehicleModel, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "warehouse_item_applicability"
        constraints = [
            models.UniqueConstraint(
                fields=["item", "make", "model"],
                name="warehouse_item_applicability_uniq",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item_id}:{self.make_id}"


class Packaging(models.Model):
    """A packaging of a divisible material: its content in the base unit (§ 11.1.15). It is not a
    lot: one packaging comes in different lots, one lot is kept in different packagings."""

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="packagings")
    name = models.CharField(max_length=100, blank=True, default="")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "warehouse_packaging"
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="warehouse_packaging_positive"
            ),
            models.UniqueConstraint(
                fields=["item", "quantity"],
                condition=Q(state="active"),
                name="warehouse_packaging_active_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item_id}:{self.quantity}"


class Barcode(models.Model):
    """A barcode of the company linked to an item and, for a material, to its packaging
    (§ 11.1.16). It does not define a lot."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    code = models.CharField(max_length=64)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="barcodes")
    packaging = models.ForeignKey(
        Packaging, on_delete=models.CASCADE, null=True, blank=True, related_name="barcodes"
    )
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "warehouse_barcode"
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="warehouse_barcode_uniq"),
        ]

    def __str__(self) -> str:
        return self.code
