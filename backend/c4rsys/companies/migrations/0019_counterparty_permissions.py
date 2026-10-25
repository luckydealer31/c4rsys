"""Permissions of counterparties (§ 23.8.2): viewing, creating, changing, archiving and restoring.
«Company administrator» gets all of them; «Storekeeper» viewing and creating, so a missing
supplier is added right at a receipt (§ 11.2.8) — PROPOSAL."""

from typing import Any

from django.db import migrations

CODES = [
    "counterparty.view",
    "counterparty.create",
    "counterparty.edit",
    "counterparty.archive",
    "counterparty.restore",
]
STOREKEEPER = ["counterparty.view", "counterparty.create"]


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "counterparty"})
    admin = permission_set.objects.get(code="company_admin", is_system=True)
    for code in CODES:
        item.objects.get_or_create(permission_set=admin, permission_id=code)
    storekeeper = permission_set.objects.get(code="storekeeper", is_system=True)
    for code in STOREKEEPER:
        item.objects.get_or_create(permission_set=storekeeper, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0018_drop_context_storage")]

    operations = [migrations.RunPython(add, remove)]
