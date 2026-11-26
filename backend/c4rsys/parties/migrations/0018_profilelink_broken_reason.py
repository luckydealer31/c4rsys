from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("parties", "0017_profile_gives_fresh_data")]

    operations = [
        migrations.AddField(
            model_name="profilelink",
            name="broken_reason",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
