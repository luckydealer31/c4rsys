"""Import templates of clients and counterparties (ТЗ § 22.3.2–22.3.3, § 22.2.3).

The type is required: a person with first and last name, or a company with its name; and at
least one contact. Contacts are phones and emails (decision of 22.10.2026); messengers are OPEN.
Matches are searched first by the ID given in the file — the company's external ID or the
c4rsys ID of a linked profile — then by a coinciding phone or email; a name is never enough.
An ID pointing to one card while a contact points to another is a conflict. Counterparties are
searched in their own list only: a client card with the same data blocks nothing. A c4rsys ID
sends no request: linking is started from the card after the import.
"""

from datetime import date
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.accounts import services as accounts
from c4rsys.companies.models import Company
from c4rsys.parties import counterparties, services
from c4rsys.parties.models import (
    ClientCard,
    ClientContact,
    Counterparty,
    CounterpartyContact,
    ProfileLink,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.warehouse.imports import Checked, Scope

CONTACT_COLUMNS = (
    ("phone", "phone"),
    ("phone_2", "phone"),
    ("email", "email"),
    ("email_2", "email"),
)
NAME_COLUMNS: dict[str, int] = {
    "kind": 10,
    "first_name": 150,
    "last_name": 150,
    "middle_name": 150,
}
CONTACTS: dict[str, int] = {
    "phone": 40,
    "phone_messenger": 20,
    "phone_2": 40,
    "phone_2_messenger": 20,
    "email": 254,
    "email_2": 254,
}
HELP_COMMON = {
    "kind": (
        "person or company; required",
        "person (физлицо) или company (компания); обязательно",
        "person",
    ),
    "first_name": ("First name; required for a person", "Имя; обязательно для физлица", "Ivan"),
    "last_name": ("Last name; required for a person", "Фамилия; обязательно для физлица", "Petrov"),
    "middle_name": ("Middle name", "Отчество", ""),
    "company_name": ("Name; required for a company", "Название; обязательно для компании", ""),
    "phone": (
        "Phone; at least one contact is required",
        "Телефон; нужен хотя бы один контакт",
        "+66 81 234 5678",
    ),
    "phone_messenger": (
        "Priority messenger by this phone: "
        + ", ".join(services.MESSENGERS)
        + "; a call or SMS when empty",
        "Приоритетный мессенджер по этому телефону: "
        + ", ".join(services.MESSENGERS)
        + "; если пусто — звонок или SMS",
        "whatsapp",
    ),
    "phone_2": ("Second phone", "Второй телефон", ""),
    "phone_2_messenger": (
        "Messenger by the second phone",
        "Мессенджер по второму телефону",
        "",
    ),
    "email": ("Email", "Email", "ivan@example.com"),
    "email_2": ("Second email", "Второй email", ""),
    "address": ("Address", "Адрес", ""),
    "comment": ("Comment", "Комментарий", ""),
    "external_id": (
        "Your own ID of the card, used to find it again",
        "Ваш внешний ID карточки для повторного поиска",
        "CL-001",
    ),
    "c4rsys_id": (
        "c4rsys ID of the person's profile (USR-…); sends no request",
        "c4rsys ID профиля (USR-…); запрос на связь не отправляется",
        "",
    ),
}


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


class PartiesTemplate:
    """Common rules of the two lists; the differences are in the attributes."""

    version = 1
    kind: str
    sheet: str
    permission: str
    object_type: str
    columns: dict[str, int]
    help: dict[str, tuple[str, str, str]]
    required: tuple[str, ...] = ("kind",)
    model: type[ClientCard] | type[Counterparty]
    contact_model: type[ClientContact] | type[CounterpartyContact]
    contact_card: str

    def references(self, company_id: UUID) -> list[tuple[str, list[list[str]]]]:
        return []

    def _cards(self, company_id: UUID) -> QuerySet[Any]:
        return self.model.objects.filter(company_id=company_id)

    def check_row(self, scope: Scope, values: dict[str, str]) -> Checked:
        result = Checked()
        kind = values.get("kind", "").strip().lower()
        if not kind:
            result.error("kind", "FIELD_REQUIRED")
        elif kind not in ("person", "company"):
            result.error("kind", "VALUE_UNKNOWN")
        if kind == "person":
            for key in ("first_name", "last_name"):
                if not values.get(key):
                    result.error(key, "FIELD_REQUIRED")
            if values.get("company_name"):
                result.error("company_name", "FIELD_NOT_APPLICABLE")
        elif kind == "company":
            if not values.get("company_name"):
                result.error("company_name", "FIELD_REQUIRED")
            for key in ("first_name", "last_name", "middle_name", "birth_date"):
                if values.get(key):
                    result.error(key, "FIELD_NOT_APPLICABLE")
        birth = values.get("birth_date", "")
        if birth:
            parsed = _date(birth)
            if parsed is None:
                result.error("birth_date", "DATE_INVALID")
            elif parsed > timezone.localdate():
                result.error("birth_date", "DATE_IN_FUTURE")
        contacts: list[services.Contact] = []
        for column, contact_kind in CONTACT_COLUMNS:
            if values.get(column):
                try:
                    contacts.append(
                        services.normalize_contact(
                            contact_kind,
                            values[column],
                            column,
                            values.get(f"{column}_messenger", ""),
                        )
                    )
                except errors.ApiError as exc:
                    result.error(column, exc.code)
        for column in ("phone", "phone_2"):
            if values.get(f"{column}_messenger") and not values.get(column):
                result.error(f"{column}_messenger", "MESSENGER_WITHOUT_PHONE")
        if not contacts and not any(values.get(c) for c, _ in CONTACT_COLUMNS):
            result.error("phone", "CONTACT_REQUIRED")
        lookup = values.get("c4rsys_id", "").strip().upper()
        if lookup and not lookup.startswith("USR-"):
            result.error("c4rsys_id", "VALUE_INVALID")
        self._matches(scope.company_id, values, lookup, contacts, result)
        return result

    def _matches(
        self,
        company_id: UUID,
        values: dict[str, str],
        lookup: str,
        contacts: list[services.Contact],
        out: Checked,
    ) -> None:
        cards = self._cards(company_id)
        by_id: set[str] = set()

        def add(found: QuerySet[Any], by: str, into: set[str]) -> None:
            for card in found[:5]:
                out.match(card.id, str(card), by)
                into.add(str(card.id))

        if values.get("external_id"):
            add(cards.filter(external_id=values["external_id"]), "external_id", by_id)
        if lookup:
            # Only cards already linked to the profile are found; nothing is told about it.
            user = accounts.find_active_user(lookup)
            if user is not None:
                add(cards.filter(linked_user_id=user.id), "c4rsys_id", by_id)
        others: set[str] = set()
        for contact in contacts:
            ids = self.contact_model.objects.filter(
                company_id=company_id, kind=contact.kind, value_normalized=contact.normalized
            ).values_list(f"{self.contact_card}_id", flat=True)
            add(cards.filter(pk__in=ids), contact.kind, others)
        out.conflict = bool(by_id and others - by_id)

    def _contacts(self, values: dict[str, str]) -> list[services.Contact]:
        contacts: list[services.Contact] = []
        for column, contact_kind in CONTACT_COLUMNS:
            if values.get(column):
                contact = services.normalize_contact(
                    contact_kind, values[column], column, values.get(f"{column}_messenger", "")
                )
                if all(
                    (c.kind, c.normalized) != (contact.kind, contact.normalized) for c in contacts
                ):
                    contacts.append(contact)
        return contacts

    def remove(self, ctx: CommandContext, company_id: UUID, record_ids: list[UUID]) -> None:
        """A card linked to a profile is a confirmed link and stops the cancel (§ 22.2.5); so does a
        request sent from it: its history is kept (PROPOSAL)."""
        cards: list[Any] = list(
            self.model.objects.select_for_update().filter(company_id=company_id, pk__in=record_ids)
        )
        requested = set(
            ProfileLink.objects.filter(
                Q(client_id__in=record_ids) | Q(counterparty_id__in=record_ids)
            ).values_list(f"{self.contact_card}_id", flat=True)
        )
        linked = [
            c
            for c in cards
            if c.linked_user_id is not None or c.linked_company_id is not None or c.id in requested
        ]
        if linked:
            raise errors.business_rule(
                "IMPORT_CANCEL_BLOCKED",
                blockers=[{"reason": "linked_profile", "id": str(c.id)} for c in linked],
            )
        for card in cards:
            card.contacts.all().delete()
            card.delete()


class ClientsTemplate(PartiesTemplate):
    kind = "clients"
    sheet = "clients"
    permission = "client.create"
    object_type = "client_card"
    model = ClientCard
    contact_model = ClientContact
    contact_card = "client"
    columns = {
        **NAME_COLUMNS,
        "birth_date": 10,
        "company_name": 200,
        **CONTACTS,
        "address": 500,
        "comment": 2000,
        "external_id": 100,
        "c4rsys_id": 40,
    }
    help = {
        **HELP_COMMON,
        "birth_date": ("Birth date, YYYY-MM-DD", "Дата рождения, ГГГГ-ММ-ДД", ""),
    }

    def create(
        self,
        ctx: CommandContext,
        company: Company,
        values: dict[str, str],
        extra: dict[str, Any],
        *,
        duplicate: bool,
        operation_id: UUID,
        session: dict[str, Any],
    ) -> UUID:
        birth = values.get("birth_date", "")
        data = services.ClientData(
            kind=values["kind"].strip().lower(),
            first_name=values.get("first_name", ""),
            last_name=values.get("last_name", ""),
            middle_name=values.get("middle_name", ""),
            birth_date=_date(birth) if birth else None,
            company_name=values.get("company_name", ""),
            address=values.get("address", ""),
            comment=values.get("comment", ""),
            external_id=values.get("external_id", ""),
        )
        card = services.create_client(
            ctx, company, data, self._contacts(values), possible_duplicate=duplicate
        )
        return card.id


class CounterpartiesTemplate(PartiesTemplate):
    kind = "counterparties"
    sheet = "counterparties"
    permission = "counterparty.create"
    object_type = "counterparty"
    model = Counterparty
    contact_model = CounterpartyContact
    contact_card = "counterparty"
    columns = {
        **NAME_COLUMNS,
        "company_name": 200,
        **CONTACTS,
        "registration": 500,
        "address": 500,
        "category": 100,
        "comment": 2000,
        "external_id": 100,
        "c4rsys_id": 40,
    }
    help = {
        **HELP_COMMON,
        "registration": (
            "Registration and tax details",
            "Регистрационные и налоговые сведения",
            "",
        ),
        "category": ("Category of the counterparty", "Категория контрагента", "Запчасти"),
    }

    def create(
        self,
        ctx: CommandContext,
        company: Company,
        values: dict[str, str],
        extra: dict[str, Any],
        *,
        duplicate: bool,
        operation_id: UUID,
        session: dict[str, Any],
    ) -> UUID:
        data = counterparties.CounterpartyData(
            kind=values["kind"].strip().lower(),
            first_name=values.get("first_name", ""),
            last_name=values.get("last_name", ""),
            middle_name=values.get("middle_name", ""),
            company_name=values.get("company_name", ""),
            comment=values.get("comment", ""),
            registration=values.get("registration", ""),
            address=values.get("address", ""),
            category=values.get("category", ""),
            external_id=values.get("external_id", ""),
        )
        card = counterparties.create(
            ctx, company, data, self._contacts(values), possible_duplicate=duplicate
        )
        return card.id


CLIENTS = ClientsTemplate()
COUNTERPARTIES = CounterpartiesTemplate()
