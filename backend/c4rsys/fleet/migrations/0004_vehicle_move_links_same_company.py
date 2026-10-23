"""A vehicle move refers to records of the same company (A05, STAGE1_DATA_SCHEMA)."""

from django.db import migrations

FORWARD = """
ALTER TABLE vehicle_move
    ADD CONSTRAINT vehicle_move_card_same_company_fk
    FOREIGN KEY (company_id, vehicle_card_id) REFERENCES vehicle_card (company_id, id),
    ADD CONSTRAINT vehicle_move_order_same_company_fk
    FOREIGN KEY (company_id, order_id) REFERENCES orders (company_id, id),
    ADD CONSTRAINT vehicle_move_from_same_company_fk
    FOREIGN KEY (company_id, from_location_id) REFERENCES location (company_id, id),
    ADD CONSTRAINT vehicle_move_to_same_company_fk
    FOREIGN KEY (company_id, to_location_id) REFERENCES location (company_id, id),
    ADD CONSTRAINT vehicle_move_driver_same_company_fk
    FOREIGN KEY (company_id, driver_employment_id) REFERENCES employment (company_id, id);
"""

REVERSE = """
ALTER TABLE vehicle_move
    DROP CONSTRAINT IF EXISTS vehicle_move_card_same_company_fk,
    DROP CONSTRAINT IF EXISTS vehicle_move_order_same_company_fk,
    DROP CONSTRAINT IF EXISTS vehicle_move_from_same_company_fk,
    DROP CONSTRAINT IF EXISTS vehicle_move_to_same_company_fk,
    DROP CONSTRAINT IF EXISTS vehicle_move_driver_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("fleet", "0003_vehicle_move"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
