"""A contact belongs to a client card of the same company (A05)."""

from django.db import migrations

FORWARD = """
ALTER TABLE client_contact
    ADD CONSTRAINT client_contact_client_same_company_fk
    FOREIGN KEY (company_id, client_id) REFERENCES client_card (company_id, id);
"""

REVERSE = "ALTER TABLE client_contact DROP CONSTRAINT IF EXISTS client_contact_client_same_company_fk;"


class Migration(migrations.Migration):
    dependencies = [
        ("parties", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
