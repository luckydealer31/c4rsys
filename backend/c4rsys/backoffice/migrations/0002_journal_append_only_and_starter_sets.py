"""The system journal cannot be changed or deleted (ТЗ § 24.4.5); the starter sets of the system
staff (§ 24.1.5)."""

from typing import Any

from django.db import migrations

FORWARD = """
CREATE FUNCTION system_journal_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'system_journal_entry is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER system_journal_append_only
BEFORE UPDATE OR DELETE ON system_journal_entry
FOR EACH ROW EXECUTE FUNCTION system_journal_forbid_change();
"""

REVERSE = """
DROP TRIGGER IF EXISTS system_journal_append_only ON system_journal_entry;
DROP FUNCTION IF EXISTS system_journal_forbid_change();
"""

STARTER_SETS = {
    "platform_admin": [
        "staff.manage",
        "journal.view",
        "journal.export",
        "directory.search",
        "contacts.reveal",
        "tickets.manage",
        "privileged.open",
        "privileged.finance",
        "privileged.documents",
        "privileged.files",
        "block.manage",
    ],
    "support": ["directory.search", "contacts.reveal", "tickets.manage", "privileged.open"],
    "verification": ["directory.search", "contacts.reveal", "tickets.manage", "privileged.open"],
}


def forward(apps: Any, schema_editor: Any) -> None:
    role = apps.get_model("backoffice", "SystemRole")
    for code, permissions in STARTER_SETS.items():
        role.objects.update_or_create(
            code=code, defaults={"permissions": permissions, "is_starter": True}
        )


def backward(apps: Any, schema_editor: Any) -> None:
    apps.get_model("backoffice", "SystemRole").objects.filter(is_starter=True).delete()


class Migration(migrations.Migration):
    dependencies = [("backoffice", "0001_initial")]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
        migrations.RunPython(forward, backward),
    ]
