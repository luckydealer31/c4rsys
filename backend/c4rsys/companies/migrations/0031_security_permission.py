"""§ 24.3.2: the separate security right of a company, in «Company administrator» (PROPOSAL)."""

from typing import Any

from django.db import migrations

CODE = "company.security"


def forward(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    item = apps.get_model("companies", "PermissionSetItem")
    admin = apps.get_model("companies", "PermissionSet").objects.get(
        code="company_admin", is_system=True
    )
    permission.objects.get_or_create(code=CODE, defaults={"section": "company"})
    item.objects.get_or_create(permission_set=admin, permission_id=CODE)


def backward(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(permission_id=CODE).delete()
    apps.get_model("companies", "Permission").objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0030_org_links_and_permissions")]

    operations = [migrations.RunPython(forward, backward)]
