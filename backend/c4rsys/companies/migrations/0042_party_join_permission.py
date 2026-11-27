"""The right to say that two cards are of one person or company (customer's decision of
28.11.2026). In «Company administrator» (PROPOSAL)."""

from typing import Any

from django.db import migrations

CODE = "party.join"


def add(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "Permission").objects.get_or_create(
        code=CODE, defaults={"section": "client"}
    )
    target = (
        apps.get_model("companies", "PermissionSet")
        .objects.filter(code="company_admin", is_system=True)
        .first()
    )
    if target is not None:
        apps.get_model("companies", "PermissionSetItem").objects.get_or_create(
            permission_set=target, permission_id=CODE
        )


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(permission_id=CODE).delete()
    apps.get_model("companies", "Permission").objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0041_investor_permissions")]

    operations = [migrations.RunPython(add, remove)]
