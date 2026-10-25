"""Sale lines refer to the same company (A05)."""

from django.db import migrations

SAME_COMPANY = [
    ("warehouse_sale_line", "order", "orders"),
    ("warehouse_sale_line", "context", "activity_context"),
    ("warehouse_sale_line", "item", "warehouse_item"),
    ("warehouse_sale_line", "asset", "warehouse_asset"),
    ("warehouse_sale_line", "lot", "warehouse_lot"),
    ("warehouse_sale_line", "vehicle", "vehicle_card"),
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
    dependencies = [("warehouse", "0011_sales"), ("orders", "0001_initial")]

    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
