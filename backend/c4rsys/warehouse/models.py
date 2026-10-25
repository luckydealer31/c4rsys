"""Catalog of the warehouse (ТЗ § 11.1).

The catalog is shared by the whole company; stock belongs to the warehouses of locations (§ 11.1.1).
The accounting method follows the base unit (§ 11.1.11): a piece or an instance is accounted
individually, divisible units by lots. The base unit never changes after creation.
"""

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company
from c4rsys.files.models import StoredFile
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
        # The customer's decision of 25.10.2026: «piece» and «instance» are one unit, the piece;
        # the list is extendable (§ 11.1.3, § 11.1.11).
        PIECE = "piece"
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
    # New or used (customer's decision of 25.10.2026); empty — not given.
    condition = models.CharField(max_length=10, blank=True, default="")
    # Optional data of the catalog card (§ 22.4.1).
    article = models.CharField(max_length=100, blank=True, default="")
    category = models.CharField(max_length=100, blank=True, default="")
    model_characteristic = models.CharField(max_length=200, blank=True, default="")
    photo = models.ForeignKey(
        StoredFile, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    photo_url = models.CharField(max_length=500, blank=True, default="")
    comment = models.TextField(blank=True, default="")
    external_id = models.CharField(max_length=100, blank=True, default="")
    # Created by an import as a possible duplicate of an existing card (§ 22.4.1).
    possible_duplicate = models.BooleanField(default=False)
    import_operation = models.ForeignKey(
        "ImportOperation", on_delete=models.PROTECT, null=True, blank=True, related_name="items"
    )
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
                    Q(unit="piece", accounting="individual")
                    | (~Q(unit="piece") & Q(accounting="batch"))
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


class ImportOperation(models.Model):
    """An import of one kind of data from an XLSX file (§ 22.2.1–22.2.7): checked first, posted
    after the user's confirmation, cancelled only as a whole while its records are unused. The
    source file is kept for six months from the first upload (§ 22.2.4)."""

    class Kind(models.TextChoices):
        ITEMS = "items"

    class State(models.TextChoices):
        CHECKED = "checked"
        POSTED = "posted"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    template_version = models.PositiveSmallIntegerField()
    file = models.ForeignKey(
        StoredFile, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    file_name = models.CharField(max_length=255, blank=True, default="")
    sha256 = models.CharField(max_length=64)
    state = models.CharField(max_length=20, choices=State.choices, default=State.CHECKED)
    # Rows with their codes, values, errors, matches and the chosen action.
    rows = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    posted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    loaded_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "warehouse_import"
        indexes = [models.Index(fields=["company", "sha256"], name="warehouse_import_sha_idx")]

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"


# --- Stock (§ 11.1.2–11.1.4, § 11.2, § 11.3) ---------------------------------------------------
#
# Stock lives only in the warehouses of locations (customer's decision of 25.10.2026): `context`
# is always an activity context of type «warehouse». Individual units are assets (AST), divisible
# materials are lots (LOT). Inside a lot the receipts keep their own cost (layers, § 11.2.13) and
# the physical stock is kept by packaging (§ 11.1.15). Every change is a line of the movement
# journal; posted records are not changed, a correction is a new record (§ 11.6.2).


class PurchaseOrder(models.Model):
    """A purchase made in advance and received in one or several receipts (§ 11.2.11–11.2.12).
    The supplier may stay unknown (customer's decision of 25.10.2026). Payments come with the
    finance stage (decision of 25.10.2026)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    number = models.PositiveIntegerField()
    supplier = models.ForeignKey(
        "parties.Counterparty", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    currency = models.CharField(max_length=3)
    comment = models.TextField(blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "warehouse_purchase"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="warehouse_purchase_company_id_uniq"
            ),
            models.UniqueConstraint(
                fields=["company", "number"], name="warehouse_purchase_number_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"purchase {self.number}"


class PurchaseLine(models.Model):
    """What is ordered. Received comes from posted receipts; the undelivered rest can be
    cancelled when no longer expected (§ 11.2.10)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    purchase = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    ordered_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    cancelled_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Per base unit; empty — «Cost not given» (§ 11.2.6), never zero by default.
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "warehouse_purchase_line"
        constraints = [
            models.CheckConstraint(
                condition=Q(ordered_quantity__gt=0), name="warehouse_purchase_line_positive"
            ),
            models.CheckConstraint(
                condition=Q(cancelled_quantity__gte=0)
                & Q(cancelled_quantity__lte=models.F("ordered_quantity")),
                name="warehouse_purchase_line_cancelled_range",
            ),
            models.CheckConstraint(
                condition=Q(unit_cost__isnull=True) | Q(unit_cost__gte=0),
                name="warehouse_purchase_line_cost_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.purchase_id}:{self.item_id}"


class Receipt(models.Model):
    """A receipt to the warehouse of a location (§ 11.2.7): a draft is changed or deleted freely,
    posting increases the stock by what was actually received (§ 11.2.3, § 11.6.2)."""

    class Source(models.TextChoices):
        PURCHASE = "purchase"  # bought from a counterparty, with or without a purchase
        INITIAL = "initial"  # initial stock, no supplier (§ 11.2.8)

    class State(models.TextChoices):
        DRAFT = "draft"
        POSTED = "posted"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    number = models.PositiveIntegerField()
    context = models.ForeignKey(
        "companies.ActivityContext", on_delete=models.PROTECT, related_name="+"
    )
    source = models.CharField(max_length=20, choices=Source.choices)
    supplier = models.ForeignKey(
        "parties.Counterparty", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    purchase = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="receipts"
    )
    document = models.ForeignKey(
        StoredFile, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    currency = models.CharField(max_length=3)
    comment = models.TextField(blank=True, default="")
    state = models.CharField(max_length=10, choices=State.choices, default=State.DRAFT)
    received_at = models.DateTimeField(default=now)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    posted_by_user_id = models.UUIDField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "warehouse_receipt"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="warehouse_receipt_company_id_uniq"
            ),
            models.UniqueConstraint(
                fields=["company", "number"], name="warehouse_receipt_number_uniq"
            ),
            models.CheckConstraint(
                condition=~Q(source="initial")
                | (Q(supplier__isnull=True) & Q(purchase__isnull=True)),
                name="warehouse_receipt_initial_without_supplier",
            ),
            models.CheckConstraint(
                condition=Q(state="draft", posted_at__isnull=True)
                | Q(state="posted", posted_at__isnull=False),
                name="warehouse_receipt_posted_at",
            ),
        ]

    def __str__(self) -> str:
        return f"receipt {self.number}"


class ReceiptLine(models.Model):
    """One item of a receipt. Quantity is what was actually received in the base unit; for a
    material it may be entered as a number of closed packages (§ 11.1.15). An individual item
    gets one asset per unit, with optional serial numbers (§ 11.2.3). A material goes to an
    existing lot or to a new one (§ 11.2.14, § 11.2.16)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    purchase_line = models.ForeignKey(
        PurchaseLine, on_delete=models.PROTECT, null=True, blank=True, related_name="receipt_lines"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    packaging = models.ForeignKey(
        Packaging, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    packs = models.PositiveIntegerField(null=True, blank=True)
    expected_quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # The quantity after corrections of the posted line (§ 11.6.2); `quantity` stays as posted.
    corrected_quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    tags = ArrayField(models.CharField(max_length=50), default=list, blank=True)
    serial_numbers = ArrayField(models.CharField(max_length=100), default=list, blank=True)
    # Lot: an existing one, or the data of a new one (created at posting).
    lot = models.ForeignKey(
        "Lot", on_delete=models.PROTECT, null=True, blank=True, related_name="receipt_lines"
    )
    new_lot = models.BooleanField(default=False)
    lot_external_number = models.CharField(max_length=100, blank=True, default="")
    lot_production_date = models.DateField(null=True, blank=True)
    lot_expiry_date = models.DateField(null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "warehouse_receipt_line"
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="warehouse_receipt_line_positive"
            ),
            models.CheckConstraint(
                condition=Q(unit_cost__isnull=True) | Q(unit_cost__gte=0),
                name="warehouse_receipt_line_cost_not_negative",
            ),
            models.CheckConstraint(
                condition=(Q(packaging__isnull=True) & Q(packs__isnull=True))
                | (Q(packaging__isnull=False) & Q(packs__gt=0)),
                name="warehouse_receipt_line_packs",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.receipt_id}:{self.item_id}"


class Asset(models.Model):
    """An individually accounted unit with its own AST ID (§ 11.1.2, § 2.6.1)."""

    class State(models.TextChoices):
        IN_STOCK = "in_stock"
        ISSUED = "issued"  # handed out with a return possible (§ 11.4.1)
        CONSUMED = "consumed"  # actually used (§ 11.4.1, § 11.4.4)
        IN_TRANSIT = "in_transit"  # sent and not yet received (§ 11.5.1)
        WRITTEN_OFF = "written_off"  # § 11.3.4: the record and history stay
        CANCELLED = "cancelled"  # removed by a correction of its receipt (§ 11.6.2)

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    public_id = models.CharField(max_length=40, unique=True)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="assets")
    # Where an issued or consumed unit went: a vehicle of the company (decision of 25.10.2026).
    vehicle = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    context = models.ForeignKey(
        "companies.ActivityContext", on_delete=models.PROTECT, related_name="+"
    )
    state = models.CharField(max_length=20, choices=State.choices, default=State.IN_STOCK)
    serial_number = models.CharField(max_length=100, blank=True, default="")
    tags = ArrayField(models.CharField(max_length=50), default=list, blank=True)
    # Purchase cost of this very unit (§ 11.2.13); empty — «Cost not given» (§ 11.2.6).
    purchase_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    currency = models.CharField(max_length=3)
    receipt_line = models.ForeignKey(
        ReceiptLine, on_delete=models.PROTECT, null=True, blank=True, related_name="assets"
    )
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "warehouse_asset"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="warehouse_asset_company_id_uniq"
            ),
        ]

    def __str__(self) -> str:
        return self.public_id


class Lot(models.Model):
    """A lot of a divisible material with its LOT ID (§ 11.1.3, § 11.2.16). The external number
    of the producer or supplier does not replace the LOT ID and does not merge lots."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    public_id = models.CharField(max_length=40, unique=True)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="lots")
    context = models.ForeignKey(
        "companies.ActivityContext", on_delete=models.PROTECT, related_name="+"
    )
    # A child lot separated for a move to another warehouse (§ 11.3.3) — part С3.
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    external_number = models.CharField(max_length=100, blank=True, default="")
    production_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    tags = ArrayField(models.CharField(max_length=50), default=list, blank=True)
    currency = models.CharField(max_length=3)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "warehouse_lot"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="warehouse_lot_company_id_uniq"),
        ]
        indexes = [models.Index(fields=["item", "external_number"], name="warehouse_lot_ext_idx")]

    def __str__(self) -> str:
        return self.public_id


class LotLayer(models.Model):
    """What one receipt brought into a lot, with its own cost (§ 11.2.13–11.2.14). The rest of
    the earlier layer is used first."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="layers")
    # A layer comes from a receipt, or — in a child lot — from a layer of the parent (§ 11.3.3).
    receipt_line = models.ForeignKey(
        ReceiptLine, on_delete=models.PROTECT, null=True, blank=True, related_name="layers"
    )
    source_layer = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="child_layers"
    )
    received_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    remaining = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    received_at = models.DateTimeField()

    class Meta:
        db_table = "warehouse_lot_layer"
        constraints = [
            models.CheckConstraint(
                condition=Q(remaining__gte=0) & Q(remaining__lte=models.F("received_quantity")),
                name="warehouse_lot_layer_remaining_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.lot_id}:{self.received_quantity}"


class LotStock(models.Model):
    """Physical stock of a lot by packaging; no packaging — loose material in the base unit
    (§ 11.1.15). It never goes below zero (§ 11.6.2, § 11.8.1)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="stock")
    packaging = models.ForeignKey(
        Packaging, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        db_table = "warehouse_lot_stock"
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gte=0), name="warehouse_lot_stock_not_negative"
            ),
            models.UniqueConstraint(
                fields=["lot", "packaging"], name="warehouse_lot_stock_uniq", nulls_distinct=False
            ),
        ]

    def __str__(self) -> str:
        return f"{self.lot_id}:{self.packaging_id}:{self.quantity}"


class StockMovement(models.Model):
    """The movement journal of the stock: append-only, a database trigger rejects changes."""

    class Kind(models.TextChoices):
        RECEIPT = "receipt"
        ISSUE = "issue"
        RETURN = "return"
        CONSUME = "consume"
        MOVE_OUT = "move_out"
        MOVE_IN = "move_in"
        MOVE_BACK = "move_back"
        WRITE_OFF = "write_off"
        REPACK = "repack"
        OPEN = "open"
        CORRECTION = "correction"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    context = models.ForeignKey(
        "companies.ActivityContext", on_delete=models.PROTECT, related_name="+"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    asset = models.ForeignKey(
        Asset, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    packaging = models.ForeignKey(
        Packaging, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    receipt_line = models.ForeignKey(
        ReceiptLine, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    operation_line = models.ForeignKey(
        "OperationLine", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    opened_package = models.ForeignKey(
        "OpenedPackage", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    actor_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "warehouse_movement"
        constraints = [
            models.CheckConstraint(
                condition=(Q(asset__isnull=False) & Q(lot__isnull=True))
                | (Q(asset__isnull=True) & Q(lot__isnull=False)),
                name="warehouse_movement_asset_or_lot",
            ),
            models.CheckConstraint(condition=~Q(quantity=0), name="warehouse_movement_not_zero"),
        ]
        indexes = [
            models.Index(
                fields=["company", "item", "created_at"], name="warehouse_movement_item_idx"
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.quantity}"


class SupplierArticle(models.Model):
    """An article of a supplier linked to an item of the company (§ 11.2.17). It does not create
    a separate item or lot; the actual purchase price stays in the receipt."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    supplier = models.ForeignKey("parties.Counterparty", on_delete=models.PROTECT, related_name="+")
    article = models.CharField(max_length=100)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="supplier_articles")
    supplier_name = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "warehouse_supplier_article"
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "article"], name="warehouse_supplier_article_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.supplier_id}:{self.article}"


class OpenedPackage(models.Model):
    """An opened package of a lot: its own rest of the content, not merged with other opened
    packages (§ 11.1.17). It keeps the LOT and the expiry of the lot; at zero it stays in the
    history."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="opened_packages")
    packaging = models.ForeignKey(Packaging, on_delete=models.PROTECT, related_name="+")
    remaining = models.DecimalField(max_digits=12, decimal_places=2)
    label = models.CharField(max_length=50, blank=True, default="")
    opened_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "warehouse_opened_package"
        constraints = [
            models.CheckConstraint(
                condition=Q(remaining__gte=0), name="warehouse_opened_package_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.lot_id}:{self.remaining}"


class WriteOffReason(models.Model):
    """A reason of a write-off (§ 11.3.4). The starter reasons have a code and a translated name;
    a renamed or own reason shows its name as entered. Reasons are archived, not deleted."""

    class State(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    code = models.CharField(max_length=30, blank=True, default="")
    name = models.CharField(max_length=100, blank=True, default="")
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "warehouse_write_off_reason"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="warehouse_reason_company_id_uniq"
            ),
            models.UniqueConstraint(
                fields=["company", "code"],
                condition=~Q(code=""),
                name="warehouse_reason_code_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(code="") | ~Q(name=""), name="warehouse_reason_named"
            ),
        ]

    def __str__(self) -> str:
        return self.name or self.code


class WarehouseSettings(models.Model):
    """Settings of the warehouse of a company. Expired lots (§ 11.2.15): a warning with an
    explicit confirmation by default, or a full ban on issue and use."""

    class ExpiredPolicy(models.TextChoices):
        WARN = "warn"
        FORBID = "forbid"

    company = models.OneToOneField(
        Company, on_delete=models.PROTECT, primary_key=True, related_name="+"
    )
    expired_policy = models.CharField(
        max_length=10, choices=ExpiredPolicy.choices, default=ExpiredPolicy.WARN
    )
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "warehouse_settings"

    def __str__(self) -> str:
        return f"{self.company_id}:{self.expired_policy}"


class StockOperation(models.Model):
    """A posted operation with the stock (§ 11.3–11.6): issue with a return possible and actual
    consumption on a vehicle of the company (§ 11.4.1; decision of 25.10.2026), a move between
    warehouses (§ 11.5), a write-off (§ 11.3.4) and a repacking (§ 11.3.3)."""

    class Kind(models.TextChoices):
        ISSUE = "issue"
        CONSUME = "consume"
        MOVE = "move"
        WRITE_OFF = "write_off"
        REPACK = "repack"

    class State(models.TextChoices):
        DONE = "done"
        IN_TRANSIT = "in_transit"  # a move sent and not yet received
        DISCREPANCY = "discrepancy"  # a move received with less than sent, awaiting a decision
        RECEIVED = "received"  # a move received in full or with the shortage settled

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    number = models.PositiveIntegerField()
    kind = models.CharField(max_length=20, choices=Kind.choices)
    state = models.CharField(max_length=20, choices=State.choices, default=State.DONE)
    context = models.ForeignKey(
        "companies.ActivityContext", on_delete=models.PROTECT, related_name="+"
    )
    target_context = models.ForeignKey(
        "companies.ActivityContext",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    vehicle = models.ForeignKey(
        "fleet.VehicleCard", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    reason = models.ForeignKey(
        WriteOffReason, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # A write-off of losses made by a repacking or a settled shortage of a move.
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    photos = ArrayField(models.UUIDField(), default=list, blank=True)
    comment = models.TextField(blank=True, default="")
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    received_by_user_id = models.UUIDField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "warehouse_operation"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "id"], name="warehouse_operation_company_id_uniq"
            ),
            models.UniqueConstraint(
                fields=["company", "number"], name="warehouse_operation_number_uniq"
            ),
            models.CheckConstraint(
                condition=~Q(kind__in=["issue", "consume"]) | Q(vehicle__isnull=False),
                name="warehouse_operation_vehicle_for_issue",
            ),
            models.CheckConstraint(
                condition=~Q(kind="move") | Q(target_context__isnull=False),
                name="warehouse_operation_target_for_move",
            ),
            models.CheckConstraint(
                condition=~Q(kind="write_off") | Q(reason__isnull=False),
                name="warehouse_operation_reason_for_write_off",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.number}"


class OperationLine(models.Model):
    """One position of an operation: a unit (asset) or a quantity of a lot taken from a form —
    closed packages of a packaging, an opened package or loose material. For an issue the
    returned and consumed parts are kept; for a move the received and settled parts; for a
    repacking `output` marks what was obtained."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    operation = models.ForeignKey(StockOperation, on_delete=models.PROTECT, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    asset = models.ForeignKey(
        Asset, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    packaging = models.ForeignKey(
        Packaging, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    opened_package = models.ForeignKey(
        OpenedPackage, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    output = models.BooleanField(default=False)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    returned_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    consumed_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    received_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    settled_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    target_lot = models.ForeignKey(
        Lot, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    expired_confirmed = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "warehouse_operation_line"
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="warehouse_operation_line_positive"
            ),
            models.CheckConstraint(
                condition=(Q(asset__isnull=False) & Q(lot__isnull=True))
                | (Q(asset__isnull=True) & Q(lot__isnull=False)),
                name="warehouse_operation_line_asset_or_lot",
            ),
            models.CheckConstraint(
                condition=Q(returned_quantity__gte=0)
                & Q(consumed_quantity__gte=0)
                & Q(quantity__gte=models.F("returned_quantity") + models.F("consumed_quantity")),
                name="warehouse_operation_line_issue_range",
            ),
            models.CheckConstraint(
                condition=Q(received_quantity__gte=0)
                & Q(settled_quantity__gte=0)
                & Q(quantity__gte=models.F("received_quantity") + models.F("settled_quantity")),
                name="warehouse_operation_line_move_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.operation_id}:{self.quantity}"


class LayerUse(models.Model):
    """Which receipt layers an outgoing quantity of a lot came from, earlier receipts first
    (§ 11.2.13), with their purchase cost. The cost is supplemented when a cost that was not
    given is entered later (§ 11.2.5); `quantity` goes down when issued material is returned."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    line = models.ForeignKey(OperationLine, on_delete=models.PROTECT, related_name="layer_uses")
    layer = models.ForeignKey(LotLayer, on_delete=models.PROTECT, related_name="uses")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    received_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    class Meta:
        db_table = "warehouse_layer_use"
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gte=0) & Q(received_quantity__gte=0),
                name="warehouse_layer_use_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.layer_id}:{self.quantity}"


class ReceiptCorrection(models.Model):
    """A correction of a posted receipt line (§ 11.6.2): the original record stays, the change,
    its reason and author are kept."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    line = models.ForeignKey(ReceiptLine, on_delete=models.PROTECT, related_name="corrections")
    quantity_before = models.DecimalField(max_digits=12, decimal_places=2)
    quantity_after = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost_before = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    unit_cost_after = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    reason = models.CharField(max_length=500)
    user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "warehouse_receipt_correction"

    def __str__(self) -> str:
        return f"{self.line_id}:{self.quantity_before}->{self.quantity_after}"
