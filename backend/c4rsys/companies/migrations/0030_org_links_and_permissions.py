"""Block 10, part B: links of the organisation structure to the same company, the new rights and
the starting subordination of the existing employees.

* Departments, members and responsibles refer to departments and cards of the same company
  (composite foreign keys).
* § 23.3.9, § 23.3.1: editing the employee card, managing the positions handbook, managing the
  organisation structure and terminating access are separate rights; of the system sets only
  «Company administrator» gets them (PROPOSAL).
* § 5.2.1: every current employee who is not the top authority becomes subordinate to the current
  top manager, as a new employee would.
"""

from typing import Any

from django.db import migrations

FORWARD = """
ALTER TABLE department
    ADD CONSTRAINT department_parent_department_same_company_fk
    FOREIGN KEY (company_id, parent_department_id) REFERENCES department (company_id, id);
ALTER TABLE department
    ADD CONSTRAINT department_parent_employment_same_company_fk
    FOREIGN KEY (company_id, parent_employment_id) REFERENCES employment (company_id, id);
ALTER TABLE department_member
    ADD CONSTRAINT department_member_department_same_company_fk
    FOREIGN KEY (company_id, department_id) REFERENCES department (company_id, id);
ALTER TABLE department_member
    ADD CONSTRAINT department_member_employment_same_company_fk
    FOREIGN KEY (company_id, employment_id) REFERENCES employment (company_id, id);
ALTER TABLE responsibility
    ADD CONSTRAINT responsibility_subordinate_same_company_fk
    FOREIGN KEY (company_id, subordinate_id) REFERENCES employment (company_id, id);
ALTER TABLE responsibility
    ADD CONSTRAINT responsibility_employment_same_company_fk
    FOREIGN KEY (company_id, responsible_employment_id) REFERENCES employment (company_id, id);
ALTER TABLE responsibility
    ADD CONSTRAINT responsibility_department_same_company_fk
    FOREIGN KEY (company_id, responsible_department_id) REFERENCES department (company_id, id);
"""

REVERSE = """
ALTER TABLE responsibility DROP CONSTRAINT IF EXISTS responsibility_department_same_company_fk;
ALTER TABLE responsibility DROP CONSTRAINT IF EXISTS responsibility_employment_same_company_fk;
ALTER TABLE responsibility DROP CONSTRAINT IF EXISTS responsibility_subordinate_same_company_fk;
ALTER TABLE department_member
    DROP CONSTRAINT IF EXISTS department_member_employment_same_company_fk;
ALTER TABLE department_member
    DROP CONSTRAINT IF EXISTS department_member_department_same_company_fk;
ALTER TABLE department DROP CONSTRAINT IF EXISTS department_parent_employment_same_company_fk;
ALTER TABLE department DROP CONSTRAINT IF EXISTS department_parent_department_same_company_fk;
"""

CODES = {
    "employee.card.edit": "employee",
    "employee.positions.manage": "employee",
    "employee.access.terminate": "employee",
    "org.manage": "org",
}


def forward(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    item = apps.get_model("companies", "PermissionSetItem")
    admin = apps.get_model("companies", "PermissionSet").objects.get(
        code="company_admin", is_system=True
    )
    for code, section in CODES.items():
        permission.objects.get_or_create(code=code, defaults={"section": section})
        item.objects.get_or_create(permission_set=admin, permission_id=code)

    employment_model = apps.get_model("companies", "Employment")
    responsibility = apps.get_model("companies", "Responsibility")
    tops = {
        e.company_id: e.pk
        for e in employment_model.objects.filter(
            state="active", position__is_top_authority=True
        ).order_by("-created_at")
    }
    for e in employment_model.objects.exclude(state="terminated"):
        top = tops.get(e.company_id)
        if top is None or top == e.pk:
            continue
        responsibility.objects.create(
            company_id=e.company_id, subordinate_id=e.pk, responsible_employment_id=top
        )


def backward(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "Responsibility").objects.all().delete()
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=list(CODES)
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=list(CODES)).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0029_org_structure")]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
        migrations.RunPython(forward, backward),
    ]
