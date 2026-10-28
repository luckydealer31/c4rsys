"""Rental proposals of a preliminary order, annulment and transfer of a rental, cancellation of an
order (ТЗ § 12.1.7, § 12.2.1–12.2.2, § 12.3.7; customer's decisions З2–З6 of 28.10.2026)."""

from typing import Any

from django.db import migrations, models

RIGHTS = {
    "order.cancel": ("order", ("company_admin", "rental_manager")),
    "rental.annul": ("rental", ("company_admin",)),
    "rental.transfer": ("rental", ("company_admin",)),
}


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code, (section, sets) in RIGHTS.items():
        permission.objects.get_or_create(code=code, defaults={"section": section})
        for set_code in sets:
            target = permission_set.objects.get(code=set_code, is_system=True)
            item.objects.get_or_create(permission_set=target, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=RIGHTS
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=RIGHTS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0019_rental_changes"),
        ("orders", "0004_preliminary_and_cancel"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rental",
            name="state",
            field=models.CharField(
                choices=[
                    ("proposed", "Proposed"),
                    ("booked", "Booked"),
                    ("active", "Active"),
                    ("returned", "Returned"),
                    ("cancelled", "Cancelled"),
                    ("annulled", "Annulled"),
                ],
                max_length=10,
            ),
        ),
        migrations.RunPython(add, remove),
    ]
