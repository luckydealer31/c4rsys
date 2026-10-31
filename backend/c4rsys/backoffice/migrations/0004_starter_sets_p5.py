"""Block 11 П5: the permissions of the merge of accounts, of the verification and of the recovery
through the support in the starter sets (PROPOSAL, c4rsys.backoffice.catalog)."""

from typing import Any

from django.db import migrations

ADDED = {
    "platform_admin": ["accounts.merge", "verification.resolve", "recovery.manage", "recovery.approve"],
    "support": ["accounts.merge", "recovery.manage"],
    "verification": ["verification.resolve", "recovery.manage", "recovery.approve"],
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
    dependencies = [("backoffice", "0003_merge_and_recovery")]

    operations = [migrations.RunPython(forward, backward)]
