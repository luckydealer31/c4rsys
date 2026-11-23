from django.db import migrations

CAR = ["sedan", "hatchback", "wagon", "suv", "crossover", "coupe", "convertible", "minivan", "pickup", "other"]
TRUCK = ["van", "other"]


def forward(apps, schema_editor):
    """The bodies belong to the types and «moped» becomes a body of «motorcycle» (customer's
    decision of 23.11.2026). A former moped is taken for a scooter so that its picture stays; a
    body that does not belong to the type becomes «other», or empty where no body is asked."""
    Vehicle = apps.get_model("fleet", "Vehicle")
    Make = apps.get_model("fleet", "VehicleMake")
    Vehicle.objects.filter(vehicle_type="motorcycle").exclude(body_type="").update(body_type="")
    Vehicle.objects.filter(vehicle_type="moped").update(vehicle_type="motorcycle", body_type="scooter")
    Make.objects.filter(vehicle_type="moped").update(vehicle_type="motorcycle")
    Vehicle.objects.filter(vehicle_type="passenger_car").exclude(body_type="").exclude(
        body_type__in=CAR
    ).update(body_type="other")
    Vehicle.objects.filter(vehicle_type="truck").exclude(body_type="").exclude(
        body_type__in=TRUCK
    ).update(body_type="other")
    Vehicle.objects.exclude(vehicle_type__in=["passenger_car", "truck", "motorcycle"]).exclude(
        body_type=""
    ).update(body_type="")


class Migration(migrations.Migration):
    dependencies = [("fleet", "0030_owner_address")]

    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
