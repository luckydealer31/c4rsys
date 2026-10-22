"""Cash accounts are not bound to locations (ТЗ § 17.2.5, clarification of 23.10.2026)."""

from django.db import migrations

DROP_FK = "ALTER TABLE cash_account DROP CONSTRAINT IF EXISTS cash_account_location_same_company_fk;"
ADD_FK = """
ALTER TABLE cash_account
    ADD CONSTRAINT cash_account_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0006_payments_links_and_protection"),
    ]

    operations = [
        migrations.RunSQL(sql=DROP_FK, reverse_sql=ADD_FK),
        migrations.RemoveField(model_name="cashaccount", name="location"),
    ]
