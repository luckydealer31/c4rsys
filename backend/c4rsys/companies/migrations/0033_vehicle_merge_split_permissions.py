"""§ 8.6.5–8.6.6: the merge of duplicate vehicle cards needs its own right and the split of a
merge an elevated one. The split is in «Company administrator» (CONFIRMED 18.10.2026); the merge
in «Company administrator» only (PROPOSAL)."""

from typing import Any

from django.db import migrations

CODES = ("vehicle.merge", "vehicle.split")


def forward(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    item = apps.get_model("companies", "PermissionSetItem")
    admin = apps.get_model("companies", "PermissionSet").objects.get(
        code="company_admin", is_system=True
    )
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "vehicle"})
        item.objects.get_or_create(permission_set=admin, permission_id=code)


def backward(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0032_journal_export_permission")]

    operations = [migrations.RunPython(forward, backward)]
