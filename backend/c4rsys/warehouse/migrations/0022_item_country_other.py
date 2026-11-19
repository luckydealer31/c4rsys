from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("warehouse", "0021_item_country")]

    operations = [
        migrations.AddField(
            model_name="item",
            name="country_other",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
