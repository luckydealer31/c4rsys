"""Prices and minimums refer to the same company (A05)."""

from django.db import migrations

SAME_COMPANY = [
    ("warehouse_sale_price", "item", "warehouse_item"),
    ("warehouse_sale_price", "lot", "warehouse_lot"),
    ("warehouse_vehicle_price", "vehicle", "vehicle_card"),
    ("warehouse_minimum_stock", "item", "warehouse_item"),
    ("warehouse_minimum_stock", "context", "activity_context"),
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
    dependencies = [("warehouse", "0009_prices")]

    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
