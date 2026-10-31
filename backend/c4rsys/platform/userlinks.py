"""Links of a personal account kept by the modules, for the merge and the split of two global
accounts by the support (ТЗ § 24.1.8).

Each module that refers to a person by his account registers a provider: it lists the links of
an account (contacts, places of work, positions, shares, profile links, vehicle ownership…) and
moves chosen ones from one account to another in its own tables. The support never rewrites
business records of companies (documents, rentals, payments, authors of actions): they keep the
account that did them, and the merged account resolves to the main one (`accounts.resolve`).

A link has a `conflict_key` when two such links cannot belong to one account at once (two current
places of work in one company, two current shares in one company…). Two links with the same key
on the accounts of a merge — or on one account of a split — are a conflict that the support
resolves before the action; nothing is resolved automatically.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class UserLink:
    module: str
    kind: str
    record_id: UUID
    # What the support sees: a readable description without closed data of companies.
    label: dict[str, Any] = field(default_factory=dict)
    conflict_key: str = ""
    # A link that blocks a merge until it is finished (a pending share transfer).
    blocking: bool = False

    @property
    def ref(self) -> tuple[str, str, str]:
        return (self.module, self.kind, str(self.record_id))


class Provider(Protocol):
    module: str

    def links(self, user_id: UUID) -> list[UserLink]: ...

    def move(self, kind: str, record_ids: list[UUID], source: UUID, target: UUID) -> int:
        """Moves the records from `source` to `target` in the caller's transaction, with row
        locks; returns how many moved. A record that no longer refers to `source` stays."""
        ...


_providers: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    _providers[provider.module] = provider


def providers() -> list[Provider]:
    return list(_providers.values())


def provider(module: str) -> Provider:
    return _providers[module]


def links_of(user_id: UUID) -> list[UserLink]:
    return [link for p in providers() for link in p.links(user_id)]


def conflicts(accounts: dict[str, list[tuple[str, UserLink]]]) -> list[dict[str, Any]]:
    """For each resulting account — its links with the account they come from — the links that
    cannot be together on it, and the links that block the action by themselves."""
    found: list[dict[str, Any]] = []
    for account, entries in accounts.items():
        by_key: dict[str, list[tuple[str, UserLink]]] = {}
        for origin, link in entries:
            if link.blocking:
                found.append(
                    {
                        "account": account,
                        "key": link.conflict_key or link.kind,
                        "links": [_entry(origin, link)],
                    }
                )
            elif link.conflict_key:
                by_key.setdefault(link.conflict_key, []).append((origin, link))
        for key, same in by_key.items():
            if len(same) > 1:
                found.append(
                    {
                        "account": account,
                        "key": key,
                        "links": [_entry(origin, link) for origin, link in same],
                    }
                )
    return found


def _entry(side: str, link: UserLink) -> dict[str, Any]:
    return {
        "side": side,
        "module": link.module,
        "kind": link.kind,
        "record_id": str(link.record_id),
        "label": link.label,
    }


def payload(link: UserLink) -> dict[str, Any]:
    return {
        "module": link.module,
        "kind": link.kind,
        "record_id": str(link.record_id),
        "label": link.label,
        "conflict_key": link.conflict_key or None,
        "blocking": link.blocking,
    }
