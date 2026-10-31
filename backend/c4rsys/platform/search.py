"""The single search line over the working records of the active company (ТЗ § 2.8.1–2.8.3).

Each module registers a provider for its own records; this module neither reads their tables nor
decides their rights (A04). A provider gets the actor of the active company, so a found record is
one the employee may already see in the section: the search widens no rights and shows nothing of
other companies (§ 2.8.2). Exact matches come first (§ 2.8.3); a name matches by a part of the
text; typos are not corrected.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from c4rsys.platform.commands import Actor

MAX_TEXT = 100
PER_KIND = 10


@dataclass(frozen=True)
class Query:
    text: str
    # A sequential number of the company: «12», «#12», «№ 12».
    number: int | None
    # The id of a record, when the text is one.
    uuid: UUID | None

    @property
    def lowered(self) -> str:
        return self.text.lower()


@dataclass(frozen=True)
class Hit:
    kind: str
    id: str
    label: str
    detail: str = ""
    exact: bool = False
    # Where the record is opened when that is not the card of its kind (e.g. an order of a
    # payment); the interface builds the path.
    extra: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "label": self.label,
            "detail": self.detail,
            "exact": self.exact,
            **({"extra": self.extra} if self.extra else {}),
        }


Provider = Callable[[Actor, UUID, Query, int], list[Hit]]
_providers: dict[str, Provider] = {}
_order: list[str] = []
# The order of the groups in the answer; a kind not listed goes last.
PREFERRED = [
    "order",
    "rental",
    "service_operation",
    "trip",
    "payment",
    "document",
    "vehicle",
    "client",
    "counterparty",
    "item",
    "task",
]

NUMBER = re.compile(r"^(?:#|№|n|no\.?)?\s*0*(\d{1,12})$", re.IGNORECASE)


def register(kind: str, provider: Provider) -> None:
    if kind not in _providers:
        _order.append(kind)
        _order.sort(key=lambda k: PREFERRED.index(k) if k in PREFERRED else len(PREFERRED))
    _providers[kind] = provider


def kinds() -> list[str]:
    return list(_order)


def parse(text: str) -> Query:
    text = " ".join(text.split())[:MAX_TEXT]
    number = None
    found = NUMBER.match(text)
    if found:
        number = int(found.group(1))
    uuid = None
    try:
        uuid = UUID(text)
    except ValueError:
        pass
    return Query(text=text, number=number, uuid=uuid)


def ranked(hits: list[Hit], limit: int) -> list[Hit]:
    """Exact matches first; the rest keep the provider's order."""
    return sorted(hits, key=lambda h: not h.exact)[:limit]


def search(
    actor: Actor, company_id: UUID, text: str, only: list[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    query = parse(text)
    if not query.text:
        return {}
    answer: dict[str, list[dict[str, Any]]] = {}
    for kind in _order:
        if only and kind not in only:
            continue
        hits = ranked(_providers[kind](actor, company_id, query, PER_KIND), PER_KIND)
        if hits:
            answer[kind] = [h.payload() for h in hits]
    return answer


def exact_text(value: str, query: Query) -> bool:
    return bool(value) and value.strip().lower() == query.lowered
