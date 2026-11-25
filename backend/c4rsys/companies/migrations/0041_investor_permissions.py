"""The rights of the list of investors (customer's decision of 26.11.2026): the list is open not
to everybody. All in «Company administrator»; «Accountant» views the list (PROPOSAL for the
accountant's part). Existing assignments keep their own copy of codes (§ 23.1.3)."""

from typing import Any

from django.db import migrations

CODES = (
    "investor.view",
    "investor.create",
    "investor.edit",
    "investor.link.request",
    "investor.archive",
    "investor.restore",
    "investor.merge",
    "investor.split",
)
IN_SETS = {"company_admin": CODES, "accountant": ("investor.view",)}


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    item = apps.get_model("companies", "PermissionSetItem")
    sets = apps.get_model("companies", "PermissionSet")
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "investor"})
    for set_code, codes in IN_SETS.items():
        target = sets.objects.filter(code=set_code, is_system=True).first()
        if target is None:
            continue
        for code in codes:
            item.objects.get_or_create(permission_set=target, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0040_company_phones")]

    operations = [migrations.RunPython(add, remove)]
