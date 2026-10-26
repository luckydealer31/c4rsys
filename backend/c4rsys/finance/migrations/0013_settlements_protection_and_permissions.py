"""Settlements with parties (ТЗ § 11.2.4, § 11.4.2, § 17.2.3, § 17.2.10–17.2.17, § 17.3.3,
§ 23.2.7).

New records link rows of the same company. Settlement entries are never changed; offsets,
supplier allocations and expense shares may only be cancelled — a correction is a new record and
the old one stays in history. New permissions go to «Company administrator» and «Accountant»:
initial obligations and settlements of returns, set-off of available funds, corrections.
"""

from typing import Any

from django.db import migrations

FORWARD = """
ALTER TABLE money_movement
    ADD CONSTRAINT money_movement_counterparty_same_company_fk
    FOREIGN KEY (company_id, counterparty_id) REFERENCES counterparty (company_id, id),
    ADD CONSTRAINT money_movement_reverses_same_company_fk
    FOREIGN KEY (company_id, reverses_id) REFERENCES money_movement (company_id, id);
ALTER TABLE supplier_allocation
    ADD CONSTRAINT supplier_allocation_movement_same_company_fk
    FOREIGN KEY (company_id, money_movement_id) REFERENCES money_movement (company_id, id),
    ADD CONSTRAINT supplier_allocation_purchase_same_company_fk
    FOREIGN KEY (company_id, purchase_id) REFERENCES warehouse_purchase (company_id, id),
    ADD CONSTRAINT supplier_allocation_receipt_same_company_fk
    FOREIGN KEY (company_id, receipt_id) REFERENCES warehouse_receipt (company_id, id);
ALTER TABLE settlement_entry
    ADD CONSTRAINT settlement_entry_client_same_company_fk
    FOREIGN KEY (company_id, client_id) REFERENCES client_card (company_id, id),
    ADD CONSTRAINT settlement_entry_counterparty_same_company_fk
    FOREIGN KEY (company_id, counterparty_id) REFERENCES counterparty (company_id, id),
    ADD CONSTRAINT settlement_entry_cancels_same_company_fk
    FOREIGN KEY (company_id, cancels_id) REFERENCES settlement_entry (company_id, id);
ALTER TABLE finance_offset
    ADD CONSTRAINT finance_offset_client_same_company_fk
    FOREIGN KEY (company_id, client_id) REFERENCES client_card (company_id, id),
    ADD CONSTRAINT finance_offset_order_same_company_fk
    FOREIGN KEY (company_id, order_id) REFERENCES orders (company_id, id),
    ADD CONSTRAINT finance_offset_opening_same_company_fk
    FOREIGN KEY (company_id, opening_id) REFERENCES settlement_entry (company_id, id);
ALTER TABLE expense_share
    ADD CONSTRAINT expense_share_movement_same_company_fk
    FOREIGN KEY (company_id, money_movement_id) REFERENCES money_movement (company_id, id),
    ADD CONSTRAINT expense_share_vehicle_same_company_fk
    FOREIGN KEY (company_id, vehicle_id) REFERENCES vehicle_card (company_id, id),
    ADD CONSTRAINT expense_share_order_same_company_fk
    FOREIGN KEY (company_id, order_id) REFERENCES orders (company_id, id);

CREATE TRIGGER settlement_entry_append_only BEFORE UPDATE OR DELETE ON settlement_entry
FOR EACH ROW EXECUTE FUNCTION finance_append_only();

CREATE FUNCTION finance_only_cancel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.state <> 'active' OR NEW.state <> 'cancelled'
       OR (to_jsonb(NEW) - 'state' - 'cancelled_at' - 'cancelled_by_user_id')
          IS DISTINCT FROM (to_jsonb(OLD) - 'state' - 'cancelled_at' - 'cancelled_by_user_id') THEN
        RAISE EXCEPTION '% rows can only be cancelled', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER finance_offset_only_cancel BEFORE UPDATE OR DELETE ON finance_offset
FOR EACH ROW EXECUTE FUNCTION finance_only_cancel();
CREATE TRIGGER supplier_allocation_only_cancel BEFORE UPDATE OR DELETE ON supplier_allocation
FOR EACH ROW EXECUTE FUNCTION finance_only_cancel();
CREATE TRIGGER expense_share_only_cancel BEFORE UPDATE OR DELETE ON expense_share
FOR EACH ROW EXECUTE FUNCTION finance_only_cancel();
"""

REVERSE = """
DROP TRIGGER IF EXISTS expense_share_only_cancel ON expense_share;
DROP TRIGGER IF EXISTS supplier_allocation_only_cancel ON supplier_allocation;
DROP TRIGGER IF EXISTS finance_offset_only_cancel ON finance_offset;
DROP FUNCTION IF EXISTS finance_only_cancel();
DROP TRIGGER IF EXISTS settlement_entry_append_only ON settlement_entry;
ALTER TABLE expense_share
    DROP CONSTRAINT IF EXISTS expense_share_order_same_company_fk,
    DROP CONSTRAINT IF EXISTS expense_share_vehicle_same_company_fk,
    DROP CONSTRAINT IF EXISTS expense_share_movement_same_company_fk;
ALTER TABLE finance_offset
    DROP CONSTRAINT IF EXISTS finance_offset_opening_same_company_fk,
    DROP CONSTRAINT IF EXISTS finance_offset_order_same_company_fk,
    DROP CONSTRAINT IF EXISTS finance_offset_client_same_company_fk;
ALTER TABLE settlement_entry
    DROP CONSTRAINT IF EXISTS settlement_entry_cancels_same_company_fk,
    DROP CONSTRAINT IF EXISTS settlement_entry_counterparty_same_company_fk,
    DROP CONSTRAINT IF EXISTS settlement_entry_client_same_company_fk;
ALTER TABLE supplier_allocation
    DROP CONSTRAINT IF EXISTS supplier_allocation_receipt_same_company_fk,
    DROP CONSTRAINT IF EXISTS supplier_allocation_purchase_same_company_fk,
    DROP CONSTRAINT IF EXISTS supplier_allocation_movement_same_company_fk;
ALTER TABLE money_movement
    DROP CONSTRAINT IF EXISTS money_movement_reverses_same_company_fk,
    DROP CONSTRAINT IF EXISTS money_movement_counterparty_same_company_fk;
"""

CODES = ["finance.settlement.manage", "finance.offset", "finance.correct"]


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "finance"})
    for set_code in ("company_admin", "accountant"):
        target = permission_set.objects.get(code=set_code, is_system=True)
        for code in CODES:
            item.objects.get_or_create(permission_set=target, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0012_settlements"),
        ("warehouse", "0001_catalog"),
        ("parties", "0002_client_contact_same_company"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
        migrations.RunPython(add, remove),
    ]
