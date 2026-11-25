"""The documents of a profile become a list, each of a chosen kind (customer's remark of
26.11.2026: a person chooses a passport, an ID card, a driving licence or another document; none
is required in the profile). The passport or ID and the driving licence entered before move into
the list; the separate fields go."""

from typing import Any

from django.db import migrations, models


def documents_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The list for the separate fields of one profile."""

    def iso(value: Any) -> str | None:
        return value.isoformat() if value else None

    found = []
    if row["document_number"]:
        found.append(
            {
                "type": row["document_type"] or "passport",
                "title": "",
                "number": row["document_number"],
                "issued_on": iso(row["document_issued_on"]),
                "expires_on": iso(row["document_expires_on"]),
                "issued_by": row["document_issued_by"],
                "categories": [],
            }
        )
    if row["licence_number"]:
        found.append(
            {
                "type": "driver_license",
                "title": "",
                "number": row["licence_number"],
                "issued_on": iso(row["licence_issued_on"]),
                "expires_on": iso(row["licence_expires_on"]),
                "issued_by": row["licence_issued_by"],
                "categories": list(row["licence_categories"] or []),
            }
        )
    return found


OLD = (
    "document_type",
    "document_number",
    "document_issued_on",
    "document_expires_on",
    "document_issued_by",
    "licence_number",
    "licence_issued_on",
    "licence_expires_on",
    "licence_issued_by",
    "licence_categories",
)


def to_list(apps: Any, schema_editor: Any) -> None:
    users = apps.get_model("accounts", "UserAccount")
    for row in users.objects.exclude(document_number="", licence_number="").values("pk", *OLD):
        users.objects.filter(pk=row["pk"]).update(documents=documents_of(row))


class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_document_issued_by")]

    operations = [
        migrations.AddField(
            model_name="useraccount",
            name="documents",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(to_list, migrations.RunPython.noop),
        *[migrations.RemoveField("useraccount", name) for name in OLD],
    ]
