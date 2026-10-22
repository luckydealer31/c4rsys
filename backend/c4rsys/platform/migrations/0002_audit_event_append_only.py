"""The mandatory journal cannot be edited or deleted by the application (ТЗ § 24.4.5)."""

from django.db import migrations

FORWARD = """
CREATE FUNCTION audit_event_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER audit_event_append_only
BEFORE UPDATE OR DELETE ON audit_event
FOR EACH ROW EXECUTE FUNCTION audit_event_forbid_change();
"""

REVERSE = """
DROP TRIGGER IF EXISTS audit_event_append_only ON audit_event;
DROP FUNCTION IF EXISTS audit_event_forbid_change();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
