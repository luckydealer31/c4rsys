"""Deposits (ТЗ § 14.7): entries link rows of the same company and are never changed or deleted;
the right «Manage deposits» goes to «Company administrator» and «Accountant»."""

from typing import Any

from django.db import migrations

FORWARD = """
ALTER TABLE deposit_entry
    ADD CONSTRAINT deposit_entry_rental_same_company_fk
    FOREIGN KEY (company_id, rental_id) REFERENCES rental (company_id, id),
    ADD CONSTRAINT deposit_entry_client_same_company_fk
    FOREIGN KEY (company_id, client_id) REFERENCES client_card (company_id, id),
    ADD CONSTRAINT deposit_entry_movement_same_company_fk
    FOREIGN KEY (company_id, money_movement_id) REFERENCES money_movement (company_id, id),
    ADD CONSTRAINT deposit_entry_order_same_company_fk
    FOREIGN KEY (company_id, order_id) REFERENCES orders (company_id, id);
CREATE TRIGGER deposit_entry_append_only BEFORE UPDATE OR DELETE ON deposit_entry
FOR EACH ROW EXECUTE FUNCTION finance_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS deposit_entry_append_only ON deposit_entry;
ALTER TABLE deposit_entry
    DROP CONSTRAINT IF EXISTS deposit_entry_order_same_company_fk,
    DROP CONSTRAINT IF EXISTS deposit_entry_movement_same_company_fk,
    DROP CONSTRAINT IF EXISTS deposit_entry_client_same_company_fk,
    DROP CONSTRAINT IF EXISTS deposit_entry_rental_same_company_fk;
"""

CODE = "finance.deposit.manage"


def add(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "Permission").objects.get_or_create(
        code=CODE, defaults={"section": "finance"}
    )
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for set_code in ("company_admin", "accountant"):
        target = permission_set.objects.get(code=set_code, is_system=True)
        item.objects.get_or_create(permission_set=target, permission_id=CODE)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(permission_id=CODE).delete()
    apps.get_model("companies", "Permission").objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [("finance", "0014_deposits")]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
        migrations.RunPython(add, remove),
    ]
