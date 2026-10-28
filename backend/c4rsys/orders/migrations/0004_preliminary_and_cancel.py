"""Preliminary orders and the cancellation of an order (ТЗ § 12.1.7, § 12.2.1–12.2.2;
customer's decisions З2–З4 of 28.10.2026)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0003_bookings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="state",
            field=models.CharField(
                choices=[
                    ("preliminary", "Preliminary"),
                    ("open", "Open"),
                    ("closed", "Closed"),
                    ("cancelled", "Cancelled"),
                    ("terminated", "Terminated"),
                ],
                default="open",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="cancel_reason",
            field=models.CharField(blank=True, default="", max_length=2000),
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.CheckConstraint(
                condition=~models.Q(state="cancelled") | models.Q(cancelled_at__isnull=False),
                name="orders_cancelled_has_time",
            ),
        ),
    ]
