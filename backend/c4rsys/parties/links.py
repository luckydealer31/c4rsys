"""Linking a card of a client or a counterparty to a c4rsys profile (ТЗ § 6.2.4–6.2.6, § 6.3,
§ 6.4.1–6.4.2, § 6.6.2–6.6.6, § 6.1.13, § 20.3.4; decisions Б-4, Б-5).

* The company sends the request from an existing card by a USR ID, a confirmed email or phone of
  a person, or by the CMP ID of a company. The answer does not tell whether such a profile exists
  (§ 6.2.5, § 6.3.1). An email without a profile gets a link to register; after registering with
  it the person sees the request of this card without a new search (§ 6.2.6).
* One open request or link per card. Without automatic reminders: a new request may be sent by
  hand after a decline, an expiry or a cancel (Б-5). A request is valid 30 days (Б-5).
* The person answers in the personal area; a company — its top-authority director or an employee
  with the right (§ 6.3.8). Accepting gives the chosen data; they are kept as a snapshot and do
  not change the working fields of the card by themselves (§ 6.4.1–6.4.2).
* Breaking the link is done by the company; the history given before stays with the client
  (§ 6.6.2–6.6.3). A new request after it links the same card (§ 6.6.6).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from c4rsys.accounts import mail
from c4rsys.accounts import services as accounts
from c4rsys.accounts.models import UserAccount
from c4rsys.companies import services as companies
from c4rsys.companies.models import Company
from c4rsys.parties import counterparties, services
from c4rsys.parties.models import ClientCard, Counterparty, ProfileLink
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.ids import normalize_public_id

TTL = timedelta(days=30)
CARD_TYPES = ("client", "counterparty")
Profile = ProfileLink.Profile
State = ProfileLink.State
OPEN = (State.PENDING, State.ACCEPTED)

# The data a recipient may give (§ 6.2.5); each is chosen separately when accepting (PROPOSAL).
PERSON_FIELDS = (
    "first_name",
    "last_name",
    "middle_name",
    "birth_date",
    "email",
    "phone",
    "document",
)
COMPANY_FIELDS = (
    "display_name",
    "legal_name",
    "country",
    "legal_form",
    "identifiers",
    "legal_address",
    "actual_address",
    "phone",
    "email",
)

Card = ClientCard | Counterparty


def effective_state(link: ProfileLink, now: datetime) -> str:
    if link.state == State.PENDING and link.expires_at <= now:
        return State.EXPIRED
    return link.state


# --- Target (§ 6.2.5, § 6.3.1) ----------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    profile: str
    label: str
    user: UserAccount | None = None
    company: Company | None = None
    email: str = ""
    phone: str = ""


def resolve_target(card_kind: str, value: str) -> Target:
    """A person card is linked to a person by USR ID, email or phone; a company card to a company
    by its CMP ID: a company profile has no confirmed contacts to address it (PROPOSAL)."""
    text = value.strip()
    if not text:
        raise errors.validation_error("target", code="FIELD_REQUIRED")
    public_id = normalize_public_id(text)
    if card_kind == ClientCard.Kind.COMPANY:
        company = (
            companies.find_company(public_id)
            if public_id is not None and public_id.startswith("CMP-")
            else None
        )
        if public_id is None or not public_id.startswith("CMP-"):
            raise errors.business_rule("LINK_COMPANY_BY_ID", field="target")
        if company is None:
            raise errors.business_rule("LINK_TARGET_NOT_FOUND", field="target")
        return Target(Profile.COMPANY, public_id, company=company)
    if public_id is not None:
        user = accounts.find_active_user(public_id) if public_id.startswith("USR-") else None
        if user is None:
            raise errors.business_rule("LINK_TARGET_NOT_FOUND", field="target")
        return Target(Profile.PERSON, public_id, user=user)
    if "@" in text:
        email = services.normalize_contact("email", text, "target").normalized
        return Target(Profile.PERSON, email, user=accounts.find_user_by_email(email), email=email)
    phone = services.normalize_contact("phone", text, "target").normalized
    return Target(Profile.PERSON, phone, user=accounts.find_user_by_phone(phone), phone=phone)


# --- Cards ------------------------------------------------------------------------------------


def card_model(card_type: str) -> type[ClientCard] | type[Counterparty]:
    if card_type not in CARD_TYPES:
        raise errors.validation_error("card_type")
    return ClientCard if card_type == "client" else Counterparty


def _lock_card(company_id: UUID, card_type: str, card_id: UUID) -> Card:
    card = (
        card_model(card_type).objects.select_for_update().filter(company_id=company_id, pk=card_id)
    ).first()
    if card is None:
        raise errors.not_found()
    return card


def card_type_of(link: ProfileLink) -> str:
    return "client" if link.client_id is not None else "counterparty"


def card_id_of(link: ProfileLink) -> UUID:
    found = link.client_id or link.counterparty_id
    if found is None:  # excluded by the constraint profile_link_one_card
        raise RuntimeError(f"Profile link {link.id} has no card")
    return found


def _card_filter(card_type: str, card_id: UUID) -> Q:
    return Q(client_id=card_id) if card_type == "client" else Q(counterparty_id=card_id)


def _one_per_profile(exc: IntegrityError) -> bool:
    """The constraint of one card of the list per profile in a company (migration 0009)."""
    text = str(exc)
    return any(
        f"{table}_one_per_{profile}" in text
        for table in ("client_card", "counterparty")
        for profile in ("user", "company")
    )


def _taken(
    company_id: UUID, card_type: str, card_id: UUID, user_id: UUID | None, company: UUID | None
) -> bool:
    """Another card of the same list of the company is already linked to the profile."""
    others = card_model(card_type).objects.filter(company_id=company_id).exclude(pk=card_id)
    if user_id is not None:
        return others.filter(linked_user_id=user_id).exists()
    return company is not None and others.filter(linked_company_id=company).exists()


def links_of_card(company_id: UUID, card_type: str, card_id: UUID) -> QuerySet[ProfileLink]:
    return ProfileLink.objects.filter(
        _card_filter(card_type, card_id), company_id=company_id
    ).order_by("-created_at")


# --- Request by the company -------------------------------------------------------------------


def request_link(
    ctx: CommandContext, company: Company, card_type: str, card_id: UUID, target: Target
) -> ProfileLink:
    now = timezone.now()
    card = _lock_card(company.id, card_type, card_id)
    if card.state != ClientCard.State.ACTIVE:
        raise errors.business_rule("CARD_ARCHIVED")
    if card.linked_user_id is not None or card.linked_company_id is not None:
        raise errors.business_rule("LINK_ALREADY_EXISTS")
    expected = Profile.COMPANY if card.kind == ClientCard.Kind.COMPANY else Profile.PERSON
    if target.profile != expected:
        raise errors.business_rule("LINK_PROFILE_KIND", field="target")
    if target.company is not None and target.company.id == company.id:
        raise errors.business_rule("LINK_SELF", field="target")
    pending = (
        ProfileLink.objects.select_for_update()
        .filter(_card_filter(card_type, card_id), company=company, state=State.PENDING)
        .first()
    )
    if pending is not None:
        if effective_state(pending, now) != State.EXPIRED:
            raise errors.business_rule("LINK_REQUEST_PENDING")
        pending.state = State.EXPIRED
        pending.save(update_fields=["state"])
    if _taken(
        company.id,
        card_type,
        card_id,
        target.user.id if target.user else None,
        target.company.id if target.company else None,
    ):
        raise errors.business_rule("LINK_PROFILE_TAKEN", field="target")
    link = ProfileLink.objects.create(
        company=company,
        client_id=card_id if card_type == "client" else None,
        counterparty_id=card_id if card_type == "counterparty" else None,
        profile=target.profile,
        target_label=target.label,
        target_user_id=target.user.id
        if target.user and not target.email and not target.phone
        else None,
        target_company_id=target.company.id if target.company else None,
        target_email=target.email,
        target_phone=target.phone,
        created_by_user_id=ctx.actor.user_id,
        created_at=now,
        expires_at=now + TTL,
    )
    ctx.audit(
        "profile_link.requested",
        object_type="profile_link",
        object_id=link.id,
        after={
            "card_type": card_type,
            "card_id": str(card_id),
            "profile": link.profile,
            "target": link.target_label,
            "expires_at": link.expires_at,
        },
    )
    if target.email:
        email, name = target.email, company.display_name
        transaction.on_commit(lambda: mail.send_link_request(email, name))
    return link


def cancel(ctx: CommandContext, company_id: UUID, link_id: UUID) -> ProfileLink:
    """The company withdraws a request that is still pending."""
    link = ProfileLink.objects.select_for_update().filter(company_id=company_id, pk=link_id).first()
    if link is None:
        raise errors.not_found()
    if effective_state(link, timezone.now()) != State.PENDING:
        raise errors.business_rule("LINK_NOT_PENDING")
    link.state = State.CANCELLED
    link.decided_at = timezone.now()
    link.save(update_fields=["state", "decided_at"])
    ctx.audit("profile_link.cancelled", object_type="profile_link", object_id=link.id)
    return link


def break_link(ctx: CommandContext, company_id: UUID, link_id: UUID, initiator: str) -> ProfileLink:
    """By the company's initiative or after the client asked (§ 6.6.2); the initiator and the time
    are recorded (§ 6.6.6). Data and history of the card stay (§ 6.6.1)."""
    if initiator not in (ProfileLink.Initiator.COMPANY, ProfileLink.Initiator.CLIENT):
        raise errors.validation_error("initiator")
    query = ProfileLink.objects.filter(company_id=company_id)
    peek = query.filter(pk=link_id).first()
    if peek is None:
        raise errors.not_found()
    # The card is locked before its link, as in request_link and the answer.
    card = _lock_card(company_id, card_type_of(peek), card_id_of(peek))
    link = query.select_for_update().get(pk=link_id)
    if link.state != State.ACCEPTED:
        raise errors.business_rule("LINK_NOT_ACCEPTED")
    now = timezone.now()
    card.linked_user_id = None
    card.linked_company_id = None
    card.version += 1
    card.save(update_fields=["linked_user_id", "linked_company_id", "version", "updated_at"])
    link.state = State.BROKEN
    link.broken_at = now
    link.broken_by_user_id = ctx.actor.user_id
    link.broken_initiator = initiator
    link.save(update_fields=["state", "broken_at", "broken_by_user_id", "broken_initiator"])
    ctx.audit(
        "profile_link.broken",
        object_type="profile_link",
        object_id=link.id,
        after={"initiator": initiator},
    )
    return link


def find(link_id: UUID) -> ProfileLink | None:
    """A link for the system verification (§ 24.1.7), whatever the company."""
    return ProfileLink.objects.select_related("company").filter(pk=link_id).first()


def break_by_platform(ctx: CommandContext, link_id: UUID, reason: str) -> ProfileLink:
    """The system verification ends a disputed link after its check (§ 24.1.7): the same effect
    as § 6.6.2, with the initiator «platform»; the company sees it in its journal. Data and history
    of the card stay (§ 6.6.1); the access already given is not revoked here (§ 6.5.5 is the
    company's correction)."""
    peek = find(link_id)
    if peek is None:
        raise errors.not_found()
    card = _lock_card(peek.company_id, card_type_of(peek), card_id_of(peek))
    link = ProfileLink.objects.select_for_update().get(pk=link_id)
    if link.state not in (State.ACCEPTED, State.PENDING):
        raise errors.business_rule("LINK_NOT_OPEN")
    now = timezone.now()
    before = link.state
    if link.state == State.ACCEPTED:
        card.linked_user_id = None
        card.linked_company_id = None
        card.version += 1
        card.save(update_fields=["linked_user_id", "linked_company_id", "version", "updated_at"])
        link.state = State.BROKEN
        link.broken_at = now
        link.broken_by_user_id = ctx.actor.user_id
        link.broken_initiator = ProfileLink.Initiator.PLATFORM
        link.save(update_fields=["state", "broken_at", "broken_by_user_id", "broken_initiator"])
    else:
        link.state = State.CANCELLED
        link.decided_at = now
        link.save(update_fields=["state", "decided_at"])
    ctx.audit(
        "profile_link.broken" if before == State.ACCEPTED else "profile_link.cancelled",
        object_type="profile_link",
        object_id=link.id,
        before={"state": before},
        after={"state": link.state, "initiator": "platform"},
        reason=reason,
        company_id=link.company_id,
    )
    return link


def verification_payload(link: ProfileLink) -> dict[str, Any]:
    """What the verification sees of a link: the company, the kind of card and the profile, not
    the card's data."""
    target = None
    if link.target_user_id is not None:
        user = accounts.get_user(link.target_user_id)
        target = {"type": "user", "public_id": user.public_id if user else ""}
    elif link.target_company_id is not None:
        company = Company.objects.filter(pk=link.target_company_id).first()
        target = {"type": "company", "public_id": company.public_id if company else ""}
    return {
        "id": str(link.id),
        "company": {"public_id": link.company.public_id, "name": link.company.display_name},
        "card_type": card_type_of(link),
        "profile": link.profile,
        "target": target,
        "state": link.state,
        "created_at": link.created_at.isoformat(),
        "decided_at": link.decided_at.isoformat() if link.decided_at else None,
        "broken_at": link.broken_at.isoformat() if link.broken_at else None,
        "broken_initiator": link.broken_initiator or None,
    }


# --- The recipient's answer (§ 6.2.5, § 6.3.8) ------------------------------------------------


def for_user(user: UserAccount) -> QuerySet[ProfileLink]:
    """Requests to the person by ID or by any of their confirmed emails and phones."""
    return ProfileLink.objects.filter(profile=Profile.PERSON).filter(
        Q(target_user_id=user.id)
        | Q(target_email__in=accounts.confirmed_emails(user))
        | Q(target_phone__in=accounts.confirmed_phones(user))
    )


def links_of_user(user_id: UUID) -> QuerySet[ProfileLink]:
    """Confirmed and broken links of a person: the companies of the personal cabinet."""
    return (
        ProfileLink.objects.filter(
            profile=Profile.PERSON,
            target_user_id=user_id,
            state__in=(State.ACCEPTED, State.BROKEN),
        )
        .select_related("company")
        .order_by("-decided_at")
    )


def client_cards_of_user(user_id: UUID) -> list[tuple[UUID, UUID, datetime | None]]:
    """(company, client card, end of the shared history) of a person. A confirmed link shares
    the whole history of the card; after a break only what was shared before stays (§ 6.3.6,
    § 6.6.3)."""
    return [
        (link.company_id, link.client_id, link.broken_at)
        for link in links_of_user(user_id).filter(client__isnull=False)
        if link.client_id is not None
    ]


def for_company(company_id: UUID) -> QuerySet[ProfileLink]:
    return ProfileLink.objects.filter(profile=Profile.COMPANY, target_company_id=company_id)


def _locked(query: QuerySet[ProfileLink], link_id: UUID) -> ProfileLink:
    link = query.select_for_update().filter(pk=link_id).first()
    if link is None:
        raise errors.not_found()
    state = effective_state(link, timezone.now())
    if state == State.EXPIRED:
        raise errors.business_rule("LINK_EXPIRED")
    if state != State.PENDING:
        raise errors.business_rule("LINK_NOT_PENDING")
    return link


def _chosen(
    available: dict[str, Any], fields: list[str], allowed: tuple[str, ...]
) -> dict[str, Any]:
    unknown = [f for f in fields if f not in allowed]
    if unknown:
        raise errors.validation_error("fields", code="VALUE_UNKNOWN")
    return {f: available[f] for f in allowed if f in fields and available.get(f) not in ("", None)}


def _locked_with_card(query: QuerySet[ProfileLink], link_id: UUID) -> tuple[ProfileLink, Card]:
    """The card first, then its link: the same order as request_link and break_link, so that
    concurrent commands on one card do not deadlock."""
    peek = query.filter(pk=link_id).first()
    if peek is None:
        raise errors.not_found()
    card = _lock_card(peek.company_id, card_type_of(peek), card_id_of(peek))
    return _locked(query, link_id), card


def accept_as_user(
    ctx: CommandContext, user: UserAccount, link_id: UUID, fields: list[str]
) -> ProfileLink:
    link, card = _locked_with_card(for_user(user), link_id)
    data = _chosen(accounts.shareable_profile(user), fields, PERSON_FIELDS)
    return _accept(ctx, link, card, data, user_id=user.id, company_id=None)


def accept_as_company(
    ctx: CommandContext, company: Company, link_id: UUID, fields: list[str]
) -> ProfileLink:
    link, card = _locked_with_card(for_company(company.id), link_id)
    data = _chosen(companies.shareable_profile(company), fields, COMPANY_FIELDS)
    _answered(ctx, link, company.id, accepted=True)
    return _accept(ctx, link, card, data, user_id=None, company_id=company.id)


def _accept(
    ctx: CommandContext,
    link: ProfileLink,
    card: Card,
    data: dict[str, Any],
    *,
    user_id: UUID | None,
    company_id: UUID | None,
) -> ProfileLink:
    """The same card is linked and the given data are kept as its snapshot (§ 6.2.11). The card
    is checked again on fresh data: it may have been archived after the request was sent."""
    card_type = card_type_of(link)
    if card.state != ClientCard.State.ACTIVE:
        raise errors.business_rule("CARD_ARCHIVED")
    if _taken(link.company_id, card_type, card.id, user_id, company_id):
        raise errors.business_rule("LINK_PROFILE_TAKEN")
    now = timezone.now()
    card.linked_user_id = user_id
    card.linked_company_id = company_id
    card.version += 1
    try:
        with transaction.atomic():
            card.save(
                update_fields=["linked_user_id", "linked_company_id", "version", "updated_at"]
            )
    except IntegrityError as exc:
        # Another card was linked to the profile at the same moment.
        if _one_per_profile(exc):
            raise errors.business_rule("LINK_PROFILE_TAKEN") from exc
        raise
    link.state = State.ACCEPTED
    link.target_user_id = user_id or link.target_user_id
    link.decided_at = now
    link.decided_by_user_id = ctx.actor.user_id
    link.shared = {"data": data, "at": now.isoformat()}
    link.save(
        update_fields=["state", "target_user_id", "decided_at", "decided_by_user_id", "shared"]
    )
    # Journaled in the requesting company: its card changed (§ 20.3.4: the answer is told there).
    ctx.audit(
        "profile_link.accepted",
        object_type="profile_link",
        object_id=link.id,
        after={"card_type": card_type, "card_id": str(card.id), "fields": sorted(data)},
        company_id=link.company_id,
    )
    return link


def decline_as_user(ctx: CommandContext, user: UserAccount, link_id: UUID) -> ProfileLink:
    return _decline(ctx, _locked(for_user(user), link_id))


def decline_as_company(ctx: CommandContext, company: Company, link_id: UUID) -> ProfileLink:
    link = _locked(for_company(company.id), link_id)
    _answered(ctx, link, company.id, accepted=False)
    return _decline(ctx, link)


def _decline(ctx: CommandContext, link: ProfileLink) -> ProfileLink:
    """Nothing of the profile is loaded; the local card and its history stay (§ 6.2.11)."""
    link.state = State.DECLINED
    link.decided_at = timezone.now()
    link.decided_by_user_id = ctx.actor.user_id
    link.save(update_fields=["state", "decided_at", "decided_by_user_id"])
    ctx.audit(
        "profile_link.declined",
        object_type="profile_link",
        object_id=link.id,
        company_id=link.company_id,
    )
    return link


def _answered(ctx: CommandContext, link: ProfileLink, company_id: UUID, *, accepted: bool) -> None:
    """The answer of a company is journaled in that company as well."""
    ctx.audit(
        "profile_link.answered",
        object_type="profile_link",
        object_id=link.id,
        after={"accepted": accepted, "requester": str(link.company_id)},
        company_id=company_id,
    )


# --- A vehicle owner who confirmed the link (§ 8.3.1) ------------------------------------------


def _owner_contacts(values: list[tuple[str, str]]) -> list[services.Contact]:
    found: list[services.Contact] = []
    for kind, value in values:
        if not value:
            continue
        try:
            found.append(services.normalize_contact(kind, value))
        except errors.ApiError:
            continue  # a free-text contact of a company profile that is not valid is left out
    return found


def client_from_owner(
    ctx: CommandContext,
    requester: Company,
    *,
    owner_user: UserAccount | None,
    owner_company: Company | None,
    requested_by: UUID,
) -> ClientCard:
    """«Yes» to the link of § 8.3.1 makes the owner a client of the company (customer's decision
    of 30.10.2026). A client card already linked to the profile is kept. A card whose link to the
    profile was broken is linked again: after a new request is accepted the previous card is
    linked (§ 6.6.6). Otherwise a new card is created and linked at once. It holds the minimum of
    § 6.2.7–6.2.8 taken from the profile: names (or the company name) and the confirmed email
    (PROPOSAL). A card with the same contact is not merged; the new card is marked as a possible
    duplicate (§ 6.5.1)."""
    user_id = owner_user.id if owner_user is not None else None
    company_id = owner_company.id if owner_company is not None else None
    by_profile = (
        Q(linked_user_id=user_id) if user_id is not None else Q(linked_company_id=company_id)
    )
    linked = ClientCard.objects.select_for_update().filter(by_profile, company=requester)
    existing = linked.first()
    if existing is not None:
        # TODO(spec): an archived card stays linked (§ 6.6.5) and is returned as it is; whether
        # the owner's «yes» restores it from the archive is not decided («можно восстановить»).
        return existing
    now = timezone.now()
    if owner_user is not None:
        data = accounts.shareable_profile(owner_user)
        shared = {
            k: data[k] for k in ("first_name", "last_name", "middle_name", "email") if data[k]
        }
        fields: dict[str, Any] = {
            "kind": ClientCard.Kind.PERSON,
            "first_name": data["first_name"],
            "last_name": data["last_name"],
            "middle_name": data["middle_name"],
        }
        contacts = _owner_contacts([("email", data["email"])])
        profile, label = Profile.PERSON, owner_user.public_id
    elif owner_company is not None:
        data = companies.shareable_profile(owner_company)
        shared = {k: data[k] for k in ("display_name", "email", "phone") if data[k]}
        fields = {"kind": ClientCard.Kind.COMPANY, "company_name": data["display_name"]}
        contacts = _owner_contacts([("email", data["email"]), ("phone", data["phone"])])
        profile, label = Profile.COMPANY, owner_company.public_id
    else:
        raise RuntimeError("An owner is a person or a company")

    def accepted_link(card: ClientCard) -> ProfileLink:
        return ProfileLink.objects.create(
            company=requester,
            client=card,
            profile=profile,
            target_label=label,
            target_user_id=user_id,
            target_company_id=company_id,
            state=State.ACCEPTED,
            created_by_user_id=requested_by,
            created_at=now,
            expires_at=now,
            decided_at=now,
            decided_by_user_id=ctx.actor.user_id,
            shared={"data": shared, "at": now.isoformat(), "source": "vehicle_owner"},
        )

    previous = _previous_card(requester.id, user_id, company_id, now)
    if previous is not None:
        previous.linked_user_id = user_id
        previous.linked_company_id = company_id
        previous.version += 1
        try:
            with transaction.atomic():
                previous.save(
                    update_fields=["linked_user_id", "linked_company_id", "version", "updated_at"]
                )
                link = accepted_link(previous)
        except IntegrityError as exc:
            return _linked_meanwhile(linked, exc)
        ctx.audit(
            "profile_link.accepted",
            object_type="profile_link",
            object_id=link.id,
            after={
                "card_type": "client",
                "card_id": str(previous.id),
                "fields": sorted(shared),
                "source": "vehicle_owner",
            },
            company_id=requester.id,
        )
        return previous
    if owner_user is not None and (not fields["first_name"] or not fields["last_name"]):
        raise errors.business_rule("NAME_REQUIRED")
    duplicates = any(
        services.matches(
            requester.id,
            services.MatchQuery(
                first_name=fields.get("first_name", ""),
                last_name=fields.get("last_name", ""),
                birth_date=None,
                company_name=fields.get("company_name", ""),
                contacts=tuple(contacts),
            ),
        )
    )
    try:
        with transaction.atomic():
            card = ClientCard.objects.create(
                company=requester,
                **fields,
                possible_duplicate=duplicates,
                linked_user_id=user_id,
                linked_company_id=company_id,
                created_by_user_id=ctx.actor.user_id,
                created_at=now,
            )
            services.write_contacts(card, contacts)
            link = accepted_link(card)
    except IntegrityError as exc:
        return _linked_meanwhile(linked, exc)
    ctx.audit(
        "client.created",
        object_type="client_card",
        object_id=card.id,
        after={**services.snapshot(card), "source": "vehicle_owner", "link": str(link.id)},
        company_id=requester.id,
    )
    return card


def _previous_card(
    company_id: UUID, user_id: UUID | None, profile_company_id: UUID | None, now: datetime
) -> ClientCard | None:
    """The client card of the latest broken link to the profile that is free to be linked again
    (§ 6.6.6). An expired request of that card is closed, as request_link does."""
    broken = ProfileLink.objects.filter(
        company_id=company_id, client__isnull=False, state=State.BROKEN
    )
    broken = (
        broken.filter(profile=Profile.PERSON, target_user_id=user_id)
        if user_id is not None
        else broken.filter(profile=Profile.COMPANY, target_company_id=profile_company_id)
    )
    seen: set[UUID] = set()
    for link in broken.order_by("-broken_at"):
        if link.client_id is None or link.client_id in seen:
            continue
        seen.add(link.client_id)
        card = _lock_card(company_id, "client", link.client_id)
        if not isinstance(card, ClientCard):  # the client list only
            continue
        if card.linked_user_id is not None or card.linked_company_id is not None:
            continue  # linked to another profile since
        pending = (
            ProfileLink.objects.select_for_update()
            .filter(client_id=card.id, company_id=company_id, state=State.PENDING)
            .first()
        )
        if pending is not None:
            if effective_state(pending, now) != State.EXPIRED:
                # TODO(spec): the previous card waits for an answer to another request; whether
                # the owner's «yes» links it anyway or a new card is created is not decided.
                continue
            pending.state = State.EXPIRED
            pending.save(update_fields=["state"])
        # TODO(spec): an archived previous card is linked again in the archive (§ 6.6.5–6.6.6);
        # whether it is restored is not decided.
        return card
    return None


def _linked_meanwhile(linked: QuerySet[ClientCard], exc: IntegrityError) -> ClientCard:
    """Another answer of the same owner linked a card at the same moment: that card is kept."""
    if not _one_per_profile(exc):
        raise exc
    card = linked.first()
    if card is None:
        raise exc
    return card


# --- Working data from the snapshot (§ 6.4.1–6.4.2) -------------------------------------------


def _contacts(card: Card) -> list[services.Contact]:
    return [
        services.Contact(c.kind, c.value, c.value_normalized, c.messenger)
        for c in card.contacts.all()
    ]


def _identifiers(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "; ".join(
        f"{i.get('type', '')}: {i.get('value', '')}".strip(": ")
        for i in value
        if isinstance(i, dict)
    )


def apply_value(
    ctx: CommandContext, company_id: UUID, link_id: UUID, field: str, expected_version: int
) -> Card:
    """Takes one value of the snapshot into the working data of the card; the profile does not
    change (§ 6.4.2). The previous value stays in the card's history."""
    link = ProfileLink.objects.filter(
        company_id=company_id, pk=link_id, state__in=(State.ACCEPTED, State.BROKEN)
    ).first()
    if link is None:
        raise errors.not_found()
    data = (link.shared or {}).get("data", {})
    if field not in data:
        raise errors.validation_error("field", code="VALUE_UNKNOWN")
    value = data[field]
    card_type = card_type_of(link)
    card_id = card_id_of(link)
    changes: dict[str, Any] = {}
    contacts: list[services.Contact] | None = None
    if field in ("email", "phone"):
        card = card_model(card_type).objects.get(pk=card_id)
        contact = services.normalize_contact(field, str(value), "field")
        contacts = _contacts(card)
        if all((c.kind, c.normalized) != (contact.kind, contact.normalized) for c in contacts):
            contacts.append(contact)
    elif field in ("first_name", "last_name", "middle_name"):
        changes[field] = str(value)[:150]
    elif field == "birth_date" and card_type == "client":
        changes[field] = date.fromisoformat(str(value))
    elif field in ("display_name", "legal_name"):
        changes["company_name"] = str(value)[:200]
    elif field in ("legal_address", "actual_address"):
        changes["address"] = str(value)[:500]
    elif field == "identifiers" and card_type == "counterparty":
        changes["registration"] = _identifiers(value)[:500]
    else:
        raise errors.validation_error("field", code="FIELD_NOT_APPLICABLE")
    if card_type == "client":
        result: Card = services.update_client(
            ctx, company_id, card_id, changes, contacts, expected_version
        )
    else:
        result = counterparties.update(
            ctx, company_id, card_id, changes, contacts, expected_version
        )
    ctx.audit(
        "profile_link.value_applied",
        object_type="profile_link",
        object_id=link.id,
        after={"field": field, "source": "profile"},
    )
    return result


# --- Payloads ---------------------------------------------------------------------------------


def _profile_of(link: ProfileLink) -> dict[str, str] | None:
    """The linked profile is named only after the recipient accepted."""
    if link.state not in (State.ACCEPTED, State.BROKEN):
        return None
    if link.profile == Profile.COMPANY and link.target_company_id is not None:
        company = companies.get_company(link.target_company_id)
        return {"public_id": company.public_id, "name": company.display_name} if company else None
    if link.target_user_id is not None:
        user = accounts.get_user(link.target_user_id)
        if user is not None:
            return {
                "public_id": user.public_id,
                "name": f"{user.first_name} {user.last_name}".strip(),
            }
    return None


def payload(link: ProfileLink, now: datetime) -> dict[str, Any]:
    """For the requesting company."""
    accepted = link.state in (State.ACCEPTED, State.BROKEN)
    return {
        "id": str(link.id),
        "state": effective_state(link, now),
        "profile": link.profile,
        "target": link.target_label,
        # No message is sent to a phone: there is no SMS channel yet (OPEN).
        "by_phone": bool(link.target_phone),
        "created_at": link.created_at.isoformat(),
        "expires_at": link.expires_at.isoformat(),
        "decided_at": link.decided_at.isoformat() if link.decided_at else None,
        "broken_at": link.broken_at.isoformat() if link.broken_at else None,
        "broken_initiator": link.broken_initiator,
        "linked_profile": _profile_of(link),
        "shared": (link.shared or {}).get("data", {}) if accepted else {},
        "shared_at": (link.shared or {}).get("at") if accepted else None,
    }


def incoming_payload(link: ProfileLink, now: datetime, available: dict[str, Any]) -> dict[str, Any]:
    """For the recipient: who asks, and the recipient's own data that may be given."""
    company = link.company
    return {
        "id": str(link.id),
        "state": effective_state(link, now),
        "company": {"public_id": company.public_id, "display_name": company.display_name},
        "card_type": card_type_of(link),
        "created_at": link.created_at.isoformat(),
        "expires_at": link.expires_at.isoformat(),
        "decided_at": link.decided_at.isoformat() if link.decided_at else None,
        "broken_at": link.broken_at.isoformat() if link.broken_at else None,
        "available": available,
        "shared": (link.shared or {}).get("data", {}),
    }
