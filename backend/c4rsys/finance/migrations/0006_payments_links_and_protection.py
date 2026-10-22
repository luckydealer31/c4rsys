"""Payments link records of the same company (A05). A posted movement is not changed or deleted
(§ 12.2.5, § 17.2.11); an allocation keeps its movement, order and amount — only its state may
change to cancelled — and is never deleted (§ 17.2.15)."""

from django.db import migrations

FORWARD = """
ALTER TABLE money_movement
    ADD CONSTRAINT money_movement_account_same_company_fk
    FOREIGN KEY (company_id, cash_account_id) REFERENCES cash_account (company_id, id),
    ADD CONSTRAINT money_movement_client_same_company_fk
    FOREIGN KEY (company_id, client_id) REFERENCES client_card (company_id, id);
ALTER TABLE payment_allocation
    ADD CONSTRAINT payment_allocation_movement_same_company_fk
    FOREIGN KEY (company_id, money_movement_id) REFERENCES money_movement (company_id, id),
    ADD CONSTRAINT payment_allocation_order_same_company_fk
    FOREIGN KEY (company_id, order_id) REFERENCES orders (company_id, id);

CREATE FUNCTION money_movement_protect() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.state = 'posted' THEN
        RAISE EXCEPTION 'posted money movement cannot be changed'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER money_movement_protected
BEFORE UPDATE OR DELETE ON money_movement
FOR EACH ROW EXECUTE FUNCTION money_movement_protect();

CREATE FUNCTION payment_allocation_protect() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.state <> 'active' OR NEW.state <> 'cancelled'
       OR (to_jsonb(NEW) - 'state') IS DISTINCT FROM (to_jsonb(OLD) - 'state') THEN
        RAISE EXCEPTION 'allocation can only be cancelled'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER payment_allocation_protected
BEFORE UPDATE OR DELETE ON payment_allocation
FOR EACH ROW EXECUTE FUNCTION payment_allocation_protect();
"""

REVERSE = """
DROP TRIGGER IF EXISTS payment_allocation_protected ON payment_allocation;
DROP FUNCTION IF EXISTS payment_allocation_protect();
DROP TRIGGER IF EXISTS money_movement_protected ON money_movement;
DROP FUNCTION IF EXISTS money_movement_protect();
ALTER TABLE payment_allocation
    DROP CONSTRAINT IF EXISTS payment_allocation_order_same_company_fk,
    DROP CONSTRAINT IF EXISTS payment_allocation_movement_same_company_fk;
ALTER TABLE money_movement
    DROP CONSTRAINT IF EXISTS money_movement_client_same_company_fk,
    DROP CONSTRAINT IF EXISTS money_movement_account_same_company_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0005_payments"),
        ("orders", "0002_order_client_same_company"),
        ("parties", "0002_client_contact_same_company"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
