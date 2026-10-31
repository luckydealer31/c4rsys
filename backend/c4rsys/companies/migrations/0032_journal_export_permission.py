"""§ 24.4.5: the export of a journal needs its own right; in «Company administrator» (PROPOSAL)."""

from typing import Any

from django.db import migrations

CODE = "employee.journal.export"


def forward(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    item = apps.get_model("companies", "PermissionSetItem")
    admin = apps.get_model("companies", "PermissionSet").objects.get(
        code="company_admin", is_system=True
    )
    permission.objects.get_or_create(code=CODE, defaults={"section": "employee"})
    item.objects.get_or_create(permission_set=admin, permission_id=CODE)


def backward(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(permission_id=CODE).delete()
    apps.get_model("companies", "Permission").objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0031_security_permission")]

    operations = [migrations.RunPython(forward, backward)]
