"""§ 8.2.13: the system service work «Odometer replacement» in the base reference of works
(decision С-1: the reference grows by new migrations)."""

from typing import Any

from django.db import migrations

CODE = "odometer_replacement"


def add(apps: Any, schema_editor: Any) -> None:
    system_work = apps.get_model("service", "SystemWork")
    last = system_work.objects.order_by("-sort").values_list("sort", flat=True).first() or 0
    system_work.objects.update_or_create(
        code=CODE,
        defaults={
            "names": {
                "en": "Odometer replacement",
                "ru": "Замена одометра",
                "th": "เปลี่ยนเรือนไมล์",
            },
            "sort": last + 10,
        },
    )


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("service", "SystemWork").objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [("service", "0006_job_editing")]

    operations = [migrations.RunPython(add, remove)]
