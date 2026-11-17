"""Tags (customer's decisions of 17.11.2026): marks of the company put on records of any section
— clients, counterparties, vehicles, items of the warehouse, orders, rentals, service operations
— to find them together later; and personal tags, which their employee alone sees."""

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from c4rsys.companies.models import Company
from c4rsys.platform.ids import uuid7

NAME_MAX = 40


class TagGroup(models.Model):
    """A group of tags of the company (customer's decisions of 17.11.2026). Everybody sees the
    groups and their tags; the tags of a group are made and changed by the employees named in it
    — and by whoever has the right «Manage tags»."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=NAME_MAX)
    name_key = models.CharField(max_length=NAME_MAX)
    # The kinds of records the tags of the group are offered for; empty — for every kind.
    kinds = models.JSONField(default=list, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "tag_group"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="tag_group_company_id_uniq"),
            models.UniqueConstraint(fields=["company", "name_key"], name="tag_group_name_uniq"),
            models.CheckConstraint(condition=~Q(name=""), name="tag_group_name_given"),
        ]

    def __str__(self) -> str:
        return self.name


class TagGroupMember(models.Model):
    """An employee named in a group: they make and change its tags."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    group = models.ForeignKey(TagGroup, on_delete=models.CASCADE, related_name="members")
    employment_id = models.UUIDField()
    added_by_user_id = models.UUIDField()
    added_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "tag_group_member"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "employment_id"], name="tag_group_member_once"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.group_id}:{self.employment_id}"


class Tag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    # The group of a tag of the company, if it is in one; a group removed leaves its tags.
    group = models.ForeignKey(
        TagGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="tags"
    )
    # The user whose personal tag it is in this company; empty — a tag of the company. A personal
    # tag is seen by its owner alone.
    owner_user_id = models.UUIDField(null=True, blank=True)
    name = models.CharField(max_length=NAME_MAX)
    # The name without the difference of letter case: «VIP» and «vip» are one tag.
    name_key = models.CharField(max_length=NAME_MAX)
    # A key of the palette of the interface.
    color = models.CharField(max_length=20)
    # A tag no longer needed is archived, not deleted: its marks come back with it.
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(default=now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "tag"
        constraints = [
            models.UniqueConstraint(fields=["company", "id"], name="tag_company_id_uniq"),
            models.UniqueConstraint(
                fields=["company", "name_key"],
                condition=Q(owner_user_id__isnull=True),
                name="tag_company_name_uniq",
            ),
            models.UniqueConstraint(
                fields=["company", "owner_user_id", "name_key"],
                condition=Q(owner_user_id__isnull=False),
                name="tag_personal_name_uniq",
            ),
            models.CheckConstraint(condition=~Q(name=""), name="tag_name_given"),
            # A personal tag is in no group of the company.
            models.CheckConstraint(
                condition=Q(owner_user_id__isnull=True) | Q(group__isnull=True),
                name="tag_personal_without_group",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class TagLink(models.Model):
    """A tag put on a record. The record is named by its kind and id: the tags do not read the
    tables of the other sections."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    tag = models.ForeignKey(Tag, on_delete=models.PROTECT, related_name="links")
    object_type = models.CharField(max_length=30)
    object_id = models.UUIDField()
    added_by_user_id = models.UUIDField()
    added_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "tag_link"
        constraints = [
            models.UniqueConstraint(
                fields=["tag", "object_type", "object_id"], name="tag_link_once"
            ),
        ]
        indexes = [
            models.Index(fields=["company", "object_type", "object_id"], name="tag_link_object_idx")
        ]

    def __str__(self) -> str:
        return f"{self.tag_id}:{self.object_type}:{self.object_id}"
