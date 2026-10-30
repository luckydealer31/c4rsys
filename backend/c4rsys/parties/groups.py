"""Groups of the client base (ТЗ § 6.2.14; decision В-3): named by the company, a client may be
in several; kept by the employees who change clients. A change of the groups does not change
discounts already applied (§ 12.5.2)."""

from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.parties.models import ClientCard, ClientGroup, ClientGroupMember
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext


def groups_of(company_id: UUID) -> QuerySet[ClientGroup]:
    return ClientGroup.objects.filter(company_id=company_id).order_by("name")


def create(ctx: CommandContext, company: Company, name: str) -> ClientGroup:
    name = name.strip()
    if not name:
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    if groups_of(company.id).filter(name__iexact=name, state=ClientGroup.State.ACTIVE).exists():
        raise errors.business_rule("CLIENT_GROUP_EXISTS", field="name")
    group = ClientGroup.objects.create(
        company=company, name=name, created_by_user_id=ctx.actor.user_id, created_at=timezone.now()
    )
    ctx.audit(
        "client_group.created", object_type="client_group", object_id=group.id, after={"name": name}
    )
    return group


def change(
    ctx: CommandContext,
    company_id: UUID,
    group_id: UUID,
    *,
    name: str | None,
    archived: bool | None,
) -> ClientGroup:
    group = (
        ClientGroup.objects.select_for_update().filter(company_id=company_id, pk=group_id).first()
    )
    if group is None:
        raise errors.not_found()
    before = {"name": group.name, "state": group.state}
    if name is not None:
        if not name.strip():
            raise errors.validation_error("name", code="FIELD_REQUIRED")
        group.name = name.strip()
    if archived is not None:
        group.state = ClientGroup.State.ARCHIVED if archived else ClientGroup.State.ACTIVE
    group.version += 1
    group.save()
    ctx.audit(
        "client_group.changed",
        object_type="client_group",
        object_id=group.id,
        before=before,
        after={"name": group.name, "state": group.state},
    )
    return group


def ids_of_client(client_id: UUID) -> list[UUID]:
    return list(
        ClientGroupMember.objects.filter(client_id=client_id).values_list("group_id", flat=True)
    )


def set_of_client(
    ctx: CommandContext, company_id: UUID, client_id: UUID, group_ids: list[UUID]
) -> list[UUID]:
    """The groups of a client replaced by the given ones; archived groups are not added."""
    client = ClientCard.objects.filter(company_id=company_id, pk=client_id).first()
    if client is None:
        raise errors.not_found()
    wanted = set(group_ids)
    found = set(
        groups_of(company_id)
        .filter(pk__in=wanted, state=ClientGroup.State.ACTIVE)
        .values_list("id", flat=True)
    )
    current = set(ids_of_client(client_id))
    if wanted - found - current:
        raise errors.validation_error("group_ids", code="CLIENT_GROUP_UNKNOWN")
    ClientGroupMember.objects.filter(client_id=client_id).exclude(group_id__in=wanted).delete()
    for group_id in wanted - current:
        ClientGroupMember.objects.create(company_id=company_id, group_id=group_id, client=client)
    ctx.audit(
        "client.groups_changed",
        object_type="client_card",
        object_id=client_id,
        before={"groups": sorted(str(g) for g in current)},
        after={"groups": sorted(str(g) for g in wanted)},
    )
    return sorted(wanted, key=str)
