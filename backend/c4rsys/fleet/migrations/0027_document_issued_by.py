from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("fleet", "0026_plate_country_other")]

    operations = [
        migrations.AddField(
            model_name="vehiclecarddocument",
            name="issued_by",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="vehicledocumentversion",
            name="issued_by",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
