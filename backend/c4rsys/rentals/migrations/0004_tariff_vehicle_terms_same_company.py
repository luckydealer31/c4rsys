"""Terms link a tariff and a vehicle card of the same company (A05)."""

from django.db import migrations

FORWARD = """
ALTER TABLE tariff_vehicle_terms
    ADD CONSTRAINT tariff_vehicle_terms_tariff_same_company_fk
    FOREIGN KEY (company_id, tariff_id) REFERENCES tariff (company_id, id),
    ADD CONSTRAINT tariff_vehicle_terms_card_same_company_fk
    FOREIGN KEY (company_id, vehicle_card_id) REFERENCES vehicle_card (company_id, id);
"""

REVERSE = """
ALTER TABLE tariff_vehicle_terms
    DROP CONSTRAINT IF EXISTS tariff_vehicle_terms_card_same_company_fk,
    DROP CONSTRAINT IF EXISTS tariff_vehicle_terms_tariff_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0003_tariff_vehicle_terms"),
        ("fleet", "0002_vehicle_links_same_company"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
