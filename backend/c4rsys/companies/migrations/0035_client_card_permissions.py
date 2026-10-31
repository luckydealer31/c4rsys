"""Block 13: the rights of the client cards and counterparties — archive and restore (§ 6.6.4,
§ 23.8.2), merge (§ 6.2.3, § 6.1.13), split (§ 6.5.3, § 23.8.6: in «Company administrator»,
CONFIRMED), the blacklist mark set and lifted by two separate rights (§ 6.7.1, § 23.8.7). All
only in «Company administrator» (PROPOSAL for all but the split)."""

from typing import Any

from django.db import migrations

CODES = {
    "client.archive": "client",
    "client.restore": "client",
    "client.merge": "client",
    "client.split": "client",
    "client.blacklist.add": "client",
    "client.blacklist.remove": "client",
    "counterparty.merge": "counterparty",
    "counterparty.split": "counterparty",
}


def forward(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    item = apps.get_model("companies", "PermissionSetItem")
    admin = apps.get_model("companies", "PermissionSet").objects.get(
        code="company_admin", is_system=True
    )
    for code, section in CODES.items():
        permission.objects.get_or_create(code=code, defaults={"section": section})
        item.objects.get_or_create(permission_set=admin, permission_id=code)


def backward(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0034_vehicle_lifecycle_permissions")]

    operations = [migrations.RunPython(forward, backward)]
