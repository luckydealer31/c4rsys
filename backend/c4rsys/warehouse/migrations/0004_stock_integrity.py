"""Integrity of the stock (A05): records refer to the same company, and the movement journal is
append-only — a posted movement is corrected by a new one, never changed (§ 11.6.2)."""

from django.db import migrations

# (table, constraint, columns, referenced table)
SAME_COMPANY = [
    ("warehouse_purchase", "supplier", "counterparty"),
    ("warehouse_purchase_line", "purchase", "warehouse_purchase"),
    ("warehouse_purchase_line", "item", "warehouse_item"),
    ("warehouse_receipt", "context", "activity_context"),
    ("warehouse_receipt", "supplier", "counterparty"),
    ("warehouse_receipt", "purchase", "warehouse_purchase"),
    ("warehouse_receipt_line", "receipt", "warehouse_receipt"),
    ("warehouse_receipt_line", "item", "warehouse_item"),
    ("warehouse_receipt_line", "lot", "warehouse_lot"),
    ("warehouse_asset", "item", "warehouse_item"),
    ("warehouse_asset", "context", "activity_context"),
    ("warehouse_lot", "item", "warehouse_item"),
    ("warehouse_lot", "context", "activity_context"),
    ("warehouse_lot_layer", "lot", "warehouse_lot"),
    ("warehouse_lot_stock", "lot", "warehouse_lot"),
    ("warehouse_movement", "context", "activity_context"),
    ("warehouse_movement", "item", "warehouse_item"),
    ("warehouse_movement", "asset", "warehouse_asset"),
    ("warehouse_movement", "lot", "warehouse_lot"),
    ("warehouse_supplier_article", "supplier", "counterparty"),
    ("warehouse_supplier_article", "item", "warehouse_item"),
]

FORWARD = [
    f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_same_company_fk "
    f"FOREIGN KEY (company_id, {column}_id) REFERENCES {target} (company_id, id);"
    for table, column, target in SAME_COMPANY
]
REVERSE = [
    f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_{column}_same_company_fk;"
    for table, column, _ in SAME_COMPANY
]

JOURNAL = """
CREATE FUNCTION warehouse_movement_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'warehouse_movement is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER warehouse_movement_append_only
BEFORE UPDATE OR DELETE ON warehouse_movement
FOR EACH ROW EXECUTE FUNCTION warehouse_movement_forbid_change();
"""

JOURNAL_REVERSE = """
DROP TRIGGER IF EXISTS warehouse_movement_append_only ON warehouse_movement;
DROP FUNCTION IF EXISTS warehouse_movement_forbid_change();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("warehouse", "0003_stock_receipts_purchases"),
        ("parties", "0004_counterparty_contact_same_company"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
        migrations.RunSQL(sql=JOURNAL, reverse_sql=JOURNAL_REVERSE),
    ]
