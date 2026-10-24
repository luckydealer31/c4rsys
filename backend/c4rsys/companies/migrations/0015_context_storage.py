"""Storage of contexts (§ 11.1.5): always on for the warehouse context, a setting for rental and
service (off by default)."""

from typing import Any

from django.db import migrations, models


def warehouse_stores(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "ActivityContext").objects.filter(type="warehouse").update(
        storage_enabled=True
    )


class Migration(migrations.Migration):
    dependencies = [("companies", "0014_vehicle_transfer_to_warehouse_permission")]

    operations = [
        migrations.AddField(
            model_name="activitycontext",
            name="storage_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(warehouse_stores, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="activitycontext",
            constraint=models.CheckConstraint(
                condition=models.Q(("type", "warehouse"), _negated=True)
                | models.Q(("storage_enabled", True)),
                name="activity_context_warehouse_stores",
            ),
        ),
    ]
