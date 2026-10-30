"""The starting kind «Act of inspection» and its starting template in Russian, English and Thai
(ТЗ § 10.2.2, § 18.1.5, § 18.1.8, § 18.2.4; customer's decision В-8.3 of 30.10.2026). The act is
of its own kind, so it is not taken for the act of completed works of a service (§ 16.7.6), and a
stage of a rental can require it by the kind or by the template (§ 18.4.1). The client's side is
chosen by the act itself (`inspection_act.make`), so the template names no required signer."""

from typing import Any

from django.db import migrations

from c4rsys.platform.ids import uuid7

CODE = "inspection_act"
KIND = "inspection_act"
REQUIRED = ["vehicle.name", "inspection.performed_at"]

TEXTS: dict[str, tuple[str, str]] = {
    "ru": (
        "Акт осмотра ТС",
        """# Акт осмотра транспортного средства № {{document.number}}

Дата: {{document.date}}

**Компания:** {{company.name}}, {{company.address}}, тел. {{company.phone}}
**ТС:** {{vehicle.name}} ({{vehicle.make}} {{vehicle.model}}, {{vehicle.year}}), госномер {{vehicle.plate}}, VIN {{vehicle.vin}}

## 1. Осмотр
- Вид осмотра: {{inspection.kind}}
- Применение: {{inspection.application}} {{inspection.operation}}
- Время осмотра: {{inspection.performed_at}}
- Место: {{inspection.place}}
- Провёл осмотр: {{inspection.performer}}
- Второй участник: {{inspection.participant}}
- Проверка состояния: {{inspection.check_status}}

## 2. Показания
- Пробег: {{inspection.odometer}}
- Топливо или заряд: {{inspection.fuel}}
- Чистота: {{inspection.cleanliness}}

## 3. Повреждения
{{table:inspection.damages}}

## 4. Фото и видео
Снимков: {{inspection.photo_count}}, видео: {{inspection.video_count}}
{{table:inspection.photos}}

С результатом осмотра ознакомлен(а).

Осмотр провёл: ____________ {{inspection.performer}}
Второй участник: ____________ {{inspection.participant}}""",
    ),
    "en": (
        "Vehicle inspection act",
        """# Vehicle inspection act No. {{document.number}}

Date: {{document.date}}

**Company:** {{company.name}}, {{company.address}}, tel. {{company.phone}}
**Vehicle:** {{vehicle.name}} ({{vehicle.make}} {{vehicle.model}}, {{vehicle.year}}), plate {{vehicle.plate}}, VIN {{vehicle.vin}}

## 1. Inspection
- Kind: {{inspection.kind}}
- Applies to: {{inspection.application}} {{inspection.operation}}
- Time: {{inspection.performed_at}}
- Place: {{inspection.place}}
- Performed by: {{inspection.performer}}
- Second participant: {{inspection.participant}}
- Condition check: {{inspection.check_status}}

## 2. Readings
- Odometer: {{inspection.odometer}}
- Fuel or charge: {{inspection.fuel}}
- Cleanliness: {{inspection.cleanliness}}

## 3. Damages
{{table:inspection.damages}}

## 4. Photos and videos
Photos: {{inspection.photo_count}}, videos: {{inspection.video_count}}
{{table:inspection.photos}}

I have read the result of the inspection.

Performed by: ____________ {{inspection.performer}}
Second participant: ____________ {{inspection.participant}}""",
    ),
    "th": (
        "บันทึกการตรวจสภาพรถ",
        """# บันทึกการตรวจสภาพรถ เลขที่ {{document.number}}

วันที่: {{document.date}}

**บริษัท:** {{company.name}}, {{company.address}}, โทร {{company.phone}}
**รถ:** {{vehicle.name}} ({{vehicle.make}} {{vehicle.model}}, {{vehicle.year}}) ทะเบียน {{vehicle.plate}} เลขตัวถัง {{vehicle.vin}}

## 1. การตรวจสภาพ
- ประเภท: {{inspection.kind}}
- ใช้กับ: {{inspection.application}} {{inspection.operation}}
- เวลา: {{inspection.performed_at}}
- สถานที่: {{inspection.place}}
- ผู้ตรวจ: {{inspection.performer}}
- ผู้เข้าร่วมอีกฝ่าย: {{inspection.participant}}
- การตรวจสภาพ: {{inspection.check_status}}

## 2. ค่าที่อ่านได้
- เลขไมล์: {{inspection.odometer}}
- น้ำมันหรือแบตเตอรี่: {{inspection.fuel}}
- ความสะอาด: {{inspection.cleanliness}}

## 3. ความเสียหาย
{{table:inspection.damages}}

## 4. รูปภาพและวิดีโอ
รูปภาพ: {{inspection.photo_count}}, วิดีโอ: {{inspection.video_count}}
{{table:inspection.photos}}

ข้าพเจ้าได้รับทราบผลการตรวจสภาพแล้ว

ผู้ตรวจ: ____________ {{inspection.performer}}
ผู้เข้าร่วมอีกฝ่าย: ____________ {{inspection.participant}}""",
    ),
}


def add(apps: Any, schema_editor: Any) -> None:
    kind = apps.get_model("documents", "DocumentKind")
    template = apps.get_model("documents", "DocumentTemplate")
    found, _ = kind.objects.get_or_create(code=KIND, company=None, defaults={"id": uuid7()})
    for language, (title, content) in TEXTS.items():
        template.objects.update_or_create(
            code=CODE,
            language=language,
            company=None,
            create_defaults={
                "id": uuid7(),
                "title": title,
                "kind_id": found.id,
                "source": "inspection",
                "content": content,
                "required_fields": REQUIRED,
                "signers": {},
            },
            defaults={
                "title": title,
                "kind_id": found.id,
                "source": "inspection",
                "content": content,
                "required_fields": REQUIRED,
                "signers": {},
            },
        )


def remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("documents", "DocumentTemplate").objects.filter(
        company__isnull=True, code=CODE
    ).delete()
    apps.get_model("documents", "DocumentKind").objects.filter(
        company__isnull=True, code=KIND
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("documents", "0008_inspection_act")]

    operations = [migrations.RunPython(add, remove)]
