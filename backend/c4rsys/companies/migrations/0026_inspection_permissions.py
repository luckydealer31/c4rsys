"""Rights of inspections (§ 23.4.1–23.4.2, § 23.3.7): cancelling before the start, terminating a
started one, recognising a saved one invalid, managing the inspection policies and templates.
Of the system sets only «Company administrator» gets them: § 23.2.2 and § 23.2.4 give the rental
manager and the mechanic creating and conducting inspections only, and § 23.2.4 expressly not the
recognition as invalid."""

from typing import Any

from django.db import migrations

CODES = [
    "inspection.cancel",
    "inspection.terminate",
    "inspection.invalidate",
    "inspection.policy.manage",
]


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    item = apps.get_model("companies", "PermissionSetItem")
    admin = apps.get_model("companies", "PermissionSet").objects.get(
        code="company_admin", is_system=True
    )
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "inspection"})
        item.objects.get_or_create(permission_set=admin, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0025_order_discount_permission")]

    operations = [migrations.RunPython(add, remove)]
