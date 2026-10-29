"""Starting templates of the system (ТЗ § 18.1.5–18.1.6, § 16.7.6, § 12.1.6; customer's decision
Р-2 of 29.10.2026), in Russian, English and Thai. A company copies and edits them; new or changed
starting templates come with a new migration."""

from typing import Any

from django.db import migrations

from c4rsys.platform.ids import uuid7

# code: (kind code, source, required fields, signers, {language: (title, content)})
STARTERS: dict[str, tuple[str, str, list[str], dict[str, bool], dict[str, tuple[str, str]]]] = {
    "rental_contract": (
        "contract",
        "rental",
        ["client.full_name", "vehicle.name", "rental.start"],
        {"company": True, "client": True},
        {
            "ru": (
                "Договор аренды ТС",
                """# Договор аренды транспортного средства № {{document.number}}

Дата: {{document.date}}

**Арендодатель:** {{company.name}}, {{company.address}}, тел. {{company.phone}}
**Арендатор:** {{client.full_name}}, тел. {{client.phone}}

## 1. Предмет договора
Арендодатель передаёт во временное пользование транспортное средство {{vehicle.name}} ({{vehicle.make}} {{vehicle.model}}, {{vehicle.year}}), госномер {{vehicle.plate}}, VIN {{vehicle.vin}}.

## 2. Срок аренды
- Начало: {{rental.start}}
- Плановое окончание: {{rental.planned_end}}
- Место выдачи: {{rental.issue_place}}

## 3. Стоимость и залог
- Тариф: {{rental.tariff}}
- Стоимость аренды: {{rental.cost}}
- Залог: {{rental.deposit}}

## 4. Условия использования
Арендатор обязуется использовать ТС по назначению, соблюдать правила дорожного движения и вернуть ТС в исправном состоянии.

Арендодатель: ____________ {{employee.full_name}}
Арендатор: ____________ {{client.full_name}}""",
            ),
            "en": (
                "Vehicle rental agreement",
                """# Vehicle rental agreement No. {{document.number}}

Date: {{document.date}}

**Lessor:** {{company.name}}, {{company.address}}, tel. {{company.phone}}
**Lessee:** {{client.full_name}}, tel. {{client.phone}}

## 1. Subject
The lessor gives the vehicle {{vehicle.name}} ({{vehicle.make}} {{vehicle.model}}, {{vehicle.year}}), plate {{vehicle.plate}}, VIN {{vehicle.vin}} for temporary use.

## 2. Term
- Start: {{rental.start}}
- Planned end: {{rental.planned_end}}
- Place of issue: {{rental.issue_place}}

## 3. Price and deposit
- Tariff: {{rental.tariff}}
- Rental cost: {{rental.cost}}
- Deposit: {{rental.deposit}}

## 4. Terms of use
The lessee uses the vehicle for its purpose, follows the traffic rules and returns the vehicle in good condition.

Lessor: ____________ {{employee.full_name}}
Lessee: ____________ {{client.full_name}}""",
            ),
            "th": (
                "สัญญาเช่ารถ",
                """# สัญญาเช่ารถ เลขที่ {{document.number}}

วันที่: {{document.date}}

**ผู้ให้เช่า:** {{company.name}}, {{company.address}}, โทร {{company.phone}}
**ผู้เช่า:** {{client.full_name}}, โทร {{client.phone}}

## 1. ทรัพย์สินที่เช่า
ผู้ให้เช่าส่งมอบรถ {{vehicle.name}} ({{vehicle.make}} {{vehicle.model}}, {{vehicle.year}}) ทะเบียน {{vehicle.plate}} เลขตัวถัง {{vehicle.vin}} ให้ใช้ชั่วคราว

## 2. ระยะเวลาเช่า
- เริ่ม: {{rental.start}}
- สิ้นสุดตามแผน: {{rental.planned_end}}
- สถานที่รับรถ: {{rental.issue_place}}

## 3. ค่าเช่าและเงินมัดจำ
- อัตรา: {{rental.tariff}}
- ค่าเช่า: {{rental.cost}}
- เงินมัดจำ: {{rental.deposit}}

## 4. เงื่อนไขการใช้
ผู้เช่าใช้รถตามวัตถุประสงค์ ปฏิบัติตามกฎจราจร และคืนรถในสภาพดี

ผู้ให้เช่า: ____________ {{employee.full_name}}
ผู้เช่า: ____________ {{client.full_name}}""",
            ),
        },
    ),
    "issue_act": (
        "act",
        "rental",
        ["vehicle.name", "rental.start"],
        {"company": True, "client": True},
        {
            "ru": (
                "Акт выдачи ТС",
                """# Акт выдачи транспортного средства № {{document.number}}

Дата: {{document.date}}

ТС {{vehicle.name}}, госномер {{vehicle.plate}}, передано арендатору {{client.full_name}} {{rental.start}} в месте: {{rental.issue_place}}.

- Пробег при выдаче: {{inspection.issue_odometer}}
- Топливо или заряд: {{inspection.issue_fuel}}

Передал: ____________ {{employee.full_name}}
Принял: ____________ {{client.full_name}}""",
            ),
            "en": (
                "Vehicle handover act",
                """# Vehicle handover act No. {{document.number}}

Date: {{document.date}}

The vehicle {{vehicle.name}}, plate {{vehicle.plate}}, was handed over to {{client.full_name}} on {{rental.start}} at: {{rental.issue_place}}.

- Odometer at issue: {{inspection.issue_odometer}}
- Fuel or charge: {{inspection.issue_fuel}}

Handed over: ____________ {{employee.full_name}}
Received: ____________ {{client.full_name}}""",
            ),
            "th": (
                "บันทึกส่งมอบรถ",
                """# บันทึกส่งมอบรถ เลขที่ {{document.number}}

วันที่: {{document.date}}

ส่งมอบรถ {{vehicle.name}} ทะเบียน {{vehicle.plate}} ให้ {{client.full_name}} เมื่อ {{rental.start}} ที่: {{rental.issue_place}}

- เลขไมล์ขณะส่งมอบ: {{inspection.issue_odometer}}
- น้ำมันหรือแบตเตอรี่: {{inspection.issue_fuel}}

ผู้ส่งมอบ: ____________ {{employee.full_name}}
ผู้รับ: ____________ {{client.full_name}}""",
            ),
        },
    ),
    "return_act": (
        "act",
        "rental",
        ["vehicle.name", "rental.end"],
        {"company": True, "client": True},
        {
            "ru": (
                "Акт возврата ТС",
                """# Акт возврата транспортного средства № {{document.number}}

Дата: {{document.date}}

ТС {{vehicle.name}}, госномер {{vehicle.plate}}, возвращено арендатором {{client.full_name}} {{rental.end}}. Место возврата: {{rental.return_place}}.

- Пробег при возврате: {{inspection.return_odometer}}
- Топливо или заряд: {{inspection.return_fuel}}

{{table:rental.charges}}

Принял: ____________ {{employee.full_name}}
Сдал: ____________ {{client.full_name}}""",
            ),
            "en": (
                "Vehicle return act",
                """# Vehicle return act No. {{document.number}}

Date: {{document.date}}

The vehicle {{vehicle.name}}, plate {{vehicle.plate}}, was returned by {{client.full_name}} on {{rental.end}}. Place of return: {{rental.return_place}}.

- Odometer at return: {{inspection.return_odometer}}
- Fuel or charge: {{inspection.return_fuel}}

{{table:rental.charges}}

Received: ____________ {{employee.full_name}}
Returned: ____________ {{client.full_name}}""",
            ),
            "th": (
                "บันทึกรับคืนรถ",
                """# บันทึกรับคืนรถ เลขที่ {{document.number}}

วันที่: {{document.date}}

{{client.full_name}} คืนรถ {{vehicle.name}} ทะเบียน {{vehicle.plate}} เมื่อ {{rental.end}} สถานที่คืน: {{rental.return_place}}

- เลขไมล์ขณะคืน: {{inspection.return_odometer}}
- น้ำมันหรือแบตเตอรี่: {{inspection.return_fuel}}

{{table:rental.charges}}

ผู้รับคืน: ____________ {{employee.full_name}}
ผู้คืน: ____________ {{client.full_name}}""",
            ),
        },
    ),
    "rental_amendment": (
        "contract",
        "rental",
        ["client.full_name", "vehicle.name"],
        {"company": True, "client": True},
        {
            "ru": (
                "Дополнительное соглашение к договору аренды",
                """# Дополнительное соглашение № {{document.number}}

Дата: {{document.date}}

{{company.name}} и {{client.full_name}} договорились изменить условия аренды ТС {{vehicle.name}}, госномер {{vehicle.plate}}:

- Плановое окончание: {{rental.planned_end}}
- Тариф: {{rental.tariff}}
- Стоимость аренды: {{rental.cost}}

Остальные условия договора аренды не меняются.

Арендодатель: ____________ {{employee.full_name}}
Арендатор: ____________ {{client.full_name}}""",
            ),
            "en": (
                "Amendment to the rental agreement",
                """# Amendment No. {{document.number}}

Date: {{document.date}}

{{company.name}} and {{client.full_name}} agreed to change the terms of the rental of the vehicle {{vehicle.name}}, plate {{vehicle.plate}}:

- Planned end: {{rental.planned_end}}
- Tariff: {{rental.tariff}}
- Rental cost: {{rental.cost}}

The other terms of the rental agreement stay as they are.

Lessor: ____________ {{employee.full_name}}
Lessee: ____________ {{client.full_name}}""",
            ),
            "th": (
                "บันทึกแก้ไขเพิ่มเติมสัญญาเช่า",
                """# บันทึกแก้ไขเพิ่มเติม เลขที่ {{document.number}}

วันที่: {{document.date}}

{{company.name}} และ {{client.full_name}} ตกลงเปลี่ยนเงื่อนไขการเช่ารถ {{vehicle.name}} ทะเบียน {{vehicle.plate}}:

- สิ้นสุดตามแผน: {{rental.planned_end}}
- อัตรา: {{rental.tariff}}
- ค่าเช่า: {{rental.cost}}

เงื่อนไขอื่นของสัญญาเช่าคงเดิม

ผู้ให้เช่า: ____________ {{employee.full_name}}
ผู้เช่า: ____________ {{client.full_name}}""",
            ),
        },
    ),
    "work_act": (
        "act",
        "service_operation",
        ["service.number", "vehicle.name"],
        {"company": True, "client": True},
        {
            "ru": (
                "Акт выполненных работ",
                """# Акт выполненных работ № {{document.number}}

Дата: {{document.date}}

Исполнитель: {{company.name}}
Заказчик: {{client.full_name}}
ТС: {{vehicle.name}}, госномер {{vehicle.plate}}
Сервис № {{service.number}}: {{service.reason}}

## Работы
{{table:service.works}}

## Запчасти и материалы
{{table:service.materials}}

**Итого: {{service.total}}**

Исполнитель: ____________ {{employee.full_name}}
Заказчик: ____________ {{client.full_name}}""",
            ),
            "en": (
                "Act of completed works",
                """# Act of completed works No. {{document.number}}

Date: {{document.date}}

Contractor: {{company.name}}
Customer: {{client.full_name}}
Vehicle: {{vehicle.name}}, plate {{vehicle.plate}}
Service No. {{service.number}}: {{service.reason}}

## Works
{{table:service.works}}

## Parts and materials
{{table:service.materials}}

**Total: {{service.total}}**

Contractor: ____________ {{employee.full_name}}
Customer: ____________ {{client.full_name}}""",
            ),
            "th": (
                "ใบส่งมอบงาน",
                """# ใบส่งมอบงาน เลขที่ {{document.number}}

วันที่: {{document.date}}

ผู้ให้บริการ: {{company.name}}
ลูกค้า: {{client.full_name}}
รถ: {{vehicle.name}} ทะเบียน {{vehicle.plate}}
งานบริการ {{service.number}}: {{service.reason}}

## งาน
{{table:service.works}}

## อะไหล่และวัสดุ
{{table:service.materials}}

**รวม: {{service.total}}**

ผู้ให้บริการ: ____________ {{employee.full_name}}
ลูกค้า: ____________ {{client.full_name}}""",
            ),
        },
    ),
    "order_estimate": (
        "settlement",
        "order",
        ["order.number"],
        {"client": True},
        {
            "ru": (
                "Предварительный заказ-наряд",
                """# Предварительный заказ-наряд № {{document.number}}

Дата: {{document.date}}

{{company.name}}, тел. {{company.phone}}
Клиент: {{client.full_name}}
Заказ № {{order.number}}

{{table:order.lines}}

**Итого: {{order.charged}}**

Согласовано клиентом: ____________ {{client.full_name}}""",
            ),
            "en": (
                "Preliminary work order",
                """# Preliminary work order No. {{document.number}}

Date: {{document.date}}

{{company.name}}, tel. {{company.phone}}
Client: {{client.full_name}}
Order No. {{order.number}}

{{table:order.lines}}

**Total: {{order.charged}}**

Agreed by the client: ____________ {{client.full_name}}""",
            ),
            "th": (
                "ใบสั่งงานเบื้องต้น",
                """# ใบสั่งงานเบื้องต้น เลขที่ {{document.number}}

วันที่: {{document.date}}

{{company.name}} โทร {{company.phone}}
ลูกค้า: {{client.full_name}}
คำสั่งซื้อ {{order.number}}

{{table:order.lines}}

**รวม: {{order.charged}}**

ลูกค้าตกลง: ____________ {{client.full_name}}""",
            ),
        },
    ),
    "payment_receipt": (
        "settlement",
        "order",
        ["order.number", "order.paid"],
        {"company": True},
        {
            "ru": (
                "Квитанция об оплате",
                """# Квитанция № {{document.number}}

Дата: {{document.date}}

{{company.name}} получила от {{client.full_name}} оплату по заказу № {{order.number}}.

- Начислено: {{order.charged}}
- Оплачено: {{order.paid}}
- Остаток: {{order.balance}}

Получил: ____________ {{employee.full_name}}""",
            ),
            "en": (
                "Payment receipt",
                """# Receipt No. {{document.number}}

Date: {{document.date}}

{{company.name}} received a payment from {{client.full_name}} for order No. {{order.number}}.

- Charged: {{order.charged}}
- Paid: {{order.paid}}
- Balance: {{order.balance}}

Received by: ____________ {{employee.full_name}}""",
            ),
            "th": (
                "ใบเสร็จรับเงิน",
                """# ใบเสร็จ เลขที่ {{document.number}}

วันที่: {{document.date}}

{{company.name}} ได้รับชำระเงินจาก {{client.full_name}} สำหรับคำสั่งซื้อ {{order.number}}

- ยอดเรียกเก็บ: {{order.charged}}
- ชำระแล้ว: {{order.paid}}
- คงเหลือ: {{order.balance}}

ผู้รับเงิน: ____________ {{employee.full_name}}""",
            ),
        },
    ),
}


def add(apps: Any, schema_editor: Any) -> None:
    kind = apps.get_model("documents", "DocumentKind")
    template = apps.get_model("documents", "DocumentTemplate")
    for code, (kind_code, source, required, signers, texts) in STARTERS.items():
        kind_id = kind.objects.get(code=kind_code, company__isnull=True).id
        for language, (title, content) in texts.items():
            template.objects.update_or_create(
                code=code,
                language=language,
                company=None,
                defaults={
                    "id": uuid7(),
                    "title": title,
                    "kind_id": kind_id,
                    "source": source,
                    "content": content,
                    "required_fields": required,
                    "signers": signers,
                },
            )


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("documents", "DocumentTemplate").objects.filter(company__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("documents", "0003_templates")]

    operations = [migrations.RunPython(add, remove)]
