"""A work of a service operation is a source of charges (ТЗ § 16.1.3; decision С-4)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("finance", "0019_extra_services")]

    operations = [
        migrations.AlterField(
            model_name="chargeentry",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("rental_base", "Rental Base"),
                    ("rental_mileage", "Rental Mileage"),
                    ("rental_manual", "Rental Manual"),
                    ("rental_service", "Rental Service"),
                    ("service_job", "Service Job"),
                ],
                max_length=30,
            ),
        ),
    ]
