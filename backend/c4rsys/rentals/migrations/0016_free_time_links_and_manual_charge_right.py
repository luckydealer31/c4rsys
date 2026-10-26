"""Free intervals and manual charges belong to a rental of the same company; a manual charge is
never deleted and only its cancellation may be written (§ 13.5.1, § 24.4.6). The right «Charge
manually in a rental» goes to «Company administrator» and «Rental manager» (customer's decision
Т6 of 27.10.2026)."""

from typing import Any

from django.db import migrations

FORWARD = """
ALTER TABLE rental_free_interval
    ADD CONSTRAINT rental_free_interval_rental_same_company_fk
    FOREIGN KEY (company_id, rental_id) REFERENCES rental (company_id, id);
ALTER TABLE rental_manual_charge
    ADD CONSTRAINT rental_manual_charge_rental_same_company_fk
    FOREIGN KEY (company_id, rental_id) REFERENCES rental (company_id, id);

CREATE FUNCTION rental_manual_charge_only_cancel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'rental_manual_charge is never deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.cancelled_at IS NOT NULL
        OR (NEW.id, NEW.company_id, NEW.rental_id, NEW.kind, NEW.amount, NEW.currency_id,
            NEW.reason, NEW.created_by_user_id, NEW.created_at)
        IS DISTINCT FROM
           (OLD.id, OLD.company_id, OLD.rental_id, OLD.kind, OLD.amount, OLD.currency_id,
            OLD.reason, OLD.created_by_user_id, OLD.created_at)
    THEN
        RAISE EXCEPTION 'rental_manual_charge: only its cancellation may be written'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER rental_manual_charge_protect
BEFORE UPDATE OR DELETE ON rental_manual_charge
FOR EACH ROW EXECUTE FUNCTION rental_manual_charge_only_cancel();
"""

REVERSE = """
DROP TRIGGER IF EXISTS rental_manual_charge_protect ON rental_manual_charge;
DROP FUNCTION IF EXISTS rental_manual_charge_only_cancel();
ALTER TABLE rental_manual_charge
    DROP CONSTRAINT IF EXISTS rental_manual_charge_rental_same_company_fk;
ALTER TABLE rental_free_interval
    DROP CONSTRAINT IF EXISTS rental_free_interval_rental_same_company_fk;
"""

CODE = "rental.manual_charge"


def add(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "Permission").objects.get_or_create(
        code=CODE, defaults={"section": "rental"}
    )
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for set_code in ("company_admin", "rental_manager"):
        target = permission_set.objects.get(code=set_code, is_system=True)
        item.objects.get_or_create(permission_set=target, permission_id=CODE)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(permission_id=CODE).delete()
    apps.get_model("companies", "Permission").objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0015_free_time_and_manual_charges"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
        migrations.RunPython(add, remove),
    ]
