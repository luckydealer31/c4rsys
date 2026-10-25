"""Decision of 25.10.2026: only the warehouse of a location stores; rental and service contexts
have no storage of their own, so the storage setting of contexts goes away."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("companies", "0017_import_permissions")]

    operations = [
        migrations.RemoveConstraint(
            model_name="activitycontext", name="activity_context_warehouse_stores"
        ),
        migrations.RemoveField(model_name="activitycontext", name="storage_enabled"),
    ]
