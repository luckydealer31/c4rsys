"""Block 14: the separate administrative and section rights of chapter 23.

* New codes: managing permission sets (§ 23.3.9), the locations (§ 23.5.3), the policies of the
  sections (§ 23.3.7), the maintenance rules and records (§ 23.5.1), link requests of clients and
  counterparties (§ 23.8.2), forming an order and its responsible (§ 23.6.5), relinking a record
  and correcting its client (§ 23.7.7).
* «Company administrator» gets all of them; the narrower codes of a split right join every
  system set that holds the broader one.
* Every active assignment that holds a broader right gets its narrower codes (a set assignment in
  its copy, a grant or denial as a sibling with the same scope), so the split neither gives nor
  takes away access (PROPOSAL). Each change is journaled by the system process.
"""

from typing import Any

from django.db import migrations
from django.utils import timezone

SPLITS = {
    "company.settings": (
        "location.create",
        "location.edit",
        "location.contexts.manage",
        "rental.policy.manage",
        "documents.policy.manage",
        "tasks.policy.manage",
        "notifications.policy.manage",
        "order.policy.manage",
        "service.policy.manage",
        "warehouse.policy.manage",
    ),
    "vehicle.edit": ("vehicle.maintenance.rules", "vehicle.maintenance.record"),
    "client.edit": ("client.link.request",),
    "counterparty.edit": ("counterparty.link.request",),
    "order.edit": ("order.form", "order.assign_responsible"),
    "finance.correct": ("finance.record.relink", "finance.record.client"),
}
NEW_ONLY_ADMIN = ("employee.sets.manage",)
SECTIONS = {
    "employee.sets.manage": "employee",
    "location.create": "location",
    "location.edit": "location",
    "location.contexts.manage": "location",
    "rental.policy.manage": "rental",
    "documents.policy.manage": "documents",
    "tasks.policy.manage": "tasks",
    "notifications.policy.manage": "notifications",
    "order.policy.manage": "order",
    "service.policy.manage": "service",
    "warehouse.policy.manage": "warehouse",
    "vehicle.maintenance.rules": "vehicle",
    "vehicle.maintenance.record": "vehicle",
    "client.link.request": "client",
    "counterparty.link.request": "counterparty",
    "order.form": "order",
    "order.assign_responsible": "order",
    "finance.record.relink": "finance",
    "finance.record.client": "finance",
}
PROCESS = "permission_split"


def _scope(a: Any) -> tuple[Any, ...]:
    return (
        a.all_locations,
        a.all_contexts,
        a.all_cash_accounts,
        frozenset(x.location_id for x in a.locations.through.objects.filter(assignment_id=a.id)),
        frozenset(x.context_id for x in a.contexts.through.objects.filter(assignment_id=a.id)),
        frozenset(x.cash_account_id for x in a.cash_accounts.all()),
    )


def forward(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    assignment_model = apps.get_model("companies", "PermissionAssignment")
    location_link = apps.get_model("companies", "AssignmentLocation")
    context_link = apps.get_model("companies", "AssignmentContext")
    cash_link = apps.get_model("companies", "AssignmentCashAccount")
    audit = apps.get_model("platform", "AuditEvent")

    for code, section in SECTIONS.items():
        permission.objects.get_or_create(code=code, defaults={"section": section})
    admin = permission_set.objects.get(code="company_admin", is_system=True)
    for code in SECTIONS:
        item.objects.get_or_create(permission_set=admin, permission_id=code)
    for broad, narrow in SPLITS.items():
        holders = permission_set.objects.filter(
            is_system=True, pk__in=item.objects.filter(permission_id=broad).values("permission_set")
        )
        for set_ in holders:
            for code in narrow:
                item.objects.get_or_create(permission_set=set_, permission_id=code)

    now = timezone.now()
    for a in assignment_model.objects.filter(revoked_at__isnull=True).order_by("granted_at"):
        added = sorted(
            {code for broad, narrow in SPLITS.items() if broad in a.permissions for code in narrow}
            - set(a.permissions)
        )
        if not added:
            continue
        if a.kind == "set":
            before = sorted(a.permissions)
            a.permissions = sorted({*a.permissions, *added})
            a.save(update_fields=["permissions"])
            audit.objects.create(
                company_id=a.company_id,
                system_process=PROCESS,
                action="employee.rights_split",
                object_type="employment",
                object_id=str(a.employment_id),
                before={"assignment": str(a.id), "permissions": before},
                after={"assignment": str(a.id), "added": added},
            )
            continue
        # A grant or a denial holds one code: the narrower ones become siblings with its scope,
        # unless such a sibling is already there (a repeated run).
        scope = _scope(a)
        held = {
            x.permissions[0]
            for x in assignment_model.objects.filter(
                employment_id=a.employment_id,
                kind=a.kind,
                owner_seat_id=a.owner_seat_id,
                revoked_at__isnull=True,
            ).exclude(pk=a.pk)
            if _scope(x) == scope
        }
        added = [code for code in added if code not in held]
        if not added:
            continue
        siblings = []
        for code in added:
            sibling = assignment_model.objects.create(
                company_id=a.company_id,
                employment_id=a.employment_id,
                kind=a.kind,
                permission_set=None,
                permissions=[code],
                all_locations=a.all_locations,
                all_contexts=a.all_contexts,
                all_cash_accounts=a.all_cash_accounts,
                owner_seat_id=a.owner_seat_id,
                granted_by_user_id=a.granted_by_user_id,
                granted_at=now,
            )
            for x in location_link.objects.filter(assignment_id=a.id):
                location_link.objects.create(
                    company_id=a.company_id, assignment=sibling, location_id=x.location_id
                )
            for x in context_link.objects.filter(assignment_id=a.id):
                context_link.objects.create(
                    company_id=a.company_id, assignment=sibling, context_id=x.context_id
                )
            for x in cash_link.objects.filter(assignment_id=a.id):
                cash_link.objects.create(
                    company_id=a.company_id, assignment=sibling, cash_account_id=x.cash_account_id
                )
            siblings.append(str(sibling.id))
        audit.objects.create(
            company_id=a.company_id,
            system_process=PROCESS,
            action="employee.rights_split",
            object_type="employment",
            object_id=str(a.employment_id),
            before={"assignment": str(a.id), "kind": a.kind, "permission": a.permissions[0]},
            after={"added": added, "assignments": siblings},
        )


def backward(apps: Any, schema_editor: Any) -> None:
    """The split cannot be undone without losing the history; only the catalog is left."""


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0036_permission_sets_and_rights_requests"),
        ("platform", "0001_initial"),
    ]

    operations = [migrations.RunPython(forward, backward)]
