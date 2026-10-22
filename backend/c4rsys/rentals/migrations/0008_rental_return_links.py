"""Return locations of a rental belong to the same company (A05)."""

from django.db import migrations

FORWARD = """
ALTER TABLE rental
    ADD CONSTRAINT rental_planned_return_location_same_company_fk
    FOREIGN KEY (company_id, planned_return_location_id) REFERENCES location (company_id, id),
    ADD CONSTRAINT rental_return_location_same_company_fk
    FOREIGN KEY (company_id, return_location_id) REFERENCES location (company_id, id);
"""

REVERSE = """
ALTER TABLE rental
    DROP CONSTRAINT IF EXISTS rental_return_location_same_company_fk,
    DROP CONSTRAINT IF EXISTS rental_planned_return_location_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0007_rental_return"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
