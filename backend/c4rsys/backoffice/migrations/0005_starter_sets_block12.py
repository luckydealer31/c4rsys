"""Block 12: the permissions of the confirmed characteristics and of the catalog of makes in the
starter sets (PROPOSAL, c4rsys.backoffice.catalog)."""

from typing import Any

from django.db import migrations

ADDED = {
    "platform_admin": ["vehicles.characteristics", "vehicles.catalog"],
    "support": ["vehicles.catalog"],
    "verification": ["vehicles.characteristics"],
}


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
    dependencies = [("backoffice", "0004_starter_sets_p5")]

    operations = [migrations.RunPython(forward, backward)]
