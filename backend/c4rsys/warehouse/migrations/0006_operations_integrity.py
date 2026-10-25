"""Records of stock operations refer to the same company (A05)."""

from django.db import migrations

SAME_COMPANY = [
    ("warehouse_asset", "vehicle", "vehicle_card"),
    ("warehouse_opened_package", "lot", "warehouse_lot"),
    ("warehouse_operation", "context", "activity_context"),
    ("warehouse_operation", "target_context", "activity_context"),
    ("warehouse_operation", "vehicle", "vehicle_card"),
    ("warehouse_operation", "reason", "warehouse_write_off_reason"),
    ("warehouse_operation", "parent", "warehouse_operation"),
    ("warehouse_operation_line", "operation", "warehouse_operation"),
    ("warehouse_operation_line", "item", "warehouse_item"),
    ("warehouse_operation_line", "asset", "warehouse_asset"),
    ("warehouse_operation_line", "lot", "warehouse_lot"),
    ("warehouse_operation_line", "target_lot", "warehouse_lot"),
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


class Migration(migrations.Migration):
    dependencies = [("warehouse", "0005_operations")]

    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
