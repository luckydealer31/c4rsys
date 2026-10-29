"""Links inside the company and the rights of deliveries (§ 23.6.6; customer's decision Д-8 of
29.10.2026): all for «Company administrator»; viewing, creating, changing, assigning and
cancelling for «Rental manager» and «Service manager»; own trips for «Driver» (§ 23.2.5).
Existing assignments keep their own copy of codes (§ 23.1.3)."""

from typing import Any

from django.db import migrations

CODES = [
    "delivery.view",
    "delivery.create",
    "delivery.edit",
    "delivery.assign",
    "delivery.take",
    "delivery.start",
    "delivery.finish",
    "delivery.cancel",
    "delivery.correct",
    "delivery.drive",
]
MANAGER = [
    "delivery.view",
    "delivery.create",
    "delivery.edit",
    "delivery.assign",
    "delivery.cancel",
]
SETS = {
    "company_admin": CODES,
    "rental_manager": MANAGER,
    "service_manager": MANAGER,
    "driver": ["delivery.drive"],
}

LINKS = """
ALTER TABLE delivery
    ADD CONSTRAINT delivery_order_same_company_fk
    FOREIGN KEY (company_id, order_id) REFERENCES orders (company_id, id),
    ADD CONSTRAINT delivery_vehicle_same_company_fk
    FOREIGN KEY (company_id, vehicle_card_id) REFERENCES vehicle_card (company_id, id),
    ADD CONSTRAINT delivery_from_location_same_company_fk
    FOREIGN KEY (company_id, from_location_id) REFERENCES location (company_id, id),
    ADD CONSTRAINT delivery_to_location_same_company_fk
    FOREIGN KEY (company_id, to_location_id) REFERENCES location (company_id, id),
    ADD CONSTRAINT delivery_driver_same_company_fk
    FOREIGN KEY (company_id, driver_id) REFERENCES employment (company_id, id);
"""
UNLINK = """
ALTER TABLE delivery
    DROP CONSTRAINT delivery_driver_same_company_fk,
    DROP CONSTRAINT delivery_to_location_same_company_fk,
    DROP CONSTRAINT delivery_from_location_same_company_fk,
    DROP CONSTRAINT delivery_vehicle_same_company_fk,
    DROP CONSTRAINT delivery_order_same_company_fk;
"""


def add(apps: Any, schema_editor: Any) -> None:
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "delivery"})
    for set_code, codes in SETS.items():
        target = permission_set.objects.get(code=set_code, is_system=True)
        for code in codes:
            item.objects.get_or_create(permission_set=target, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("delivery", "0001_deliveries"),
        ("service", "0005_maintenance_rules"),
    ]

    operations = [
        migrations.RunSQL(LINKS, UNLINK),
        migrations.RunPython(add, remove),
    ]
