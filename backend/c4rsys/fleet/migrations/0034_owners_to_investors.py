"""The owners of managed vehicles pass from the counterparties to the investors (customer's
decision of 26.11.2026, in place of the counterparty of 25.11.2026).

A counterparty named as the owner of a vehicle becomes an investor. If the same card is also
used as a counterparty elsewhere (a supplier, a contractor, a participant of a document, …), it
stays a counterparty and an investor card with the same data is made beside it: the two lists
are kept apart (agent's choice, not confirmed).
"""

from typing import Any

from django.db import migrations

# The references that go with the card itself or are the ownership.
OWN = {
    ("counterparty_contact", "counterparty_id"),
    ("counterparty", "merged_into_id"),
    ("profile_link", "counterparty_id"),
    ("card_merge", "primary_counterparty_id"),
    ("card_merge", "duplicate_counterparty_id"),
    ("vehicle_owner_claim", "counterparty_id"),
}
COPIED = (
    "company_id",
    "kind",
    "first_name",
    "last_name",
    "middle_name",
    "company_name",
    "birth_date",
    "comment",
    "registration",
    "address",
    "external_id",
    "created_by_user_id",
    # The extended data of the card (§ 6.2.12–6.2.13).
    "id_document",
    "driver_license",
    "driving_since",
    "other_documents",
    "registration_address",
    "residence_address",
    "country",
    "legal_form",
    "registration_number",
    "tax_number",
    "legal_address",
    "actual_address",
    "director_name",
    "contact_person",
    "bank_details",
    "signatory_name",
    "signatory_basis",
)
REFERENCES = """
    SELECT c.conrelid::regclass::text, a.attname
    FROM pg_constraint c
    JOIN unnest(c.conkey, c.confkey) AS k(own, foreign_) ON true
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.own
    JOIN pg_attribute f ON f.attrelid = c.confrelid AND f.attnum = k.foreign_
    WHERE c.contype = 'f' AND c.confrelid = 'counterparty'::regclass AND f.attname = 'id'
"""


def used_elsewhere(cursor: Any, references: set[tuple[str, str]], card_id: Any) -> bool:
    for table, column in sorted(references):
        cursor.execute(f'SELECT 1 FROM "{table}" WHERE "{column}" = %s LIMIT 1', [card_id])
        if cursor.fetchone():
            return True
    return False


def forward(apps: Any, schema_editor: Any) -> None:
    claims = apps.get_model("fleet", "VehicleOwnerClaim")
    cards = apps.get_model("parties", "Counterparty")
    contacts = apps.get_model("parties", "CounterpartyContact")
    owner_ids = set(
        claims.objects.filter(counterparty__isnull=False).values_list("counterparty_id", flat=True)
    )
    if not owner_ids:
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(REFERENCES)
        references = {(t.strip('"'), c) for t, c in cursor.fetchall()} - OWN
        for card in cards.objects.filter(pk__in=owner_ids, role="counterparty").order_by("id"):
            if not used_elsewhere(cursor, references, card.id):
                cards.objects.filter(pk=card.pk).update(role="investor")
                continue
            copy = cards.objects.create(
                role="investor",
                provisional=card.provisional,
                **{name: getattr(card, name) for name in COPIED},
            )
            contacts.objects.bulk_create(
                contacts(
                    company_id=c.company_id,
                    counterparty=copy,
                    kind=c.kind,
                    value=c.value,
                    value_normalized=c.value_normalized,
                    messenger=c.messenger,
                )
                for c in contacts.objects.filter(counterparty=card)
            )
            claims.objects.filter(counterparty=card).update(counterparty=copy)


class Migration(migrations.Migration):
    dependencies = [
        ("fleet", "0033_owner_counterparty"),
        ("parties", "0016_investors_list"),
    ]

    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
