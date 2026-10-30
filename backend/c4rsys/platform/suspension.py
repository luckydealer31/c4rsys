"""System blocking of users and companies seen by the other modules (ТЗ § 24.2.2–24.2.4).

The blocks themselves belong to the internal interface of the system staff (c4rsys.backoffice),
which registers itself here as the provider. The other modules ask only this module, so none of
them depends on the internal interface:

* the access to a company and its commands stop (companies, the command guard);
* automatic actions of a blocked company wait: repeating tasks, auto-cancel of bookings, the move
  of a deposit to the balance, external signing links (tasks, rentals, finance, documents);
* after the company is unblocked the modules apply § 24.2.3 through `register_resumed`.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


class Provider(Protocol):
    def company_block(self, company_id: UUID) -> dict[str, Any] | None: ...

    def blocked_company_ids(self) -> set[UUID]: ...

    def user_block(self, user_id: UUID) -> dict[str, Any] | None: ...


_provider: Provider | None = None
Resumed = Callable[[UUID, datetime, datetime], None]
_resumed: list[Resumed] = []


def register_provider(provider: Provider) -> None:
    global _provider
    _provider = provider


def register_resumed(handler: Resumed) -> None:
    """`handler(company_id, blocked_at, unblocked_at)` runs in the transaction that ends the
    block of the company (§ 24.2.3)."""
    _resumed.append(handler)


def company_block(company_id: UUID) -> dict[str, Any] | None:
    """What may be told about the block of a company in force, or None."""
    return _provider.company_block(company_id) if _provider is not None else None


def company_suspended(company_id: UUID) -> bool:
    return company_block(company_id) is not None


def suspended_company_ids() -> set[UUID]:
    return _provider.blocked_company_ids() if _provider is not None else set()


def user_block(user_id: UUID) -> dict[str, Any] | None:
    return _provider.user_block(user_id) if _provider is not None else None


def resumed(company_id: UUID, blocked_at: datetime, unblocked_at: datetime) -> None:
    for handler in _resumed:
        handler(company_id, blocked_at, unblocked_at)
