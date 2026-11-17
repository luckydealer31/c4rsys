"""The right «Manage tags» (customer's decision of 17.11.2026): only in «Company administrator»
(PROPOSAL). Existing assignments keep their own copy of codes (§ 23.1.3)."""

from typing import Any

from django.db import migrations

CODE = "tags.manage"


def add(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "Permission").objects.get_or_create(
        code=CODE, defaults={"section": "company"}
    )
    target = apps.get_model("companies", "PermissionSet").objects.get(
        code="company_admin", is_system=True
    )
    apps.get_model("companies", "PermissionSetItem").objects.get_or_create(
        permission_set=target, permission_id=CODE
    )


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(permission_id=CODE).delete()
    apps.get_model("companies", "Permission").objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tags", "0001_initial"),
        ("companies", "0040_company_phones"),
    ]

    operations = [migrations.RunPython(add, remove)]
