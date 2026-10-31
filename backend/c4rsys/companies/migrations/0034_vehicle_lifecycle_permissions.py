"""Block 12, part Б: rights of the vehicle lifecycle (ТЗ § 23.5, § 8.2.10, § 8.4.2).

* § 23.5.5: setting, changing and lifting a manual restriction are three separate rights.
* § 23.5.6: the exceptional move of a restricted vehicle to a service is a separate right.
* § 23.5.2, § 8.2.10: the change of the odometer unit by an authorized employee.
* § 8.4.2: a write-off or a disposal needs special powers.
* PROPOSAL: the archive and restore of a card, the change of the relation and the end of the
  management, the settings of what is passed to the owner, the request to the new owner about a
  wrong transfer — each its own right.

All in «Company administrator» only (PROPOSAL: the ТЗ does not assign them to other sets)."""

from typing import Any

from django.db import migrations

CODES = (
    "vehicle.restriction.set",
    "vehicle.restriction.change",
    "vehicle.restriction.lift",
    "vehicle.move_restricted_to_service",
    "vehicle.odometer_unit",
    "vehicle.dispose",
    "vehicle.archive",
    "vehicle.relation",
    "vehicle.disclosure",
    "vehicle.ownership_reversal",
)


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
    dependencies = [("companies", "0033_vehicle_merge_split_permissions")]

    operations = [migrations.RunPython(forward, backward)]
