"""The holder of a profile gives the fresh data himself; a company does not ask for them
(customer's decision of 26.11.2026, § 6.4.4). The occasion of a request is not required any more;
the requests that still waited for an answer are cancelled.
"""

from typing import Any

from django.db import migrations, models
from django.utils import timezone


def cancel_waiting(apps: Any, schema_editor: Any) -> None:
    requests = apps.get_model("parties", "ProfileDataRequest")
    requests.objects.filter(state="pending").update(state="cancelled", decided_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [("parties", "0016_investors_list")]

    operations = [
        migrations.RemoveConstraint(
            model_name="profiledatarequest", name="profile_data_request_occasion"
        ),
        migrations.AlterField(
            model_name="profiledatarequest",
            name="occasion",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.RunPython(cancel_waiting, migrations.RunPython.noop),
    ]
