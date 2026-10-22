"""Contexts and schedules belong to a location of the same company (A05, STAGE1_DATA_SCHEMA)."""

from django.db import migrations

FORWARD = """
ALTER TABLE activity_context
    ADD CONSTRAINT activity_context_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
ALTER TABLE location_schedule
    ADD CONSTRAINT location_schedule_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
"""

REVERSE = """
ALTER TABLE location_schedule DROP CONSTRAINT IF EXISTS location_schedule_location_same_company_fk;
ALTER TABLE activity_context DROP CONSTRAINT IF EXISTS activity_context_location_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0003_locations"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
