"""Finance stage, part Ф1 (ТЗ § 17.1, § 23.2.7, § 23.7.6).

A transfer links money records of the same company. Downloaded rates and posted transfers are
never changed or deleted: an applied rate stays with its operation (§ 17.1.3), a transfer is
corrected by a reverse one (§ 17.1.6). New permissions: transfers — «Company administrator» and
«Accountant»; a manual rate — «Company administrator» only (who else gets it is OPEN).
"""

from typing import Any

from django.db import migrations

FORWARD = """
ALTER TABLE money_transfer
    ADD CONSTRAINT money_transfer_out_same_company_fk
    FOREIGN KEY (company_id, out_movement_id) REFERENCES money_movement (company_id, id),
    ADD CONSTRAINT money_transfer_in_same_company_fk
    FOREIGN KEY (company_id, in_movement_id) REFERENCES money_movement (company_id, id);

CREATE FUNCTION finance_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% rows cannot be changed or deleted', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$;
CREATE TRIGGER rate_fetch_append_only BEFORE UPDATE OR DELETE ON rate_fetch
FOR EACH ROW EXECUTE FUNCTION finance_append_only();
CREATE TRIGGER exchange_rate_append_only BEFORE UPDATE OR DELETE ON exchange_rate
FOR EACH ROW EXECUTE FUNCTION finance_append_only();
CREATE TRIGGER money_transfer_append_only BEFORE UPDATE OR DELETE ON money_transfer
FOR EACH ROW EXECUTE FUNCTION finance_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS money_transfer_append_only ON money_transfer;
DROP TRIGGER IF EXISTS exchange_rate_append_only ON exchange_rate;
DROP TRIGGER IF EXISTS rate_fetch_append_only ON rate_fetch;
DROP FUNCTION IF EXISTS finance_append_only();
ALTER TABLE money_transfer
    DROP CONSTRAINT IF EXISTS money_transfer_in_same_company_fk,
    DROP CONSTRAINT IF EXISTS money_transfer_out_same_company_fk;
"""

SETS = {
    "finance.transfer": ("company_admin", "accountant"),
    "finance.rate.override": ("company_admin",),
}


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code, sets in SETS.items():
        permission.objects.get_or_create(code=code, defaults={"section": "finance"})
        for set_code in sets:
            target = permission_set.objects.get(code=set_code, is_system=True)
            item.objects.get_or_create(permission_set=target, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=list(SETS)
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=list(SETS)).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0010_exchange_rates_and_transfers"),
        ("companies", "0017_import_permissions"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
        migrations.RunPython(add, remove),
    ]
