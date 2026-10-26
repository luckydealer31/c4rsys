"""Issue policies and exceptions (ТЗ § 14.3, § 23.4.7): the policy names a location and the
exception a rental of the same company; the rights to permit an issue with unmet requirements and
to confirm or reject a recorded exception go to «Company administrator» only (customer's decision
В8 of 26.10.2026)."""

from typing import Any

from django.db import migrations

FORWARD = """
ALTER TABLE rental_issue_policy
    ADD CONSTRAINT rental_issue_policy_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
ALTER TABLE rental_issue_exception
    ADD CONSTRAINT rental_issue_exception_rental_same_company_fk
    FOREIGN KEY (company_id, rental_id) REFERENCES rental (company_id, id);
"""

REVERSE = """
ALTER TABLE rental_issue_exception
    DROP CONSTRAINT IF EXISTS rental_issue_exception_rental_same_company_fk;
ALTER TABLE rental_issue_policy
    DROP CONSTRAINT IF EXISTS rental_issue_policy_location_same_company_fk;
"""

CODES = ("rental.exception.authorize", "rental.exception.decide")


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    item = apps.get_model("companies", "PermissionSetItem")
    admin = apps.get_model("companies", "PermissionSet").objects.get(
        code="company_admin", is_system=True
    )
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "rental"})
        item.objects.get_or_create(permission_set=admin, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rentals", "0013_issue_policies"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
        migrations.RunPython(add, remove),
    ]
