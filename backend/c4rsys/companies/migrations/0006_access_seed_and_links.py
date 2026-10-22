"""Permission catalog and system sets of stage 1 (§ 23.1.14); links within one company (A05)."""

from typing import Any

from django.db import migrations

from c4rsys.companies.permissions import PERMISSIONS, SYSTEM_SETS
from c4rsys.platform.ids import uuid7

LINKS = """
ALTER TABLE permission_assignment
    ADD CONSTRAINT permission_assignment_employment_same_company_fk
    FOREIGN KEY (company_id, employment_id) REFERENCES employment (company_id, id);
ALTER TABLE permission_assignment_location
    ADD CONSTRAINT permission_assignment_location_assignment_same_company_fk
    FOREIGN KEY (company_id, assignment_id) REFERENCES permission_assignment (company_id, id),
    ADD CONSTRAINT permission_assignment_location_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
ALTER TABLE permission_assignment_context
    ADD CONSTRAINT permission_assignment_context_assignment_same_company_fk
    FOREIGN KEY (company_id, assignment_id) REFERENCES permission_assignment (company_id, id),
    ADD CONSTRAINT permission_assignment_context_context_same_company_fk
    FOREIGN KEY (company_id, context_id) REFERENCES activity_context (company_id, id);
ALTER TABLE invitation
    ADD CONSTRAINT invitation_employment_same_company_fk
    FOREIGN KEY (company_id, employment_id) REFERENCES employment (company_id, id);
"""

UNLINKS = """
ALTER TABLE invitation DROP CONSTRAINT IF EXISTS invitation_employment_same_company_fk;
ALTER TABLE permission_assignment_context
    DROP CONSTRAINT IF EXISTS permission_assignment_context_context_same_company_fk,
    DROP CONSTRAINT IF EXISTS permission_assignment_context_assignment_same_company_fk;
ALTER TABLE permission_assignment_location
    DROP CONSTRAINT IF EXISTS permission_assignment_location_location_same_company_fk,
    DROP CONSTRAINT IF EXISTS permission_assignment_location_assignment_same_company_fk;
ALTER TABLE permission_assignment
    DROP CONSTRAINT IF EXISTS permission_assignment_employment_same_company_fk;
"""


def seed(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    permission.objects.bulk_create(
        [permission(code=code, section=section) for code, section in PERMISSIONS.items()]
    )
    for code, codes in SYSTEM_SETS.items():
        created = permission_set.objects.create(id=uuid7(), code=code, is_system=True)
        item.objects.bulk_create(
            [item(permission_set=created, permission_id=c) for c in codes]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0005_access"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
        migrations.RunSQL(sql=LINKS, reverse_sql=UNLINKS),
    ]
