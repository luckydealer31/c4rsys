"""Loading of the vehicle catalog (§ 8.1.11) from the customer's CSV files.

`cars.csv` (24.10.2026): passenger cars — make code and names, popularity, country, model code
and names, class, years. Loading updates existing rows by code and adds new ones; it never
removes a make or a model, since vehicles may refer to them.
"""

import csv
from pathlib import Path

from django.db import transaction
from django.db.models import Model

CATALOG_DIR = Path(__file__).resolve().parent
PASSENGER_CAR = "passenger_car"


def _year(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.isdigit() else None


def load_passenger_cars(
    make_model: type[Model], model_model: type[Model], path: Path | None = None
) -> int:
    """Upserts makes and models in bulk; returns the number of models. The models are passed in
    so a data migration can use its historical models."""
    source = path or CATALOG_DIR / "cars.csv"
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    makes: dict[str, Model] = {}
    for row in rows:
        code = row["ID_MARK"].strip()
        makes.setdefault(
            code,
            make_model(
                code=code,
                vehicle_type=PASSENGER_CAR,
                name=row["Марка"].strip(),
                name_cyrillic=row["Марка кириллица"].strip(),
                popular=row["Популярная марка"].strip().lower() == "true",
                country=row["Страна"].strip(),
            ),
        )
    models = [
        model_model(
            code=row["MODEL_ID"].strip(),
            make_id=row["ID_MARK"].strip(),
            name=row["Модель"].strip(),
            name_cyrillic=row["Модель кириллица"].strip(),
            car_class=row["Класс"].strip(),
            year_from=_year(row["Год от"]),
            year_to=_year(row["Год до"]),
        )
        for row in rows
    ]
    with transaction.atomic():
        make_model._default_manager.bulk_create(
            list(makes.values()),
            update_conflicts=True,
            unique_fields=["code"],
            update_fields=["vehicle_type", "name", "name_cyrillic", "popular", "country"],
            batch_size=1000,
        )
        model_model._default_manager.bulk_create(
            models,
            update_conflicts=True,
            unique_fields=["code"],
            update_fields=["make", "name", "name_cyrillic", "car_class", "year_from", "year_to"],
            batch_size=1000,
        )
    return len(models)
