"""A return to the supplier refers to a receipt of the same company (A05)."""

from django.db import migrations

FORWARD = (
    "ALTER TABLE warehouse_operation ADD CONSTRAINT warehouse_operation_receipt_same_company_fk "
    "FOREIGN KEY (company_id, receipt_id) REFERENCES warehouse_receipt (company_id, id);"
)
REVERSE = (
    "ALTER TABLE warehouse_operation DROP CONSTRAINT IF EXISTS "
    "warehouse_operation_receipt_same_company_fk;"
)


class Migration(migrations.Migration):
    dependencies = [("warehouse", "0013_supplier_returns")]

    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
