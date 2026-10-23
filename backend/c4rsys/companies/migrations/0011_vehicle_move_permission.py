"""Permission to move vehicles between locations, in the sets «Company administrator» and
«Rental manager» (decision 23.10.2026). Existing assignments keep their own copy of codes
(§ 23.1.3): employees get it when their rights are assigned again."""

from typing import Any

from django.db import migrations

CODE = "vehicle.move"
SETS = ("company_admin", "rental_manager")


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    permission.objects.get_or_create(code=CODE, defaults={"section": "vehicle"})
    for code in SETS:
        target = permission_set.objects.get(code=code, is_system=True)
        item.objects.get_or_create(permission_set=target, permission_id=CODE)


def remove(apps: Any, schema_editor: Any) -> None:
    item = apps.get_model("companies", "PermissionSetItem")
    item.objects.filter(permission_id=CODE).delete()
    apps.get_model("companies", "Permission").objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0010_location_deactivated_at"),
    ]

    operations = [
        migrations.RunPython(add, remove),
    ]
