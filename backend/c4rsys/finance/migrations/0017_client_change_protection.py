"""Corrections of the client of a payment (ТЗ § 17.2.12) link rows of the same company and are
never changed or deleted: the history of the client of a payment is the list of corrections."""

from django.db import migrations

FORWARD = """
ALTER TABLE payment_client_change
    ADD CONSTRAINT payment_client_change_movement_same_company_fk
    FOREIGN KEY (company_id, money_movement_id) REFERENCES money_movement (company_id, id),
    ADD CONSTRAINT payment_client_change_previous_same_company_fk
    FOREIGN KEY (company_id, previous_client_id) REFERENCES client_card (company_id, id),
    ADD CONSTRAINT payment_client_change_client_same_company_fk
    FOREIGN KEY (company_id, client_id) REFERENCES client_card (company_id, id);
CREATE TRIGGER payment_client_change_append_only
BEFORE UPDATE OR DELETE ON payment_client_change
FOR EACH ROW EXECUTE FUNCTION finance_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS payment_client_change_append_only ON payment_client_change;
ALTER TABLE payment_client_change
    DROP CONSTRAINT IF EXISTS payment_client_change_client_same_company_fk,
    DROP CONSTRAINT IF EXISTS payment_client_change_previous_same_company_fk,
    DROP CONSTRAINT IF EXISTS payment_client_change_movement_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [("finance", "0016_client_change_opening_chain_supplier_exchange")]

    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
