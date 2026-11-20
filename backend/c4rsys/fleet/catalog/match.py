"""Makes and models of the catalog named in a free text (customer's decision of 20.11.2026): the
name of a warehouse item «door right ford focus» offers Ford Focus as its compatibility.

A make is found by its name or its Cyrillic name standing as whole words; a model is looked for
only among the models of a found make. The result is an offer, nothing is written."""

import re
from dataclasses import dataclass

from c4rsys.fleet.models import VehicleMake, VehicleModel

LIMIT = 5


@dataclass(frozen=True)
class Match:
    vehicle_type: str
    make: str
    make_name: str
    model: str | None
    model_name: str


def _has(text: str, *names: str) -> int:
    """Length of the longest of the names standing in the text as whole words; 0 — none."""
    best = 0
    for name in names:
        words = name.strip().lower()
        if words and re.search(rf"(?<!\w){re.escape(words)}(?!\w)", text):
            best = max(best, len(words))
    return best


def in_text(text: str) -> list[Match]:
    text = text.lower()
    if not text.strip():
        return []
    found: list[Match] = []
    makes = [m for m in VehicleMake.objects.order_by("name") if _has(text, m.name, m.name_cyrillic)]
    for make in makes:
        models = [
            (m, _has(text, m.name), _has(text, m.name_cyrillic))
            for m in VehicleModel.objects.filter(make=make).order_by("name")
        ]
        named = [(m, max(latin, cyrillic)) for m, latin, cyrillic in models if latin or cyrillic]
        # «Focus RS» in the text names that model, not «Focus» as well: the longest name wins
        # over the names it contains.
        longest = [
            m
            for m, _ in named
            if not any(
                other.code != m.code
                and len(other.name) > len(m.name)
                and _has(other.name.lower(), m.name)
                for other, _ in named
            )
        ]
        if longest:
            found += [
                Match(make.vehicle_type, make.code, make.name, m.code, m.name) for m in longest
            ]
        else:
            found.append(Match(make.vehicle_type, make.code, make.name, None, ""))
    return found[:LIMIT]
