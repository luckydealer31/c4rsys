"""Block 15: the permission of the warehouse dictionaries in the starter set of the platform
administrator (PROPOSAL, c4rsys.backoffice.catalog)."""

from typing import Any

from django.db import migrations

ADDED = {"platform_admin": ["warehouse.dictionaries"]}


def forward(apps: Any, schema_editor: Any) -> None:
    role = apps.get_model("backoffice", "SystemRole")
    for code, permissions in ADDED.items():
        found = role.objects.filter(code=code, is_starter=True).first()
        if found is None:
            continue
        found.permissions = list(dict.fromkeys([*found.permissions, *permissions]))
        found.save(update_fields=["permissions"])


def backward(apps: Any, schema_editor: Any) -> None:
    role = apps.get_model("backoffice", "SystemRole")
    for code, permissions in ADDED.items():
        found = role.objects.filter(code=code, is_starter=True).first()
        if found is None:
            continue
        found.permissions = [p for p in found.permissions if p not in permissions]
        found.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("backoffice", "0005_starter_sets_block12")]

    operations = [migrations.RunPython(forward, backward)]
