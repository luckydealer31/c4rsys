"""Location, contexts and readings of a vehicle card belong to the same company (A05)."""

from django.db import migrations

FORWARD = """
ALTER TABLE vehicle_card
    ADD CONSTRAINT vehicle_card_location_same_company_fk
    FOREIGN KEY (company_id, accounting_location_id) REFERENCES location (company_id, id);
ALTER TABLE vehicle_card_context
    ADD CONSTRAINT vehicle_card_context_card_same_company_fk
    FOREIGN KEY (company_id, vehicle_card_id) REFERENCES vehicle_card (company_id, id);
ALTER TABLE odometer_reading
    ADD CONSTRAINT odometer_reading_card_same_company_fk
    FOREIGN KEY (company_id, vehicle_card_id) REFERENCES vehicle_card (company_id, id);
"""

REVERSE = """
ALTER TABLE odometer_reading DROP CONSTRAINT IF EXISTS odometer_reading_card_same_company_fk;
ALTER TABLE vehicle_card_context
    DROP CONSTRAINT IF EXISTS vehicle_card_context_card_same_company_fk;
ALTER TABLE vehicle_card DROP CONSTRAINT IF EXISTS vehicle_card_location_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("fleet", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
