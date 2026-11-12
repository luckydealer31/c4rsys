from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("fleet", "0025_engine_volume_cc")]

    operations = [
        migrations.AddField(
            model_name="vehicleplate",
            name="country_other",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
    ]
