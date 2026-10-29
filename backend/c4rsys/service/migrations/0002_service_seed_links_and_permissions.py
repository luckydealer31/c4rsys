"""System reference of works (customer's decision С-1), links inside the company, permissions of
the service (§ 23.6.4) and the starter sets «Service manager» (§ 23.2.3) and «Mechanic»
(§ 23.2.4). Existing assignments keep their own copy of codes (§ 23.1.3)."""

from typing import Any

from django.db import migrations

from c4rsys.platform.ids import uuid7

# code, en, ru, th
WORKS = [
    ("scheduled_maintenance", "Scheduled maintenance", "Плановое ТО", "บำรุงรักษาตามระยะ"),
    ("diagnostics", "Diagnostics", "Диагностика", "ตรวจวินิจฉัย"),
    (
        "computer_diagnostics",
        "Computer diagnostics",
        "Компьютерная диагностика",
        "ตรวจเช็คด้วยคอมพิวเตอร์",
    ),
    ("engine_oil", "Engine oil change", "Замена моторного масла", "เปลี่ยนน้ำมันเครื่อง"),
    (
        "oil_filter",
        "Oil filter replacement",
        "Замена масляного фильтра",
        "เปลี่ยนไส้กรองน้ำมันเครื่อง",
    ),
    ("air_filter", "Air filter replacement", "Замена воздушного фильтра", "เปลี่ยนไส้กรองอากาศ"),
    ("cabin_filter", "Cabin filter replacement", "Замена салонного фильтра", "เปลี่ยนไส้กรองแอร์"),
    (
        "fuel_filter",
        "Fuel filter replacement",
        "Замена топливного фильтра",
        "เปลี่ยนไส้กรองน้ำมันเชื้อเพลิง",
    ),
    ("spark_plugs", "Spark plug replacement", "Замена свечей зажигания", "เปลี่ยนหัวเทียน"),
    (
        "transmission_oil",
        "Transmission oil change",
        "Замена масла в коробке передач",
        "เปลี่ยนน้ำมันเกียร์",
    ),
    (
        "brake_pads_front",
        "Front brake pads replacement",
        "Замена передних тормозных колодок",
        "เปลี่ยนผ้าเบรกหน้า",
    ),
    (
        "brake_pads_rear",
        "Rear brake pads replacement",
        "Замена задних тормозных колодок",
        "เปลี่ยนผ้าเบรกหลัง",
    ),
    ("brake_discs", "Brake disc replacement", "Замена тормозных дисков", "เปลี่ยนจานเบรก"),
    ("brake_fluid", "Brake fluid change", "Замена тормозной жидкости", "เปลี่ยนน้ำมันเบรก"),
    ("coolant", "Coolant change", "Замена охлаждающей жидкости", "เปลี่ยนน้ำยาหล่อเย็น"),
    ("timing_belt", "Timing belt replacement", "Замена ремня ГРМ", "เปลี่ยนสายพานไทม์มิ่ง"),
    (
        "drive_belt",
        "Drive belt replacement",
        "Замена приводного ремня",
        "เปลี่ยนสายพานหน้าเครื่อง",
    ),
    ("cvt_belt", "CVT belt replacement", "Замена ремня вариатора", "เปลี่ยนสายพานซีวีที"),
    ("chain_sprockets", "Chain and sprockets replacement", "Замена цепи и звёзд", "เปลี่ยนโซ่และสเตอร์"),
    (
        "chain_service",
        "Chain cleaning and lubrication",
        "Чистка и смазка цепи",
        "ล้างและหล่อลื่นโซ่",
    ),
    ("battery", "Battery replacement", "Замена аккумулятора", "เปลี่ยนแบตเตอรี่"),
    ("tyre_replacement", "Tyre replacement", "Замена шин", "เปลี่ยนยาง"),
    ("tyre_fitting", "Tyre fitting", "Шиномонтаж", "ถอดใส่ยาง"),
    ("puncture_repair", "Puncture repair", "Ремонт прокола", "ปะยาง"),
    ("wheel_balancing", "Wheel balancing", "Балансировка колёс", "ถ่วงล้อ"),
    ("wheel_alignment", "Wheel alignment", "Развал-схождение", "ตั้งศูนย์ล้อ"),
    ("suspension_check", "Suspension check", "Проверка подвески", "ตรวจช่วงล่าง"),
    ("shock_absorbers", "Shock absorber replacement", "Замена амортизаторов", "เปลี่ยนโช้คอัพ"),
    ("fork_oil", "Fork oil change", "Замена масла в вилке", "เปลี่ยนน้ำมันโช้คหน้า"),
    ("clutch", "Clutch replacement", "Замена сцепления", "เปลี่ยนคลัตช์"),
    ("clutch_adjustment", "Clutch adjustment", "Регулировка сцепления", "ปรับตั้งคลัตช์"),
    ("valve_adjustment", "Valve adjustment", "Регулировка клапанов", "ตั้งวาล์ว"),
    (
        "throttle_cleaning",
        "Throttle body cleaning",
        "Чистка дроссельной заслонки",
        "ล้างลิ้นปีกผีเสื้อ",
    ),
    ("ac_service", "Air conditioning service", "Обслуживание кондиционера", "บริการระบบแอร์"),
    ("ac_refill", "Air conditioning refill", "Заправка кондиционера", "เติมน้ำยาแอร์"),
    ("wipers", "Wiper blade replacement", "Замена щёток стеклоочистителя", "เปลี่ยนใบปัดน้ำฝน"),
    ("bulbs", "Bulb replacement", "Замена ламп", "เปลี่ยนหลอดไฟ"),
    ("electrical_repair", "Electrical repair", "Ремонт электрики", "ซ่อมระบบไฟฟ้า"),
    ("body_repair", "Body repair", "Кузовной ремонт", "ซ่อมตัวถัง"),
    ("painting", "Painting", "Покраска", "ทำสี"),
    ("pre_rental_check", "Pre-rental check", "Проверка перед арендой", "ตรวจสภาพก่อนให้เช่า"),
]

CODES = [
    "service.view",
    "service.create",
    "service.cancel",
    "service.accept",
    "service.place",
    "service.assign",
    "service.work",
    "service.work.others",
    "service.complete",
    "service.price",
    "service.catalog",
]
SETS = {
    "service_manager": [
        "vehicle.view",
        "client.find",
        "client.list",
        "client.create",
        "client.edit",
        "counterparty.view",
        "counterparty.create",
        "counterparty.edit",
        "order.view",
        "order.create",
        "order.edit",
        "finance.payment.receive",
        "service.view",
        "service.create",
        "service.cancel",
        "service.accept",
        "service.place",
        "service.assign",
        "service.work",
        "service.complete",
        "service.price",
        "service.catalog",
    ],
    "mechanic": [
        "vehicle.view",
        "inspection.create",
        "inspection.conduct",
        "service.view",
        "service.place",
        "service.work",
    ],
}

LINKS = """
ALTER TABLE service_operation
    ADD CONSTRAINT service_operation_order_same_company_fk
    FOREIGN KEY (company_id, order_id) REFERENCES orders (company_id, id),
    ADD CONSTRAINT service_operation_vehicle_same_company_fk
    FOREIGN KEY (company_id, vehicle_card_id) REFERENCES vehicle_card (company_id, id),
    ADD CONSTRAINT service_operation_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id),
    ADD CONSTRAINT service_operation_post_same_company_fk
    FOREIGN KEY (company_id, post_id) REFERENCES service_post (company_id, id);
ALTER TABLE service_job
    ADD CONSTRAINT service_job_operation_same_company_fk
    FOREIGN KEY (company_id, operation_id) REFERENCES service_operation (company_id, id),
    ADD CONSTRAINT service_job_order_same_company_fk
    FOREIGN KEY (company_id, order_id) REFERENCES orders (company_id, id),
    ADD CONSTRAINT service_job_work_same_company_fk
    FOREIGN KEY (company_id, work_id) REFERENCES service_work (company_id, id),
    ADD CONSTRAINT service_job_executor_same_company_fk
    FOREIGN KEY (company_id, executor_id) REFERENCES employment (company_id, id);
ALTER TABLE service_post
    ADD CONSTRAINT service_post_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
ALTER TABLE service_rate
    ADD CONSTRAINT service_rate_location_same_company_fk
    FOREIGN KEY (company_id, location_id) REFERENCES location (company_id, id);
"""

UNLINK = """
ALTER TABLE service_rate DROP CONSTRAINT service_rate_location_same_company_fk;
ALTER TABLE service_post DROP CONSTRAINT service_post_location_same_company_fk;
ALTER TABLE service_job
    DROP CONSTRAINT service_job_executor_same_company_fk,
    DROP CONSTRAINT service_job_work_same_company_fk,
    DROP CONSTRAINT service_job_order_same_company_fk,
    DROP CONSTRAINT service_job_operation_same_company_fk;
ALTER TABLE service_operation
    DROP CONSTRAINT service_operation_post_same_company_fk,
    DROP CONSTRAINT service_operation_location_same_company_fk,
    DROP CONSTRAINT service_operation_vehicle_same_company_fk,
    DROP CONSTRAINT service_operation_order_same_company_fk;
"""


def add(apps: Any, schema_editor: Any) -> None:
    system_work = apps.get_model("service", "SystemWork")
    for index, (code, en, ru, th) in enumerate(WORKS):
        system_work.objects.update_or_create(
            code=code, defaults={"names": {"en": en, "ru": ru, "th": th}, "sort": index * 10}
        )
    permission = apps.get_model("companies", "Permission")
    permission_set = apps.get_model("companies", "PermissionSet")
    item = apps.get_model("companies", "PermissionSetItem")
    for code in CODES:
        permission.objects.get_or_create(code=code, defaults={"section": "service"})
    admin = permission_set.objects.get(code="company_admin", is_system=True)
    for code in CODES:
        item.objects.get_or_create(permission_set=admin, permission_id=code)
    for set_code, codes in SETS.items():
        target, _ = permission_set.objects.get_or_create(
            code=set_code, is_system=True, defaults={"id": uuid7()}
        )
        for code in codes:
            item.objects.get_or_create(permission_set=target, permission_id=code)


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("companies", "PermissionSetItem").objects.filter(
        permission_id__in=CODES
    ).delete()
    apps.get_model("companies", "PermissionSet").objects.filter(
        code__in=SETS, is_system=True
    ).delete()
    apps.get_model("companies", "Permission").objects.filter(code__in=CODES).delete()
    apps.get_model("service", "SystemWork").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("service", "0001_service_operations"),
        ("companies", "0020_order_reopen_permission"),
        ("orders", "0004_preliminary_and_cancel"),
    ]

    operations = [
        migrations.RunSQL(LINKS, UNLINK),
        migrations.RunPython(add, remove),
    ]
