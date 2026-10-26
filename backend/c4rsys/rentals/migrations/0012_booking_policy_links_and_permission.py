"""Bookings (ТЗ § 14.1, § 14.8.1): the auto-cancel policy names a location of the same company;
the right «Book» goes to «Company administrator» and «Rental manager» (§ 23.2.2)."""

from typing import Any

from django.db import migrations

FORWARD = """
ALTER TABLE rental_policy
    ADD CONSTRAINT rental_policy_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
"""

REVERSE = """
ALTER TABLE rental_policy DROP CONSTRAINT IF EXISTS rental_policy_location_same_company_fk;
"""

CODE = "rental.book"


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
        ("rentals", "0011_bookings"),
        ("companies", "0017_import_permissions"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
        migrations.RunPython(add, remove),
    ]
