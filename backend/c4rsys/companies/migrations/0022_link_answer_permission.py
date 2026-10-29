"""The right to answer requests of other companies to link with this company's profile (§ 6.3.8);
only in «Company administrator» (PROPOSAL), the top-authority director answers without it."""

from typing import Any

from django.db import migrations

CODES = ["company.link.answer"]


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    item = apps.get_model("companies", "PermissionSetItem")
    admin = apps.get_model("companies", "PermissionSet").objects.get(
        code="company_admin", is_system=True
    )
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "company"})
        item.objects.get_or_create(permission_set=admin, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0021_setup_choices")]

    operations = [migrations.RunPython(add, remove)]
