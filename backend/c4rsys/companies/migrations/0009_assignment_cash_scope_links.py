"""Listed cash accounts of an assignment belong to the same company (A05)."""

from django.db import migrations

FORWARD = """
ALTER TABLE permission_assignment_cash_account
    ADD CONSTRAINT permission_assignment_cash_account_assignment_same_company_fk
    FOREIGN KEY (company_id, assignment_id) REFERENCES permission_assignment (company_id, id),
    ADD CONSTRAINT permission_assignment_cash_account_account_same_company_fk
    FOREIGN KEY (company_id, cash_account_id) REFERENCES cash_account (company_id, id);
"""

REVERSE = """
ALTER TABLE permission_assignment_cash_account
    DROP CONSTRAINT IF EXISTS permission_assignment_cash_account_account_same_company_fk,
    DROP CONSTRAINT IF EXISTS permission_assignment_cash_account_assignment_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0008_assignment_cash_scope_fields"),
        ("finance", "0002_cash_account_location_same_company"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
