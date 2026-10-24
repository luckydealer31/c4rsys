"""Loads the passenger car catalog of the customer (§ 8.1.11, 24.10.2026)."""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from c4rsys.fleet.catalog.loader import load_passenger_cars


def load(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    load_passenger_cars(apps.get_model("fleet", "VehicleMake"), apps.get_model("fleet", "VehicleModel"))


class Migration(migrations.Migration):
    dependencies = [("fleet", "0005_vehicle_catalog_and_characteristics")]

    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
