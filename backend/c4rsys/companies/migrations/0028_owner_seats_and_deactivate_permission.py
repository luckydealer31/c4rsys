"""Block 10: the dedicated positions of the existing owners and the right of deactivation.

* Every current share of a user gets its dedicated position (§ 3.3.4). The creator's director
  position is his owner position (stage 1, T03), so the seat points to it and to his employment.
* `company.deactivate` (§ 3.6.1) is added to the catalog and to «Company administrator» only.
"""

from typing import Any

from django.db import migrations

CODE = "company.deactivate"


def forward(apps: Any, schema_editor: Any) -> None:
    share_model = apps.get_model("companies", "OwnershipShare")
    position_model = apps.get_model("companies", "Position")
    employment_model = apps.get_model("companies", "Employment")
    seat_model = apps.get_model("companies", "OwnerSeat")
    for share in share_model.objects.filter(valid_to__isnull=True, owner_user__isnull=False):
        employment = employment_model.objects.filter(
            company_id=share.company_id,
            user_id=share.owner_user_id,
            state__in=["active", "blocked"],
        ).first()
        position = position_model.objects.filter(
            company_id=share.company_id, is_owner_position=True, code="director"
        ).first()
        if employment is not None and employment.position_id is not None:
            if position is None or employment.position_id != position.pk:
                position = employment.position
        if position is None:
            position = position_model.objects.create(
                company_id=share.company_id, code="owner", is_owner_position=True
            )
        seat_model.objects.create(
            company_id=share.company_id,
            position=position,
            owner_user_id=share.owner_user_id,
            holder=employment,
            state="active",
            created_at=share.valid_from,
            activated_at=share.valid_from,
        )

    permission = apps.get_model("companies", "Permission")
    item = apps.get_model("companies", "PermissionSetItem")
    admin = apps.get_model("companies", "PermissionSet").objects.get(
        code="company_admin", is_system=True
    )
    permission.objects.get_or_create(code=CODE, defaults={"section": "company"})
    item.objects.get_or_create(permission_set=admin, permission_id=CODE)


def backward(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "OwnerSeat").objects.all().delete()
    apps.get_model("companies", "PermissionSetItem").objects.filter(permission_id=CODE).delete()
    apps.get_model("companies", "Permission").objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0027_ownership")]

    operations = [migrations.RunPython(forward, backward)]
