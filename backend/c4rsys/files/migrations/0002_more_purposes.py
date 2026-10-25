from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("files", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="storedfile",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("avatar", "Avatar"),
                    ("company_logo", "Company Logo"),
                    ("item_photo", "Item Photo"),
                    ("import_file", "Import File"),
                ],
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="storedfile",
            name="content_type",
            field=models.CharField(max_length=100),
        ),
    ]
