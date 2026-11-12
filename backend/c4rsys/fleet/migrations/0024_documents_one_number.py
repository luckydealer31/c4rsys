from django.db import migrations, models

LABELS = {
    "passport": "Vehicle passport",
    "registration": "Registration certificate",
    "insurance": "Insurance policy",
    "other": "Document",
}


def forward(apps, schema_editor):
    """The series joins the number in one field; the owner's documents get a name (the customer's
    decisions of 12.11.2026)."""
    card_document = apps.get_model("fleet", "VehicleCardDocument")
    for d in card_document.objects.exclude(series=""):
        d.number = f"{d.series} {d.number}".strip()
        d.save(update_fields=["number"])
    document = apps.get_model("fleet", "VehicleDocument")
    for d in document.objects.filter(title=""):
        d.title = LABELS.get(d.doc_type, "Document")
        d.save(update_fields=["title"])


class Migration(migrations.Migration):
    dependencies = [("fleet", "0023_card_documents")]

    operations = [
        migrations.AlterField(
            model_name="vehiclecarddocument",
            name="number",
            field=models.CharField(blank=True, default="", max_length=150),
        ),
        migrations.AddField(
            model_name="vehicledocument",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(forward, migrations.RunPython.noop),
        migrations.RemoveField(model_name="vehiclecarddocument", name="series"),
    ]
