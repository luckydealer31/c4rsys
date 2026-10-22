"""The position of an employment belongs to the same company (A05, STAGE1_DATA_SCHEMA)."""

from django.db import migrations

FORWARD = """
ALTER TABLE employment
    ADD CONSTRAINT employment_position_same_company_fk
    FOREIGN KEY (company_id, position_id) REFERENCES position (company_id, id);
"""

REVERSE = """
ALTER TABLE employment DROP CONSTRAINT IF EXISTS employment_position_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
