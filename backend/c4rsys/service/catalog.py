"""Catalog of service works, hourly rates and posts (ТЗ § 16.1.1–16.1.5, § 16.3; customer's
decisions С-1, С-2 of 29.10.2026).

A company work is chosen from the system reference — the choice links it at once (§ 16.1.1) — or
is its own. It is priced by a fixed price or by norm hours × the hourly rate (§ 16.1.3); price and
hours may be filled later (§ 16.1.5). The rate is taken in the order work → location → company
(§ 16.1.2). Amounts are in the main currency of the company (decision С-2). Added works keep their
snapshot (§ 16.1.3).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from c4rsys.companies import locations
from c4rsys.companies.models import Company
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.platform.models import Currency
from c4rsys.platform.money import round_money
from c4rsys.service.models import ServicePost, ServiceRate, ServiceWork, SystemWork

Pricing = ServiceWork.Pricing


def amount_text(value: Decimal | None) -> str | None:
    return None if value is None else f"{Decimal(value).normalize():f}"


def system_names(code: str | None) -> dict[str, str] | None:
    if code is None:
        return None
    row = SystemWork.objects.filter(pk=code).values_list("names", flat=True).first()
    return dict(row) if row else None


def snapshot(work: ServiceWork) -> dict[str, Any]:
    return {
        "name": work.name,
        "system_work": work.system_work_id,
        "pricing": work.pricing,
        "price": amount_text(work.price),
        "hours": amount_text(work.hours),
        "rate": amount_text(work.rate),
        "currency": work.currency_id,
        "state": work.state,
    }


def _currency(company: Company) -> Currency:
    currency = (
        Currency.objects.filter(pk=company.main_currency_id).first()
        if company.main_currency_id
        else None
    )
    if currency is None:
        raise errors.business_rule("COMPANY_WITHOUT_CURRENCY")
    return currency


@dataclass(frozen=True)
class WorkData:
    name: str
    system_work: str | None
    pricing: str
    price: Decimal | None
    hours: Decimal | None
    rate: Decimal | None


def _validate(data: WorkData) -> None:
    if data.pricing not in Pricing.values:
        raise errors.validation_error("pricing", code="CHOICE_REQUIRED")
    if data.system_work is not None and not SystemWork.objects.filter(pk=data.system_work).exists():
        raise errors.validation_error("system_work", code="SYSTEM_WORK_UNKNOWN")


def create_work(ctx: CommandContext, company: Company, data: WorkData) -> ServiceWork:
    _validate(data)
    work = ServiceWork.objects.create(
        company=company,
        name=data.name,
        system_work_id=data.system_work,
        pricing=data.pricing,
        price=data.price,
        hours=data.hours,
        rate=data.rate,
        currency=_currency(company),
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "service_work.created", object_type="service_work", object_id=work.id, after=snapshot(work)
    )
    return work


def locked_work(company_id: UUID, work_id: UUID, expected_version: int) -> ServiceWork:
    work = ServiceWork.objects.select_for_update().filter(company_id=company_id, pk=work_id).first()
    if work is None:
        raise errors.not_found()
    if work.version != expected_version:
        raise errors.version_conflict()
    return work


def change_work(ctx: CommandContext, work: ServiceWork, data: WorkData) -> ServiceWork:
    """Name, link, pricing, price, hours and own rate; added works keep their terms (§ 16.1.3)."""
    _validate(data)
    before = snapshot(work)
    work.name = data.name
    work.system_work_id = data.system_work
    work.pricing = data.pricing
    work.price = data.price
    work.hours = data.hours
    work.rate = data.rate
    work.version += 1
    work.save()
    after = snapshot(work)
    if before == after:
        raise errors.business_rule("NOTHING_CHANGED")
    ctx.audit(
        "service_work.changed",
        object_type="service_work",
        object_id=work.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return work


def set_work_archived(ctx: CommandContext, work: ServiceWork, *, archived: bool) -> ServiceWork:
    """An archived work is not offered for new operations; it can be restored."""
    target = ServiceWork.State.ARCHIVED if archived else ServiceWork.State.ACTIVE
    if work.state == target:
        raise errors.business_rule("SERVICE_WORK_STATE_UNCHANGED", state=work.state)
    before = work.state
    work.state = target
    work.version += 1
    work.save(update_fields=["state", "version", "updated_at"])
    ctx.audit(
        "service_work.archived" if archived else "service_work.restored",
        object_type="service_work",
        object_id=work.id,
        before={"state": before},
        after={"state": target},
    )
    return work


def has_active_work(company_id: UUID, location_id: UUID) -> bool:
    """§ 4.3.1: a service context is ready with an active work in the catalog."""
    return ServiceWork.objects.filter(
        company_id=company_id, state=ServiceWork.State.ACTIVE
    ).exists()


# --- Hourly rates (§ 16.1.2) ---------------------------------------------------------------------


def _rate_of(company_id: UUID, location_id: UUID | None) -> Q:
    if location_id is None:
        return Q(company_id=company_id, location__isnull=True)
    return Q(company_id=company_id, location_id=location_id)


def rates_of(company_id: UUID) -> list[ServiceRate]:
    return list(ServiceRate.objects.filter(company_id=company_id).order_by("location_id"))


def set_rate(
    ctx: CommandContext, company: Company, location_id: UUID | None, rate: Decimal | None
) -> ServiceRate | None:
    """Sets or removes the rate of the company or of a service location."""
    if location_id is not None:
        if "service" not in locations.enabled_context_types(company.id, location_id):
            raise errors.validation_error("location_id", code="LOCATION_WITHOUT_SERVICE")
    current = (
        ServiceRate.objects.select_for_update().filter(_rate_of(company.id, location_id)).first()
    )
    before = amount_text(current.rate) if current is not None else None
    if rate is None:
        if current is None:
            raise errors.business_rule("NOTHING_CHANGED")
        object_id = current.id
        current.delete()
        result = None
    else:
        if current is None:
            current = ServiceRate(company=company, location_id=location_id)
        elif current.rate == rate:
            raise errors.business_rule("NOTHING_CHANGED")
        else:
            current.version += 1
        current.rate = rate
        current.currency = _currency(company)
        current.updated_by_user_id = ctx.actor.user_id
        current.updated_at = timezone.now()
        current.save()
        object_id = current.id
        result = current
    ctx.audit(
        "service_rate.set",
        object_type="service_rate",
        object_id=object_id,
        before={"location": str(location_id) if location_id else None, "rate": before},
        after={"location": str(location_id) if location_id else None, "rate": amount_text(rate)},
    )
    return result


@dataclass(frozen=True)
class Terms:
    """The price of a work added to an operation, with how it was found."""

    price: Decimal | None
    snapshot: dict[str, Any]


def terms_for(work: ServiceWork, location_id: UUID | None) -> Terms:
    """Fixed: the catalog price, maybe not known yet. Hours: norm hours × the rate of the work, of
    the location or of the company; without hours or a rate the price is not known (§ 16.1.4)."""
    minor = work.currency.minor_units
    rate: Decimal | None = None
    source = None
    if work.pricing == Pricing.HOURS:
        if work.rate is not None:
            rate, source = work.rate, "work"
        else:
            steps = ((location_id, "location"),) if location_id is not None else ()
            for loc, name in (*steps, (None, "company")):
                found = ServiceRate.objects.filter(_rate_of(work.company_id, loc)).first()
                if found is not None:
                    rate, source = found.rate, name
                    break
        price = (
            round_money(work.hours * rate, minor)
            if work.hours is not None and rate is not None
            else None
        )
    else:
        price = round_money(work.price, minor) if work.price is not None else None
    return Terms(
        price=price,
        snapshot={
            "work_id": str(work.id),
            "work_version": work.version,
            "name": work.name,
            "system_work": work.system_work_id,
            "pricing": work.pricing,
            "hours": amount_text(work.hours),
            "rate": amount_text(rate),
            "rate_source": source,
            "price": amount_text(price),
            "currency": work.currency_id,
            "currency_minor_units": minor,
        },
    )


# --- Posts (§ 16.3) ------------------------------------------------------------------------------


def posts_of(company_id: UUID, location_id: UUID | None = None) -> list[ServicePost]:
    query = ServicePost.objects.filter(company_id=company_id)
    if location_id is not None:
        query = query.filter(location_id=location_id)
    return list(query.order_by("location_id", "name"))


def require_service_location(company_id: UUID, location_id: UUID, field: str) -> None:
    locations.require_usable(company_id, location_id, field)
    if "service" not in locations.enabled_context_types(company_id, location_id):
        raise errors.validation_error(field, code="LOCATION_WITHOUT_SERVICE")


def create_post(ctx: CommandContext, company: Company, location_id: UUID, name: str) -> ServicePost:
    require_service_location(company.id, location_id, "location_id")
    try:
        with transaction.atomic():
            post = ServicePost.objects.create(company=company, location_id=location_id, name=name)
    except IntegrityError as exc:
        raise errors.validation_error("name", code="POST_NAME_TAKEN") from exc
    ctx.audit(
        "service_post.created",
        object_type="service_post",
        object_id=post.id,
        after={"location": str(location_id), "name": name},
    )
    return post


def locked_post(company_id: UUID, post_id: UUID, expected_version: int) -> ServicePost:
    post = ServicePost.objects.select_for_update().filter(company_id=company_id, pk=post_id).first()
    if post is None:
        raise errors.not_found()
    if post.version != expected_version:
        raise errors.version_conflict()
    return post


def change_post(
    ctx: CommandContext, post: ServicePost, *, name: str | None, archived: bool | None
) -> ServicePost:
    """A post is renamed or archived; an archived post takes no new records, the general zone
    stays (§ 16.3.4)."""
    before = {"name": post.name, "state": post.state}
    if name is not None:
        post.name = name
    if archived is not None:
        post.state = ServicePost.State.ARCHIVED if archived else ServicePost.State.ACTIVE
    after = {"name": post.name, "state": post.state}
    if before == after:
        raise errors.business_rule("NOTHING_CHANGED")
    if post.state == ServicePost.State.ARCHIVED and before["state"] == ServicePost.State.ACTIVE:
        from c4rsys.service import operations

        if operations.post_in_use(post.id):
            raise errors.business_rule("POST_IN_USE")
    post.version += 1
    try:
        with transaction.atomic():
            post.save()
    except IntegrityError as exc:
        raise errors.validation_error("name", code="POST_NAME_TAKEN") from exc
    ctx.audit(
        "service_post.changed",
        object_type="service_post",
        object_id=post.id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={k: v for k, v in after.items() if v != before[k]},
    )
    return post
