"""Declared owner of a vehicle (ТЗ § 8.1.6, § 8.3.13).

With «own» the company is the declared owner. With «managed» the owner is a profile looked up by
c4rsys ID, email or phone, an owner described by hand (person or organization), or not given.
A company profile is given by its CMP ID. Everything stays unconfirmed until the owner answers
the request of § 8.3.1 (`owner_requests`). The company never learns from a lookup whether a
profile exists.
"""

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from c4rsys.accounts import services as accounts
from c4rsys.companies.models import Company
from c4rsys.fleet import owner_requests
from c4rsys.fleet.models import VehicleCard, VehicleOwnerClaim
from c4rsys.parties import services as parties
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

PHONE = re.compile(r"^\+?[0-9 ()\-]{5,40}$")


@dataclass(frozen=True)
class Owner:
    kind: str = VehicleOwnerClaim.Kind.COMPANY
    lookup: str = ""
    name: str = ""
    contact: str = ""
    client_id: UUID | None = None

    def payload(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "lookup": self.lookup,
            "name": self.name,
            "contact": self.contact,
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
        return Owner(kind=kind, lookup=lookup)
    if kind in (VehicleOwnerClaim.Kind.PERSON, VehicleOwnerClaim.Kind.ORGANIZATION):
        name = _text(data, "owner_name", 300)
        if not name:
            raise errors.validation_error("owner_name", code="FIELD_REQUIRED")
        return Owner(kind=kind, name=name, contact=_text(data, "owner_contact", 254))
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
        manual_contact=owner.contact,
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
        claim.manual_contact,
        claim.client_id,
    ) == (owner.kind, owner.lookup, owner.name, owner.contact, owner.client_id)


def describe(card: VehicleCard) -> dict[str, Any] | None:
    claim = card.owner_claims.order_by("-created_at").first()
    if claim is None:
        return None
    body: dict[str, Any] = {
        "kind": claim.kind,
        "lookup": claim.lookup,
        "name": claim.manual_name,
        "contact": claim.manual_contact,
        # The answers to the request of § 8.3.1: three «yes» confirm the management.
        **owner_requests.owner_status(card),
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
