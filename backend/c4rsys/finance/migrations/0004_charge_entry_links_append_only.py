"""Charges link an order and a client of the same company (A05) and are append-only: a
correction is a new entry (§ 24.4.6)."""

from django.db import migrations

FORWARD = """
ALTER TABLE charge_entry
    ADD CONSTRAINT charge_entry_order_same_company_fk
    FOREIGN KEY (company_id, order_id) REFERENCES orders (company_id, id),
    ADD CONSTRAINT charge_entry_client_same_company_fk
    FOREIGN KEY (company_id, client_id) REFERENCES client_card (company_id, id);

CREATE FUNCTION charge_entry_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'charge_entry is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER charge_entry_append_only
BEFORE UPDATE OR DELETE ON charge_entry
FOR EACH ROW EXECUTE FUNCTION charge_entry_forbid_change();
"""

REVERSE = """
DROP TRIGGER IF EXISTS charge_entry_append_only ON charge_entry;
DROP FUNCTION IF EXISTS charge_entry_forbid_change();
ALTER TABLE charge_entry
    DROP CONSTRAINT IF EXISTS charge_entry_client_same_company_fk,
    DROP CONSTRAINT IF EXISTS charge_entry_order_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0003_charge_entry"),
        ("orders", "0002_order_client_same_company"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
