"""Rights of reports (§ 23.8.5; customer's decision О-1 of 29.10.2026): viewing for the company
administrator, the rental and service managers, the storekeeper and the accountant; export for the
administrator and the accountant. Existing assignments keep their own copy of codes (§ 23.1.3)."""

from typing import Any

from django.db import migrations

CODES = ["reports.view", "reports.export"]
SETS = {
    "company_admin": CODES,
    "rental_manager": ["reports.view"],
    "service_manager": ["reports.view"],
    "storekeeper": ["reports.view"],
    "accountant": CODES,
}


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "reports"})
    for set_code, codes in SETS.items():
        target = permission_set.objects.get(code=set_code, is_system=True)
        for code in codes:
            item.objects.get_or_create(permission_set=target, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0020_order_reopen_permission"),
        ("tasks", "0003_reminders"),
    ]

    operations = [migrations.RunPython(add, remove)]
