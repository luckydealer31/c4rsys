"""The platform catalog of parts from the customer's file (decision К1 of 25.10.2026) and the
choice of a category for items already entered by their names (К7); the earlier text of the
category stays on the item as a note.

The data file `data/part_categories.json` is built from `docs/stage_warehouse/
parts_dictionary_full.csv`; English and Thai names and keywords are a PROPOSAL (К4)."""

import json
from pathlib import Path
from typing import Any

from django.db import migrations

DATA = Path(__file__).resolve().parent.parent / "data" / "part_categories.json"


def load(apps: Any, schema_editor: Any) -> None:
    category_model = apps.get_model("warehouse", "PartCategory")
    subcategory_model = apps.get_model("warehouse", "PartSubcategory")
    type_model = apps.get_model("warehouse", "PartType")
    kinds = []
    for ci, entry in enumerate(json.loads(DATA.read_text(encoding="utf-8"))):
        category = category_model.objects.create(
            code=entry["code"], name_ru=entry["ru"], name_en=entry["en"], name_th=entry["th"], position=ci
        )
        for si, sub in enumerate(entry["subcategories"]):
            subcategory = subcategory_model.objects.create(
                code=sub["code"],
                category=category,
                name_ru=sub["ru"],
                name_en=sub["en"],
                name_th=sub["th"],
                position=si,
            )
            for ti, kind in enumerate(sub["types"]):
                words = kind["keywords"]
                type_model.objects.create(
                    subcategory=subcategory,
                    name_ru=kind["ru"],
                    keywords_ru=words["ru"],
                    keywords_en=words["en"],
                    keywords_th=words["th"],
                    position=ti,
                )
                kinds.append((subcategory.id, category.id, [words["ru"], words["en"], words["th"]]))

    from c4rsys.warehouse.categories import rank

    item_model = apps.get_model("warehouse", "Item")
    for item in item_model.objects.filter(part_category__isnull=True):
        found = rank(item.name, kinds)
        if found:
            item.part_category_id = found[0].category_id
            item.part_subcategory_id = found[0].subcategory_id
            item.save(update_fields=["part_category", "part_subcategory"])


def unload(apps: Any, schema_editor: Any) -> None:
    apps.get_model("warehouse", "Item").objects.update(part_category=None, part_subcategory=None)
    apps.get_model("warehouse", "PartType").objects.all().delete()
    apps.get_model("warehouse", "PartSubcategory").objects.all().delete()
    apps.get_model("warehouse", "PartCategory").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("warehouse", "0015_part_categories")]

    operations = [migrations.RunPython(load, unload)]
