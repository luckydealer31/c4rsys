"""The client of an order is a card of the same company (A05)."""

from django.db import migrations

FORWARD = """
ALTER TABLE orders
    ADD CONSTRAINT orders_client_same_company_fk
    FOREIGN KEY (company_id, client_id) REFERENCES client_card (company_id, id);
"""

REVERSE = "ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_client_same_company_fk;"


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0001_initial"),
        ("parties", "0002_client_contact_same_company"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
