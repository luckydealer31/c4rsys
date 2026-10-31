"""Where the income and the expenses of an operation belong (ТЗ § 17.2.4, § 17.3.1–17.3.2,
§ 16.6.5).

* Income is what is charged to clients (`charge_entry`). A charge belongs to the operation it was
  made for; the module of the operation tells its original location of accounting or issue and
  its vehicle. A late recalculation keeps the attribution: it is the same source (§ 17.3.1).
* Expenses of operations that are not money records — the cost of a contractor's work, materials
  consumed by a work — are given by the modules with their analytic dimensions: the order, the
  vehicle, the location the result belongs to and the location of the execution (§ 17.3.2). They
  are dimensions of one expense and never multiply it.

The modules register their readers here; this module does not read their tables (A04).
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class Place:
    location_id: UUID | None
    vehicle_id: UUID | None


@dataclass(frozen=True)
class Expense:
    kind: str  # "contractor", "contractor_material", "material"
    amount: Decimal
    currency: str
    at: datetime
    # The location the result belongs to (§ 17.3.1) and where the work was done (§ 17.3.2).
    location_id: UUID | None
    execution_location_id: UUID | None
    vehicle_id: UUID | None
    order_id: UUID | None
    source_id: UUID
    complete: bool = True  # every consumed part has a known cost


# (company_id, source_ids) -> {source_id: Place}
SourceReader = Callable[[UUID, list[UUID]], dict[UUID, Place]]
# (company_id, start, end) -> expenses of the period
ExpenseReader = Callable[[UUID, datetime, datetime], Iterable[Expense]]

_sources: dict[str, SourceReader] = {}
_expenses: list[ExpenseReader] = []


def register_source(source_type: str, reader: SourceReader) -> None:
    _sources[source_type] = reader


def register_expenses(reader: ExpenseReader) -> None:
    _expenses.append(reader)


def places(company_id: UUID, sources: Iterable[tuple[str, UUID]]) -> dict[tuple[str, UUID], Place]:
    by_type: dict[str, list[UUID]] = {}
    for source_type, source_id in sources:
        by_type.setdefault(source_type, []).append(source_id)
    found: dict[tuple[str, UUID], Place] = {}
    for source_type, ids in by_type.items():
        reader = _sources.get(source_type)
        if reader is None:
            continue
        for source_id, place in reader(company_id, sorted(set(ids))).items():
            found[(source_type, source_id)] = place
    return found


def expenses(company_id: UUID, start: datetime, end: datetime) -> list[Expense]:
    return [e for reader in _expenses for e in reader(company_id, start, end)]
