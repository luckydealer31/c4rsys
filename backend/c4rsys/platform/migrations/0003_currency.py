"""System reference of currencies (§ 17.1.1, § 17.1.7, § 22.1.7)."""

from typing import Any

from django.db import migrations, models

from c4rsys.platform.currencies import ISO_4217


def seed(apps: Any, schema_editor: Any) -> None:
    currency = apps.get_model("platform", "Currency")
    currency.objects.bulk_create(
        [currency(code=code, minor_units=units) for code, units in ISO_4217.items()]
    )


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0002_audit_event_append_only"),
    ]

    operations = [
        migrations.CreateModel(
            name="Currency",
            fields=[
                ("code", models.CharField(max_length=3, primary_key=True, serialize=False)),
                ("minor_units", models.PositiveSmallIntegerField()),
            ],
            options={"db_table": "currency"},
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
