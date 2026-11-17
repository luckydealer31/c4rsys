"""Tags of the company and personal tags (customer's decisions of 17.11.2026).

A tag of the company is created and changed with the right «Manage tags»; the other employees
choose from the list. A personal tag is made by any employee for themself and is seen by them
alone. A tag is only a mark: it changes no price, rule or right. The records are named by their
kind and id; what an employee may see of them is asked of the single search (A04), so a tag opens
nothing the employee could not open already.
"""

from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, QuerySet
from django.utils.timezone import now

from c4rsys.platform import errors
from c4rsys.platform import search as platform_search
from c4rsys.platform.commands import Actor, CommandContext
from c4rsys.tags.models import NAME_MAX, Tag, TagGroup, TagGroupMember, TagLink

# The kinds of records a tag is put on — as the single search names them.
TYPES = ("client", "counterparty", "vehicle", "item", "order", "rental", "service_operation")
# The names of the same records in the journal, where they differ.
JOURNAL_TYPES = {"client": "client_card", "vehicle": "vehicle_card", "item": "warehouse_item"}
# Kinds shown on the page of a tag besides these: their sections put the tags by their own rules
# (a task — with its editing, § 23.9.5).
LISTED = (*TYPES, "task")
PALETTE = ("gray", "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink")
# How many records of one kind the page of a tag shows.
PER_KIND = 200


def _name(value: str) -> str:
    name = " ".join(value.split())
    if not name:
        raise errors.validation_error("name", code="FIELD_REQUIRED")
    if len(name) > NAME_MAX:
        raise errors.validation_error("name", code="TAG_NAME_TOO_LONG")
    return name


def _color(value: str) -> str:
    if value not in PALETTE:
        raise errors.validation_error("color", code="TAG_COLOR_UNKNOWN")
    return value


def visible(company_id: UUID, user_id: UUID) -> QuerySet[Tag]:
    """The tags of the company and the personal tags of this user."""
    return Tag.objects.filter(company_id=company_id).filter(
        Q(owner_user_id__isnull=True) | Q(owner_user_id=user_id)
    )


def get(company_id: UUID, user_id: UUID, tag_id: UUID) -> Tag:
    tag = visible(company_id, user_id).filter(pk=tag_id).first()
    if tag is None:
        raise errors.not_found()
    return tag


def payload(tag: Tag, count: int | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(tag.id),
        "name": tag.name,
        "color": tag.color,
        "personal": tag.owner_user_id is not None,
        "archived": tag.archived_at is not None,
        "version": tag.version,
        "group_id": str(tag.group_id) if tag.group_id else None,
    }
    if count is not None:
        data["count"] = count
    return data


def listing(company_id: UUID, user_id: UUID) -> list[dict[str, Any]]:
    """Every tag the user sees, the archived ones too, with the number of its marks."""
    tags = visible(company_id, user_id).annotate(marks=Count("links")).order_by("name_key", "id")
    return [payload(t, t.marks) for t in tags]


def _journal(ctx: CommandContext, action: str, tag: Tag, **changes: Any) -> None:
    """A personal tag is seen by its owner alone: the journal keeps the fact, not the name."""
    if tag.owner_user_id is None:
        ctx.audit(f"tag.{action}", object_type="tag", object_id=tag.id, **changes)
    else:
        ctx.audit(f"tag.personal_{action}", object_type="personal_tag", object_id=tag.id)


def create(
    ctx: CommandContext,
    company_id: UUID,
    name: str,
    color: str,
    *,
    personal: bool,
    group_id: UUID | None = None,
) -> Tag:
    name, color = _name(name), _color(color)
    group = None if personal or group_id is None else get_group(company_id, group_id)
    try:
        with transaction.atomic():
            tag = Tag.objects.create(
                company_id=company_id,
                group=group,
                owner_user_id=ctx.actor.user_id if personal else None,
                name=name,
                name_key=name.lower(),
                color=color,
                created_by_user_id=ctx.actor.user_id,
            )
    except IntegrityError as exc:
        raise errors.validation_error("name", code="TAG_EXISTS") from exc
    _journal(ctx, "created", tag, after={"name": name, "color": color})
    return tag


def _locked(company_id: UUID, user_id: UUID, tag_id: UUID, expected_version: int) -> Tag:
    tag = visible(company_id, user_id).select_for_update().filter(pk=tag_id).first()
    if tag is None:
        raise errors.not_found()
    if tag.version != expected_version:
        raise errors.version_conflict()
    return tag


def change(
    ctx: CommandContext,
    company_id: UUID,
    tag_id: UUID,
    expected_version: int,
    *,
    name: str | None,
    color: str | None,
    group: tuple[UUID | None] | None = None,
) -> Tag:
    """A new name or colour stands at once everywhere the tag is put. `group` — a one-element
    tuple with the new group of the tag, or with nothing to take it out of its group."""
    tag = _locked(company_id, ctx.actor.user_id, tag_id, expected_version)

    def state() -> dict[str, Any]:
        return {"name": tag.name, "color": tag.color, "group": tag.group.name if tag.group else ""}

    before = state()
    if name is not None:
        tag.name = _name(name)
        tag.name_key = tag.name.lower()
    if color is not None:
        tag.color = _color(color)
    if group is not None and tag.owner_user_id is None:
        tag.group = get_group(company_id, group[0]) if group[0] is not None else None
    after = state()
    if after == before:
        raise errors.validation_error("name", code="TAG_UNCHANGED")
    tag.version += 1
    try:
        with transaction.atomic():
            tag.save(update_fields=["name", "name_key", "color", "group", "version"])
    except IntegrityError as exc:
        raise errors.validation_error("name", code="TAG_EXISTS") from exc
    _journal(ctx, "changed", tag, before=before, after=after)
    return tag


def set_archived(
    ctx: CommandContext, company_id: UUID, tag_id: UUID, expected_version: int, archived: bool
) -> Tag:
    """An archived tag is shown nowhere but its list; its marks are kept and come back with it."""
    tag = _locked(company_id, ctx.actor.user_id, tag_id, expected_version)
    if (tag.archived_at is not None) == archived:
        raise errors.validation_error("tag", code="TAG_UNCHANGED")
    tag.archived_at = now() if archived else None
    tag.version += 1
    tag.save(update_fields=["archived_at", "version"])
    _journal(ctx, "archived" if archived else "restored", tag, after={"name": tag.name})
    return tag


def may_see(actor: Actor, company_id: UUID, object_type: str, object_id: UUID) -> bool:
    """Whether the employee sees the record in its section — the condition of putting a tag."""
    return (
        object_type in TYPES
        and platform_search.lookup(actor, company_id, object_type, object_id) is not None
    )


def _active(company_id: UUID, user_id: UUID) -> QuerySet[Tag]:
    return visible(company_id, user_id).filter(archived_at__isnull=True)


def tags_of(company_id: UUID, user_id: UUID, object_type: str, object_id: UUID) -> list[Tag]:
    """The tags on a record: of the company first, then the user's own."""
    found = _active(company_id, user_id).filter(
        links__object_type=object_type, links__object_id=object_id
    )
    return sorted(found, key=lambda t: (t.owner_user_id is not None, t.name_key))


def links_of_type(company_id: UUID, user_id: UUID, object_type: str) -> dict[str, list[str]]:
    """The tags of every record of a kind: for the lists of the sections."""
    answer: dict[str, list[str]] = {}
    links = TagLink.objects.filter(
        company_id=company_id,
        object_type=object_type,
        tag__in=_active(company_id, user_id),
    ).values_list("object_id", "tag_id")
    for object_id, tag_id in links:
        answer.setdefault(str(object_id), []).append(str(tag_id))
    return answer


def same_links(
    company_id: UUID, user_id: UUID, object_type: str, object_id: UUID, wanted: set[UUID]
) -> bool:
    """Whether the record already has exactly these tags: then there is nothing to do, and a
    command — which must leave a journal entry — is not run."""
    return {t.id for t in tags_of(company_id, user_id, object_type, object_id)} == wanted


def set_links(
    ctx: CommandContext, company_id: UUID, object_type: str, object_id: UUID, wanted: set[UUID]
) -> None:
    """Puts on the record exactly the given tags out of those the user sees; the personal tags of
    the others stay as they are."""
    user_id = ctx.actor.user_id
    tags = {t.id: t for t in _active(company_id, user_id).select_for_update()}
    if wanted - set(tags):
        raise errors.validation_error("tag_ids", code="TAG_UNKNOWN")
    current = {
        link.tag_id: link
        for link in TagLink.objects.select_for_update().filter(
            company_id=company_id, object_type=object_type, object_id=object_id, tag_id__in=tags
        )
    }
    added, removed = wanted - set(current), set(current) - wanted
    if not added and not removed:
        raise errors.validation_error("tag_ids", code="TAG_UNCHANGED")
    # A tag of a group is put on the kinds of records the group is for.
    kinds = {g.id: g.kinds for g in TagGroup.objects.filter(company_id=company_id)}
    for tag_id in added:
        group_id = tags[tag_id].group_id
        allowed = kinds.get(group_id) if group_id is not None else None
        if allowed and object_type not in allowed:
            raise errors.validation_error("tag_ids", code="TAG_NOT_FOR_KIND")
    TagLink.objects.bulk_create(
        TagLink(
            company_id=company_id,
            tag_id=tag_id,
            object_type=object_type,
            object_id=object_id,
            added_by_user_id=user_id,
        )
        for tag_id in added
    )
    TagLink.objects.filter(pk__in=[current[tag_id].pk for tag_id in removed]).delete()

    def names(ids: set[UUID]) -> str:
        return ", ".join(sorted(tags[i].name for i in ids if tags[i].owner_user_id is None))

    changed = added | removed
    # The tags of the company are told in the history of the record itself.
    if any(tags[i].owner_user_id is None for i in changed):
        ctx.audit(
            "tag.links_changed",
            object_type=JOURNAL_TYPES.get(object_type, object_type),
            object_id=object_id,
            before={"tags": names(set(current))},
            after={"tags": names(wanted)},
        )
    if any(tags[i].owner_user_id is not None for i in changed):
        ctx.audit("tag.personal_links_changed", object_type="personal_tag", object_id=object_id)


def objects_of(actor: Actor, company_id: UUID, tag: Tag) -> dict[str, list[dict[str, Any]]]:
    """Everything the tag is put on, by kinds — only what the employee sees in its section."""
    answer: dict[str, list[dict[str, Any]]] = {}
    for object_type in LISTED:
        ids = (
            TagLink.objects.filter(tag=tag, object_type=object_type)
            .order_by("-added_at")
            .values_list("object_id", flat=True)[:PER_KIND]
        )
        hits = [platform_search.lookup(actor, company_id, object_type, i) for i in ids]
        found = [h.payload() for h in hits if h is not None]
        if found:
            answer[object_type] = found
    return answer


# --- For the sections (A04: they keep no tags of their own and read no table of the tags) -------


def matching_ids(company_id: UUID, user_id: UUID | None, object_type: str, text: str) -> set[UUID]:
    """The records of a kind marked with a tag whose name has the text in it: the search inside a
    section finds them too (customer's decision of 17.11.2026). With a user — their personal tags
    count as well."""
    needle = " ".join(text.split()).lower()
    if not needle:
        return set()
    tags = (
        visible(company_id, user_id)
        if user_id is not None
        else Tag.objects.filter(company_id=company_id, owner_user_id__isnull=True)
    ).filter(archived_at__isnull=True, name_key__contains=needle)
    return set(
        TagLink.objects.filter(
            company_id=company_id, object_type=object_type, tag__in=tags
        ).values_list("object_id", flat=True)
    )


def _of_company(company_id: UUID) -> QuerySet[Tag]:
    return Tag.objects.filter(
        company_id=company_id, owner_user_id__isnull=True, archived_at__isnull=True
    )


def company_tags_by_ids(company_id: UUID, tag_ids: list[UUID]) -> list[Tag] | None:
    """The active tags of the company with these ids; nothing when one of them is not such."""
    found = list(_of_company(company_id).filter(pk__in=tag_ids))
    return found if len(found) == len(set(tag_ids)) else None


def company_tags_among(company_id: UUID, tag_ids: list[UUID]) -> list[Tag]:
    """Those of the ids that are active tags of the company now."""
    return list(_of_company(company_id).filter(pk__in=tag_ids))


def company_tags_of(company_id: UUID, object_type: str, object_id: UUID) -> list[Tag]:
    """The tags of the company on a record, by name — what every employee sees of them."""
    return list(
        _of_company(company_id)
        .filter(links__object_type=object_type, links__object_id=object_id)
        .order_by("name_key")
    )


def tagged_ids(company_id: UUID, tag_id: UUID, object_type: str) -> list[UUID]:
    """The records of a kind a tag is put on: for the filter of a list."""
    return list(
        TagLink.objects.filter(
            company_id=company_id, tag_id=tag_id, object_type=object_type
        ).values_list("object_id", flat=True)
    )


def replace_company_links(
    user_id: UUID, company_id: UUID, object_type: str, object_id: UUID, wanted: list[Tag]
) -> None:
    """Puts on a record exactly the given tags of the company; the personal ones stay. The
    journal entry is the caller's: a section that changes the tags by its own rules tells of it
    in its own words."""
    current = TagLink.objects.select_for_update().filter(
        company_id=company_id,
        object_type=object_type,
        object_id=object_id,
        tag__owner_user_id__isnull=True,
    )
    keep = {tag.id for tag in wanted}
    have = {link.tag_id for link in current}
    current.exclude(tag_id__in=keep).delete()
    TagLink.objects.bulk_create(
        TagLink(
            company_id=company_id,
            tag=tag,
            object_type=object_type,
            object_id=object_id,
            added_by_user_id=user_id,
        )
        for tag in wanted
        if tag.id not in have
    )


# --- Groups of tags (customer's decisions of 17.11.2026) ----------------------------------------


def groups_of(company_id: UUID) -> QuerySet[TagGroup]:
    return TagGroup.objects.filter(company_id=company_id).order_by("name_key", "id")


def get_group(company_id: UUID, group_id: UUID) -> TagGroup:
    group = TagGroup.objects.filter(company_id=company_id, pk=group_id).first()
    if group is None:
        raise errors.validation_error("group_id", code="TAG_GROUP_UNKNOWN")
    return group


def is_named(group_id: UUID | None, employment_id: UUID | None) -> bool:
    """Whether the employee is named in the group: then they make and change its tags."""
    return (
        group_id is not None
        and employment_id is not None
        and TagGroupMember.objects.filter(group_id=group_id, employment_id=employment_id).exists()
    )


def group_payloads(company_id: UUID, actor: Actor, may_manage: bool) -> list[dict[str, Any]]:
    from c4rsys.companies import staff

    groups = list(groups_of(company_id).prefetch_related("members"))
    ids = {m.employment_id for g in groups for m in g.members.all()}
    names = staff.employee_names(company_id, ids) if ids else {}
    return [
        {
            "id": str(g.id),
            "name": g.name,
            "kinds": list(g.kinds),
            "members": sorted(
                (
                    {"id": str(m.employment_id), "name": names.get(m.employment_id, "")}
                    for m in g.members.all()
                ),
                key=lambda member: member["name"],
            ),
            "can_create": may_manage
            or any(m.employment_id == actor.employment_id for m in g.members.all()),
            "version": g.version,
        }
        for g in groups
    ]


def _kinds(value: list[str]) -> list[str]:
    if any(kind not in LISTED for kind in value):
        raise errors.validation_error("kinds", code="TAG_OBJECT_TYPE_UNKNOWN")
    return [kind for kind in LISTED if kind in value]


def _set_members(ctx: CommandContext, group: TagGroup, member_ids: set[UUID]) -> None:
    from c4rsys.companies import staff

    known = staff.employee_names(group.company_id)
    if member_ids - set(known):
        raise errors.validation_error("member_ids", code="TAG_GROUP_MEMBER_UNKNOWN")
    have = set(group.members.values_list("employment_id", flat=True))
    group.members.filter(employment_id__in=have - member_ids).delete()
    TagGroupMember.objects.bulk_create(
        TagGroupMember(
            company_id=group.company_id,
            group=group,
            employment_id=employment_id,
            added_by_user_id=ctx.actor.user_id,
        )
        for employment_id in member_ids - have
    )


def _group_state(group: TagGroup) -> dict[str, Any]:
    members = sorted(str(i) for i in group.members.values_list("employment_id", flat=True))
    return {"name": group.name, "kinds": list(group.kinds), "members": members}


def create_group(
    ctx: CommandContext, company_id: UUID, name: str, kinds: list[str], member_ids: set[UUID]
) -> TagGroup:
    name = _name(name)
    try:
        with transaction.atomic():
            group = TagGroup.objects.create(
                company_id=company_id,
                name=name,
                name_key=name.lower(),
                kinds=_kinds(kinds),
                created_by_user_id=ctx.actor.user_id,
            )
    except IntegrityError as exc:
        raise errors.validation_error("name", code="TAG_GROUP_EXISTS") from exc
    _set_members(ctx, group, member_ids)
    ctx.audit(
        "tag_group.created", object_type="tag_group", object_id=group.id, after=_group_state(group)
    )
    return group


def change_group(
    ctx: CommandContext,
    company_id: UUID,
    group_id: UUID,
    expected_version: int,
    *,
    name: str | None,
    kinds: list[str] | None,
    member_ids: set[UUID] | None,
) -> TagGroup:
    group = TagGroup.objects.select_for_update().filter(company_id=company_id, pk=group_id).first()
    if group is None:
        raise errors.not_found()
    if group.version != expected_version:
        raise errors.version_conflict()
    before = _group_state(group)
    if name is not None:
        group.name = _name(name)
        group.name_key = group.name.lower()
    if kinds is not None:
        group.kinds = _kinds(kinds)
    if member_ids is not None:
        _set_members(ctx, group, member_ids)
    group.version += 1
    try:
        with transaction.atomic():
            group.save(update_fields=["name", "name_key", "kinds", "version"])
    except IntegrityError as exc:
        raise errors.validation_error("name", code="TAG_GROUP_EXISTS") from exc
    ctx.audit(
        "tag_group.changed",
        object_type="tag_group",
        object_id=group.id,
        before=before,
        after=_group_state(group),
    )
    return group


def remove_group(ctx: CommandContext, company_id: UUID, group_id: UUID) -> None:
    """The group goes; its tags stay, without a group."""
    group = TagGroup.objects.select_for_update().filter(company_id=company_id, pk=group_id).first()
    if group is None:
        raise errors.not_found()
    before = _group_state(group)
    group.delete()
    ctx.audit("tag_group.removed", object_type="tag_group", object_id=group_id, before=before)
