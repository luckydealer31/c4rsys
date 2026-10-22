"""A cash account belongs to a location of the same company (A05, STAGE1_DATA_SCHEMA)."""

from django.db import migrations

FORWARD = """
ALTER TABLE cash_account
    ADD CONSTRAINT cash_account_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
"""

REVERSE = "ALTER TABLE cash_account DROP CONSTRAINT IF EXISTS cash_account_location_same_company_fk;"


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
