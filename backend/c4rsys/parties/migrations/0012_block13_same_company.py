"""Block 13: the cards a merge, a restriction mark, a merged card or a data request refers to
belong to the same company (A05)."""

from django.db import migrations

PAIRS = [
    ("client_card", "client_card_merged_same_company_fk", "merged_into_id", "client_card"),
    ("counterparty", "counterparty_merged_same_company_fk", "merged_into_id", "counterparty"),
    ("client_restriction", "client_restriction_client_same_company_fk", "client_id", "client_card"),
    ("card_merge", "card_merge_primary_client_same_company_fk", "primary_client_id", "client_card"),
    (
        "card_merge",
        "card_merge_duplicate_client_same_company_fk",
        "duplicate_client_id",
        "client_card",
    ),
    (
        "card_merge",
        "card_merge_primary_counterparty_same_company_fk",
        "primary_counterparty_id",
        "counterparty",
    ),
    (
        "card_merge",
        "card_merge_duplicate_counterparty_same_company_fk",
        "duplicate_counterparty_id",
        "counterparty",
    ),
    ("profile_data_request", "profile_data_request_link_same_company_fk", "link_id", "profile_link"),
]

FORWARD = [
    f"ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY (company_id, {column}) "
    f"REFERENCES {target} (company_id, id);"
    for table, name, column, target in PAIRS
]
REVERSE = [f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {n};" for t, n, _c, _x in PAIRS]


class Migration(migrations.Migration):
    dependencies = [("parties", "0011_block13_cards")]

    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
