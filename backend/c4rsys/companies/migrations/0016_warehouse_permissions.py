"""Permissions of the warehouse (§ 23.7.3–23.7.5) and the starter set «Storekeeper» (§ 23.2.6);
«Company administrator» gets all of them, «Accountant» the setting of selling prices
(§ 23.7.4). Existing assignments keep their own copy of codes (§ 23.1.3)."""

from typing import Any

from django.db import migrations

from c4rsys.platform.ids import uuid7

CODES = [
    "warehouse.view",
    "warehouse.catalog.manage",
    "warehouse.purchase",
    "warehouse.receive",
    "warehouse.issue",
    "warehouse.move",
    "warehouse.repack",
    "warehouse.reserve",
    "warehouse.count",
    "warehouse.adjust.confirm",
    "warehouse.write_off",
    "warehouse.correct",
    "warehouse.tags.edit",
    "warehouse.sell",
    "warehouse.sale.correct",
    "warehouse.price.set",
    "warehouse.price.policy",
]
STOREKEEPER = [
    "warehouse.view",
    "warehouse.catalog.manage",
    "warehouse.receive",
    "warehouse.issue",
    "warehouse.move",
    "warehouse.repack",
    "warehouse.reserve",
    "warehouse.count",
    "warehouse.write_off",
    "warehouse.tags.edit",
    "warehouse.price.set",
]


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "warehouse"})
    admin = permission_set.objects.get(code="company_admin", is_system=True)
    for code in CODES:
        item.objects.get_or_create(permission_set=admin, permission_id=code)
    accountant = permission_set.objects.get(code="accountant", is_system=True)
    item.objects.get_or_create(permission_set=accountant, permission_id="warehouse.price.set")
    storekeeper, _ = permission_set.objects.get_or_create(
        code="storekeeper", is_system=True, defaults={"id": uuid7()}
    )
    for code in STOREKEEPER:
        item.objects.get_or_create(permission_set=storekeeper, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "PermissionSet").objects.filter(
        code="storekeeper", is_system=True
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0015_context_storage")]

    operations = [migrations.RunPython(add, remove)]
