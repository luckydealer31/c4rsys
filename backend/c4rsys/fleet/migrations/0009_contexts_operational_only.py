"""Service is implied for every vehicle and the warehouse is not a usage context (customer's
decision of 24.10.2026): their switches are removed; only operational contexts remain."""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def drop(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model("fleet", "VehicleCardContext").objects.filter(
        context_type__in=["service", "warehouse"]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("fleet", "0008_owner_claim_client")]

    operations = [migrations.RunPython(drop, migrations.RunPython.noop)]
