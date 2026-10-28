"""Extension, change of the tariff and reopening of a rental (ТЗ § 13.6.2–13.6.5, § 14.5.8,
§ 23.4.3; customer's decisions П10–П12 of 28.10.2026)."""

from typing import Any

from django.db import migrations, models

# The rental manager extends (§ 23.2.2); changing the terms and reopening are granted apart.
RIGHTS = {
    "rental.extend": ("company_admin", "rental_manager"),
    "rental.change_terms": ("company_admin",),
    "rental.reopen": ("company_admin",),
}


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code, sets in RIGHTS.items():
        permission.objects.get_or_create(code=code, defaults={"section": "rental"})
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
        ("rentals", "0018_extra_service_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="rental",
            name="reopened_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="rentalmanualcharge",
            name="kind",
            field=models.CharField(
                choices=[
                    ("fuel", "Fuel"),
                    ("early_return", "Early Return"),
                    ("overdue", "Overdue"),
                    ("other", "Other"),
                ],
                max_length=20,
            ),
        ),
        migrations.RunPython(add, remove),
    ]
