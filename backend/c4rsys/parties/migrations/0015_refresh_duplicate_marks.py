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
        # The services read the tables as the code is now; a later migration may add a column
        # that is not there yet. A base without marked cards (a new one) has nothing to clean
        # (added 26.11.2026 with parties/0016, by the customer's leave: the migration itself had
        # been applied on the server already).
        stored = apps.get_model("parties", model.__name__)
        if not stored.objects.filter(possible_duplicate=True).exists():
            continue
        for card in model.objects.filter(possible_duplicate=True):
            if not duplicates(card):
                model.objects.filter(pk=card.pk).update(possible_duplicate=False)


class Migration(migrations.Migration):
    dependencies = [("parties", "0014_counterparty_birth_date")]

    operations = [migrations.RunPython(unmark_stale, migrations.RunPython.noop)]
