"""Ranges and locations of a tariff belong to the same company (A05, STAGE1_DATA_SCHEMA)."""

from django.db import migrations

FORWARD = """
ALTER TABLE tariff_range
    ADD CONSTRAINT tariff_range_tariff_same_company_fk
    FOREIGN KEY (company_id, tariff_id) REFERENCES tariff (company_id, id);
ALTER TABLE tariff_location
    ADD CONSTRAINT tariff_location_tariff_same_company_fk
    FOREIGN KEY (company_id, tariff_id) REFERENCES tariff (company_id, id),
    ADD CONSTRAINT tariff_location_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
"""

REVERSE = """
ALTER TABLE tariff_location
    DROP CONSTRAINT IF EXISTS tariff_location_location_same_company_fk,
    DROP CONSTRAINT IF EXISTS tariff_location_tariff_same_company_fk;
ALTER TABLE tariff_range DROP CONSTRAINT IF EXISTS tariff_range_tariff_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
