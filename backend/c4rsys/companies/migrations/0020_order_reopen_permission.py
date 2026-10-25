"""The right to cancel the closing of an order (§ 12.2.3); only in «Company administrator»
(PROPOSAL), assigned to others separately."""

from typing import Any

from django.db import migrations

CODES = ["order.reopen"]


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    item = apps.get_model("companies", "PermissionSetItem")
    admin = apps.get_model("companies", "PermissionSet").objects.get(
        code="company_admin", is_system=True
    )
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "order"})
        item.objects.get_or_create(permission_set=admin, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0019_counterparty_permissions")]

    operations = [migrations.RunPython(add, remove)]
