"""A rental links an order, vehicle, client, tariff and location of the same company (A05)."""

from django.db import migrations

FORWARD = """
ALTER TABLE rental
    ADD CONSTRAINT rental_order_same_company_fk
    FOREIGN KEY (company_id, order_id) REFERENCES orders (company_id, id),
    ADD CONSTRAINT rental_vehicle_card_same_company_fk
    FOREIGN KEY (company_id, vehicle_card_id) REFERENCES vehicle_card (company_id, id),
    ADD CONSTRAINT rental_client_same_company_fk
    FOREIGN KEY (company_id, client_id) REFERENCES client_card (company_id, id),
    ADD CONSTRAINT rental_tariff_same_company_fk
    FOREIGN KEY (company_id, tariff_id) REFERENCES tariff (company_id, id),
    ADD CONSTRAINT rental_issue_location_same_company_fk
    FOREIGN KEY (company_id, issue_location_id) REFERENCES location (company_id, id);
"""

REVERSE = """
ALTER TABLE rental
    DROP CONSTRAINT IF EXISTS rental_issue_location_same_company_fk,
    DROP CONSTRAINT IF EXISTS rental_tariff_same_company_fk,
    DROP CONSTRAINT IF EXISTS rental_client_same_company_fk,
    DROP CONSTRAINT IF EXISTS rental_vehicle_card_same_company_fk,
    DROP CONSTRAINT IF EXISTS rental_order_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0005_rental"),
        ("orders", "0002_order_client_same_company"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
