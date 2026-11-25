"""Declared owner of a vehicle (ТЗ § 8.1.6, § 8.3.13).

With «own» the company is the declared owner. With «managed» the owner is a profile looked up by
c4rsys ID, email or phone, an owner described by hand (person or organization), or not given.
A company profile is given by its CMP ID. Everything stays unconfirmed until the owner answers
the request of § 8.3.1 (`owner_requests`). The company never learns from a lookup whether a
profile exists.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from c4rsys.accounts import services as accounts
from c4rsys.companies.models import Company
from c4rsys.fleet import owner_requests
from c4rsys.fleet.models import VehicleCard, VehicleOwnerClaim
from c4rsys.parties import services as parties
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

if TYPE_CHECKING:
    from c4rsys.fleet.listing import Batch

PHONE = re.compile(r"^\+?[0-9 ()\-]{5,40}$")


@dataclass(frozen=True)
class Owner:
    kind: str = VehicleOwnerClaim.Kind.COMPANY
    lookup: str = ""
    name: str = ""
    contact: str = ""
    client_id: UUID | None = None
    # In one line (customer's decision of 23.11.2026).
    address: str = ""
    # A person's name apart (customer's remark of 25.11.2026); «name» is them joined.
    last_name: str = ""
    first_name: str = ""
    middle_name: str = ""

    def payload(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "lookup": self.lookup,
            "name": self.name,
            "last_name": self.last_name,
            "first_name": self.first_name,
            "middle_name": self.middle_name,
            "contact": self.contact,
            "address": self.address,
            "client_id": str(self.client_id) if self.client_id else "",
        }


def _text(data: dict[str, Any], key: str, max_length: int) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise errors.validation_error(key)
    value = value.strip()
    if len(value) > max_length:
        raise errors.validation_error(key, code="FIELD_TOO_LONG", max_length=max_length)
    return value


@dataclass(frozen=True)
class Named:
    name: str
    last_name: str
    first_name: str
    middle_name: str


def is_company_lookup(lookup: str) -> bool:
    return lookup.upper().startswith("CMP-")


def _named(data: dict[str, Any], kind: str, *, required: bool, company: bool) -> "Named":
    """The name of the owner: a company or an organization by its name, a person by the last
    name, the first name and the middle name apart (customer's remark of 25.11.2026). The joined
    name alone is still taken: the bot and the import give it so."""
    parts = {
        "last_name": "" if company else _text(data, "owner_last_name", 100),
        "first_name": "" if company else _text(data, "owner_first_name", 100),
        "middle_name": "" if company else _text(data, "owner_middle_name", 100),
    }
    joined = " ".join(v for v in parts.values() if v)
    name = joined or _text(data, "owner_name", 300)
    if required and not name:
        field = "owner_name" if company else "owner_last_name"
        if kind == VehicleOwnerClaim.Kind.PERSON and "owner_name" in data:
            field = "owner_name"
        raise errors.validation_error(field, code="FIELD_REQUIRED")
    return Named(name, parts["last_name"], parts["first_name"], parts["middle_name"])


def parse(data: dict[str, Any], relation: str) -> Owner:
    if relation == VehicleCard.Relation.OWN:
        return Owner()
    if relation == VehicleCard.Relation.CLIENT:
        # The owner of a client vehicle is optional: a client of the company, a person or an
        # organization described by hand without a profile, or not given (decision of
        # 24.10.2026).
        kind = _text(data, "owner_kind", 20) or (
            VehicleOwnerClaim.Kind.CLIENT
            if _text(data, "owner_client_id", 40)
            else VehicleOwnerClaim.Kind.NONE
        )
        if kind == VehicleOwnerClaim.Kind.CLIENT:
            value = _text(data, "owner_client_id", 40)
            if not value:
                raise errors.validation_error("owner_client_id", code="FIELD_REQUIRED")
            try:
                return Owner(kind=kind, client_id=UUID(value))
            except ValueError as exc:
                raise errors.validation_error("owner_client_id") from exc
        if kind not in (
            VehicleOwnerClaim.Kind.PERSON,
            VehicleOwnerClaim.Kind.ORGANIZATION,
            VehicleOwnerClaim.Kind.NONE,
        ):
            raise errors.validation_error("owner_kind")
    kind = _text(data, "owner_kind", 20) or VehicleOwnerClaim.Kind.NONE
    allowed = {
        VehicleOwnerClaim.Kind.PROFILE,
        VehicleOwnerClaim.Kind.PERSON,
        VehicleOwnerClaim.Kind.ORGANIZATION,
        VehicleOwnerClaim.Kind.NONE,
    }
    if kind not in allowed:
        raise errors.validation_error("owner_kind")
    if kind == VehicleOwnerClaim.Kind.PROFILE:
        lookup = _text(data, "owner_lookup", 254)
        if not lookup:
            raise errors.validation_error("owner_lookup", code="FIELD_REQUIRED")
        if "@" in lookup:
            lookup = accounts.normalize_email(lookup)
        elif lookup.upper().startswith(("USR-", "CMP-")):
            lookup = lookup.upper()
        elif not PHONE.match(lookup):
            raise errors.validation_error("owner_lookup", code="OWNER_LOOKUP_INVALID")
        # The profile does not replace the name: the name, the contact and the address are kept
        # with it for the contracts and the reports (customer's remark of 25.11.2026).
        n = _named(data, kind, required=False, company=is_company_lookup(lookup))
        return Owner(
            kind=kind,
            lookup=lookup,
            name=n.name,
            last_name=n.last_name,
            first_name=n.first_name,
            middle_name=n.middle_name,
            contact=_text(data, "owner_contact", 254),
            address=_text(data, "owner_address", 500),
        )
    if kind in (VehicleOwnerClaim.Kind.PERSON, VehicleOwnerClaim.Kind.ORGANIZATION):
        company = kind == VehicleOwnerClaim.Kind.ORGANIZATION
        n = _named(data, kind, required=True, company=company)
        return Owner(
            kind=kind,
            name=n.name,
            last_name=n.last_name,
            first_name=n.first_name,
            middle_name=n.middle_name,
            contact=_text(data, "owner_contact", 254),
            address=_text(data, "owner_address", 500),
        )
    return Owner(kind=kind)


def _resolve(lookup: str) -> Any:
    """A profile for the lookup, or None. Login by phone is not available yet, so a phone finds
    no profile for now; it is kept for the request."""
    if lookup.startswith("USR-"):
        return accounts.find_active_user(lookup)
    if "@" in lookup:
        return accounts.find_user_by_email(lookup)
    return None


def declare(ctx: CommandContext, company: Company, card: VehicleCard, owner: Owner) -> None:
    user = _resolve(owner.lookup) if owner.kind == VehicleOwnerClaim.Kind.PROFILE else None
    client = None
    if owner.client_id is not None:
        client = parties.clients_of(company.id).filter(pk=owner.client_id).first()
        if client is None:
            raise errors.validation_error("owner_client_id", code="CLIENT_UNKNOWN")
    claim = VehicleOwnerClaim.objects.create(
        company=company,
        vehicle_card=card,
        kind=owner.kind,
        lookup=owner.lookup,
        owner_user_id=user.id if user else None,
        client=client,
        manual_name=owner.name,
        manual_last_name=owner.last_name,
        manual_first_name=owner.first_name,
        manual_middle_name=owner.middle_name,
        manual_contact=owner.contact,
        manual_address=owner.address,
        created_by_user_id=ctx.actor.user_id,
    )
    # The request to the previous owner is not answered any more (§ 8.3.13).
    owner_requests.close_for_new_owner(ctx, card)
    ctx.audit(
        "vehicle.owner_declared",
        object_type="vehicle_card",
        object_id=card.id,
        # The resolved profile is not journaled for the company: it is not to learn it.
        after={**owner.payload(), "claim": str(claim.id)},
    )


def is_current(card: VehicleCard, owner: Owner) -> bool:
    """Whether the latest declaration already says the same."""
    claim = card.owner_claims.order_by("-created_at").first()
    return claim is not None and (
        claim.kind,
        claim.lookup,
        claim.manual_name,
        claim.manual_last_name,
        claim.manual_first_name,
        claim.manual_middle_name,
        claim.manual_contact,
        claim.client_id,
        claim.manual_address,
    ) == (
        owner.kind,
        owner.lookup,
        owner.name,
        owner.last_name,
        owner.first_name,
        owner.middle_name,
        owner.contact,
        owner.client_id,
        owner.address,
    )


def describe(card: VehicleCard, batch: "Batch | None" = None) -> dict[str, Any] | None:
    claim = (
        batch.claims.get(card.id)
        if batch is not None
        else card.owner_claims.order_by("-created_at").first()
    )
    if claim is None:
        return None
    body: dict[str, Any] = {
        "kind": claim.kind,
        "lookup": claim.lookup,
        "name": claim.manual_name,
        "last_name": claim.manual_last_name,
        "first_name": claim.manual_first_name,
        "middle_name": claim.manual_middle_name,
        "contact": claim.manual_contact,
        "address": claim.manual_address,
        # «ownership_change»: declared by the system after the ownership passed (§ 8.3.10).
        "source": claim.source,
        # The answers to the request of § 8.3.1: three «yes» confirm the management.
        **owner_requests.owner_status(card, batch),
    }
    if claim.client is not None:
        body["client"] = {"id": str(claim.client_id), "name": parties.display_name(claim.client)}
    if claim.kind == VehicleOwnerClaim.Kind.COMPANY:
        company = card.company
        body["company"] = {
            "public_id": company.public_id,
            "display_name": company.display_name,
            "legal_name": company.legal_name,
        }
    return body


def vehicles_of_clients(company_id: UUID, client_ids: list[UUID]) -> list[UUID]:
    """§ 8.1.2: the client vehicles whose latest declared owner is one of the client cards."""
    found = []
    claims = VehicleOwnerClaim.objects.filter(company_id=company_id, client_id__in=client_ids)
    for card_id in set(claims.values_list("vehicle_card_id", flat=True)):
        latest = (
            VehicleOwnerClaim.objects.filter(company_id=company_id, vehicle_card_id=card_id)
            .order_by("-created_at")
            .first()
        )
        if latest is not None and latest.client_id in client_ids:
            found.append(card_id)
    return found
