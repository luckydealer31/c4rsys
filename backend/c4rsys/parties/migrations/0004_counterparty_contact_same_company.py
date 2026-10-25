"""A contact belongs to a counterparty of the same company (A05)."""

from django.db import migrations

FORWARD = """
ALTER TABLE counterparty_contact
    ADD CONSTRAINT counterparty_contact_same_company_fk
    FOREIGN KEY (company_id, counterparty_id) REFERENCES counterparty (company_id, id);
"""

REVERSE = (
    "ALTER TABLE counterparty_contact DROP CONSTRAINT IF EXISTS counterparty_contact_same_company_fk;"
)


class Migration(migrations.Migration):
    dependencies = [("parties", "0003_counterparties")]

    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
