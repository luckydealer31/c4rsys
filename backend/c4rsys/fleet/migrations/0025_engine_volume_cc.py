from decimal import Decimal, InvalidOperation

from django.db import migrations, models

OLD, NEW = "engine_volume_l", "engine_volume_cc"


def _cc(value):
    """Litres kept as a string («1.5») become cubic centimetres (1500)."""
    if value in (None, ""):
        return None
    try:
        return int(Decimal(str(value)) * 1000)
    except (InvalidOperation, ValueError):
        return None


def forward(apps, schema_editor):
    """The engine volume is kept in cubic centimetres (the customer's decision of 12.11.2026):
    the vehicles, the working values of the cards, the proposals and the history of the
    characteristic."""
    vehicle = apps.get_model("fleet", "Vehicle")
    for v in vehicle.objects.exclude(engine_volume_l__isnull=True):
        v.engine_volume_cc = int(v.engine_volume_l * 1000)
        v.save(update_fields=["engine_volume_cc"])
    for v in vehicle.objects.filter(confirmed_fields__contains=[OLD]):
        v.confirmed_fields = [NEW if name == OLD else name for name in v.confirmed_fields]
        v.save(update_fields=["confirmed_fields"])
    working = apps.get_model("fleet", "VehicleCardWorkingValue")
    for w in working.objects.filter(field=OLD):
        w.field, w.value = NEW, _cc(w.value)
        w.save(update_fields=["field", "value"])
    proposal = apps.get_model("fleet", "VehicleCharacteristicProposal")
    for p in proposal.objects.filter(field=OLD):
        p.field, p.current_value, p.proposed_value = NEW, _cc(p.current_value), _cc(p.proposed_value)
        p.save(update_fields=["field", "current_value", "proposed_value"])
    event = apps.get_model("fleet", "VehicleCharacteristicEvent")
    for e in event.objects.filter(field=OLD):
        e.field, e.before, e.after = NEW, _cc(e.before), _cc(e.after)
        e.save(update_fields=["field", "before", "after"])


class Migration(migrations.Migration):
    dependencies = [("fleet", "0024_documents_one_number")]

    operations = [
        migrations.AddField(
            model_name="vehicle",
            name="engine_volume_cc",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(forward, migrations.RunPython.noop),
        migrations.RemoveField(model_name="vehicle", name="engine_volume_l"),
    ]
