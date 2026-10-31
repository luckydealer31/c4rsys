"""Block 16: the permission to connect the modules of a company (§ 4.1.9) in the starter set of the platform
administrator (PROPOSAL, c4rsys.backoffice.catalog)."""

from typing import Any

from django.db import migrations

ADDED = {"platform_admin": ["companies.modules"]}


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
    dependencies = [("backoffice", "0006_starter_sets_block15")]

    operations = [migrations.RunPython(forward, backward)]
