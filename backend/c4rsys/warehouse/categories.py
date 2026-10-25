"""Categories and subcategories of parts and their choice by the name of an item (decisions
К1–К7 of 25.10.2026).

A name is compared with the keywords of every kind of part in Russian, English and Thai. A
keyword matches a word of the name that starts with its stem (Russian and English endings
differ), or — for Thai, written without spaces — any part of the name. The kind with more
matched keywords and a larger share of its keywords wins; the best match of each subcategory
is offered, the first one is applied (К2, К3).
"""

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import QuerySet

from c4rsys.platform import errors
from c4rsys.warehouse.models import PartCategory, PartSubcategory, PartType

_WORD = re.compile(r"[\w]+", re.UNICODE)
_THAI = re.compile(r"[฀-๿]")
MAX_SUGGESTIONS = 5


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def _stem(keyword: str) -> str:
    """A crude stem: Russian and English words lose their ending."""
    if len(keyword) > 5:
        return keyword[:-2]
    if len(keyword) > 3:
        return keyword[:-1]
    return keyword


def _matches(keyword: str, words: list[str], text: str) -> bool:
    keyword = _normalize(keyword)
    if _THAI.search(keyword):
        return keyword in text
    stem = _stem(keyword)
    return any(word.startswith(stem) for word in words)


@dataclass(frozen=True)
class Candidate:
    subcategory_id: UUID
    category_id: UUID
    covered: int  # words of the name explained by the kind
    matched: int  # keywords of the kind found in the name
    share: float

    @property
    def order(self) -> tuple[int, int, float]:
        return (self.covered, self.matched, self.share)


def rank(name: str, kinds: list[tuple[UUID, UUID, list[list[str]]]]) -> list[Candidate]:
    """Candidates by subcategory, the best first. `kinds` are (subcategory, category, keyword
    lists by language); a name is compared with each language apart. A kind counts with two
    matched keywords, or one when that is at least half of the keywords of that language."""
    text = _normalize(name)
    words = _WORD.findall(text)
    best: dict[UUID, Candidate] = {}
    for subcategory_id, category_id, languages in kinds:
        for keywords in languages:
            if not keywords:
                continue
            hits = [keyword for keyword in keywords if _matches(keyword, words, text)]
            matched = len(hits)
            share = matched / len(keywords)
            if matched == 0 or (matched < 2 and share < 0.5):
                continue
            stems = [_stem(_normalize(h)) for h in hits]
            covered = sum(1 for word in words if any(word.startswith(stem) for stem in stems))
            candidate = Candidate(subcategory_id, category_id, max(covered, 1), matched, share)
            current = best.get(subcategory_id)
            if current is None or candidate.order > current.order:
                best[subcategory_id] = candidate
    # The kind explaining more words of the name wins; then more keywords, then their share.
    ranked = sorted(best.values(), key=lambda c: c.order, reverse=True)
    return ranked[:MAX_SUGGESTIONS]


def _kinds() -> list[tuple[UUID, UUID, list[list[str]]]]:
    return [
        (t.subcategory_id, t.subcategory.category_id, [t.keywords_ru, t.keywords_en, t.keywords_th])
        for t in PartType.objects.select_related("subcategory")
    ]


def suggest(name: str) -> list[Candidate]:
    if not name.strip():
        return []
    return rank(name, _kinds())


def tree() -> QuerySet[PartCategory]:
    return PartCategory.objects.prefetch_related("subcategories").order_by("position")


def names(obj: PartCategory | PartSubcategory) -> dict[str, str]:
    return {"ru": obj.name_ru, "en": obj.name_en, "th": obj.name_th}


def tree_payload() -> list[dict[str, Any]]:
    return [
        {
            "id": str(c.id),
            "code": c.code,
            "names": names(c),
            "subcategories": [
                {"id": str(s.id), "code": s.code, "names": names(s)}
                for s in sorted(c.subcategories.all(), key=lambda s: s.position)
            ],
        }
        for c in tree()
    ]


def validate(
    category_id: UUID | None, subcategory_id: UUID | None
) -> tuple[UUID | None, UUID | None]:
    """A subcategory belongs to its category; with a subcategory alone its category is taken."""
    if subcategory_id is not None:
        sub = PartSubcategory.objects.filter(pk=subcategory_id).first()
        if sub is None:
            raise errors.validation_error("part_subcategory", code="CATEGORY_UNKNOWN")
        if category_id is not None and category_id != sub.category_id:
            raise errors.validation_error("part_subcategory", code="SUBCATEGORY_OF_OTHER_CATEGORY")
        return sub.category_id, sub.id
    if category_id is not None and not PartCategory.objects.filter(pk=category_id).exists():
        raise errors.validation_error("part_category", code="CATEGORY_UNKNOWN")
    return category_id, None


def by_text(text: str) -> tuple[UUID | None, UUID | None]:
    """A category given as text in an import (К5): a subcategory or category name in any
    language, «Category / Subcategory» or «Category → Subcategory»."""
    value = _normalize(text.strip())
    if not value:
        return None, None
    parts = [p.strip() for p in re.split(r"[/→>]", value) if p.strip()]
    for category in tree():
        if (
            _normalize(category.name_ru) in parts
            or _normalize(category.name_en) in parts
            or _normalize(category.name_th) in parts
        ):
            for sub in category.subcategories.all():
                if any(_normalize(n) in parts[1:] for n in (sub.name_ru, sub.name_en, sub.name_th)):
                    return category.id, sub.id
            return category.id, None
    return None, None
