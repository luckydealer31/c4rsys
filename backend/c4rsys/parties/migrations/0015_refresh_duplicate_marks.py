"""The mark «Possible duplicate» follows the current data (customer's remark of 25.11.2026): the
marks left on cards that match no other card any more are removed once. Only marked cards are
looked at; nothing is marked here."""

from typing import Any

from django.db import migrations


def unmark_stale(apps: Any, schema_editor: Any) -> None:
    # The current matching rules live in the services; they read the tables as they are now.
    from c4rsys.parties import counterparties, services
    from c4rsys.parties.models import ClientCard, Counterparty

    for model, duplicates in (
        (ClientCard, services.duplicates_of),
        (Counterparty, counterparties.duplicates_of),
    ):
        for card in model.objects.filter(possible_duplicate=True):
            if not duplicates(card):
                model.objects.filter(pk=card.pk).update(possible_duplicate=False)


class Migration(migrations.Migration):
    dependencies = [("parties", "0014_counterparty_birth_date")]

    operations = [migrations.RunPython(unmark_stale, migrations.RunPython.noop)]
