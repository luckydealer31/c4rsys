"""The profile keeps the identity document and the driving licence apart (customer's decision of
25.11.2026). The one document entered before goes to its own place by its type: a driving
licence to the licence, a passport or an ID card stays with its type as a code; a type that is
neither is left to be chosen again."""

from typing import Any

from django.db import migrations, models


def _kind(text: str) -> str:
    value = text.strip().lower().replace("_", " ").replace("-", " ")
    if any(word in value for word in ("водительск", "driver", "driving", "ใบขับขี่")):
        return "licence"
    if any(word in value for word in ("паспорт", "passport", "หนังสือเดินทาง")):
        return "passport"
    if value in ("id card", "national id", "id", "id карта"):
        return "national_id"
    return ""


def split_documents(apps: Any, schema_editor: Any) -> None:
    users = apps.get_model("accounts", "UserAccount")
    for user in users.objects.exclude(document_type="", document_number=""):
        kind = _kind(user.document_type)
        if kind == "licence":
            user.licence_number = user.document_number
            user.licence_issued_on = user.document_issued_on
            user.licence_expires_on = user.document_expires_on
            user.document_type = user.document_number = ""
            user.document_issued_on = user.document_expires_on = None
        else:
            user.document_type = kind
        user.save(
            update_fields=[
                "document_type",
                "document_number",
                "document_issued_on",
                "document_expires_on",
                "licence_number",
                "licence_issued_on",
                "licence_expires_on",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0007_document_categories")]

    operations = [
        migrations.RenameField("useraccount", "document_categories", "licence_categories"),
        migrations.AddField(
            model_name="useraccount",
            name="licence_number",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="useraccount",
            name="licence_issued_on",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="useraccount",
            name="licence_expires_on",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(split_documents, migrations.RunPython.noop),
    ]
