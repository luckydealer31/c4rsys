from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("warehouse", "0020_places_are_tags")]

    operations = [
        migrations.AddField(
            model_name="item",
            name="country",
            field=models.CharField(blank=True, default="", max_length=2),
        ),
    ]
