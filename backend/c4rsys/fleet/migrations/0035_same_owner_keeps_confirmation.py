"""The management confirmed by the owner is restored where it was ended only because the same
owner was declared anew (customer's remark of 26.11.2026): his data were saved again in the card
of the vehicle, or a card of the list took the place of his ID. A confirmation ended by another
owner, by the end of the management or followed by a newer one is left as it is.
"""

from typing import Any

from django.db import migrations


def restore(apps: Any, schema_editor: Any) -> None:
    confirmations = apps.get_model("fleet", "VehicleManagementConfirmation")
    claims = apps.get_model("fleet", "VehicleOwnerClaim")
    requests = apps.get_model("fleet", "VehicleOwnerRequest")
    cards = apps.get_model("fleet", "VehicleCard")

    def names(claim: Any, found: Any) -> bool:
        card = claim.counterparty
        user_id = card.linked_user_id if card is not None else claim.owner_user_id
        company_id = card.linked_company_id if card is not None else None
        if found.owner_company_id is not None:
            return bool(company_id == found.owner_company_id)
        return found.owner_user_id is not None and user_id == found.owner_user_id

    for found in confirmations.objects.filter(end_reason="owner_changed", ended_at__isnull=False):
        of_card = confirmations.objects.filter(vehicle_card_id=found.vehicle_card_id)
        if of_card.filter(started_at__gt=found.started_at).exists():
            continue
        if of_card.filter(ended_at__isnull=True).exists():
            continue
        card = cards.objects.filter(pk=found.vehicle_card_id).first()
        if card is None or card.relation != "managed" or card.state != "active":
            continue
        latest = (
            requests.objects.filter(vehicle_card_id=found.vehicle_card_id)
            .order_by("-created_at")
            .first()
        )
        if latest is None or latest.id != found.request_id:
            continue
        later = claims.objects.select_related("counterparty").filter(
            vehicle_card_id=found.vehicle_card_id, created_at__gt=found.started_at
        )
        if not later.exists() or not all(names(claim, found) for claim in later):
            continue
        confirmations.objects.filter(pk=found.pk).update(ended_at=None, end_reason="")


class Migration(migrations.Migration):
    dependencies = [
        ("fleet", "0034_owners_to_investors"),
        ("parties", "0016_investors_list"),
    ]

    operations = [migrations.RunPython(restore, migrations.RunPython.noop)]
