"""Permission to transfer an own vehicle from the garage to a warehouse (§ 8.1.5; decision of
24.10.2026), in the set «Company administrator» only (PROPOSAL)."""

from typing import Any

from django.db import migrations

CODE = "vehicle.transfer_to_warehouse"
SETS = ("company_admin",)


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    permission.objects.get_or_create(code=CODE, defaults={"section": "vehicle"})
    for code in SETS:
        target = permission_set.objects.get(code=code, is_system=True)
        item.objects.get_or_create(permission_set=target, permission_id=CODE)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(permission_id=CODE).delete()
    apps.get_model("companies", "Permission").objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0013_company_requisites")]

    operations = [migrations.RunPython(add, remove)]
