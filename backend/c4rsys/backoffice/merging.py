"""Merge and split of two global personal accounts by the support (ТЗ § 24.1.8).

* No automatic merge: a staff member with the separate permission `accounts.merge` checks that
  both accounts belong to one person, chooses the main one and sees what moves — contacts, places
  of work with their positions, shares, profile links, vehicles (c4rsys.platform.userlinks).
* Conflicts of data and rights are shown and must be resolved before the merge; nothing is
  resolved automatically. The merge is refused while any remains.
* The second account no longer signs in; its ID refers to the main one (accounts.resolve), so the
  earlier records keep their link. Documents, rentals, payments and other business records of
  companies are not rewritten: they keep the account that made them.
* The basis, the staff member and the log of the transfer are kept (`AccountMergeItem`, the
  system journal).
* An erroneous merge is split: the links known by the history go back to their accounts; the
  links that appeared after the merge are checked and assigned by the support by hand; the split
  is not completed while any of them is not assigned or a conflict remains.
"""

from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.accounts import services as accounts
from c4rsys.accounts.models import UserAccount
from c4rsys.backoffice import directory
from c4rsys.backoffice.core import StaffContext
from c4rsys.backoffice.models import (
    AccountMerge,
    AccountMergeItem,
    AccountSplitAssignment,
    SupportTicket,
    SystemBlock,
    SystemStaffMember,
)
from c4rsys.platform import errors, userlinks
from c4rsys.platform.userlinks import UserLink

State = AccountMerge.State
Ref = tuple[str, str, str]


def _guards(user: UserAccount, *, secondary: bool) -> list[UserLink]:
    """What the internal interface itself does not let merge (PROPOSAL): a second account that
    is a member of the system staff, and a blocked account."""
    found: list[UserLink] = []
    if secondary:
        member = SystemStaffMember.objects.filter(
            user=user, state=SystemStaffMember.State.ACTIVE
        ).first()
        if member is not None:
            found.append(UserLink("backoffice", "staff_member", member.id, {}, "", blocking=True))
    block = SystemBlock.objects.filter(user=user, ended_at__isnull=True).first()
    if block is not None:
        found.append(UserLink("backoffice", "system_block", block.id, {}, "", blocking=True))
    return found


def _check_pair(main: UserAccount, secondary: UserAccount) -> None:
    if main.id == secondary.id:
        raise errors.validation_error("secondary", code="MERGE_SAME_ACCOUNT")
    for user in (main, secondary):
        if user.state != UserAccount.State.ACTIVE:
            raise errors.business_rule("MERGE_ACCOUNT_NOT_ACTIVE", user=user.public_id)


def _user(user: UserAccount) -> dict[str, Any]:
    return directory.user_summary(user)


def preview(main: UserAccount, secondary: UserAccount) -> dict[str, Any]:
    _check_pair(main, secondary)
    main_links = userlinks.links_of(main.id) + _guards(main, secondary=False)
    moving = userlinks.links_of(secondary.id) + _guards(secondary, secondary=True)
    combined = [("main", link) for link in main_links] + [("secondary", link) for link in moving]
    return {
        "main": _user(main),
        "secondary": _user(secondary),
        "moving": [userlinks.payload(link) for link in moving if not link.blocking],
        "kept": [userlinks.payload(link) for link in main_links if not link.blocking],
        "conflicts": userlinks.conflicts({"main": combined}),
    }


def _move(links: list[UserLink], source: UUID, target: UUID) -> int:
    groups: dict[tuple[str, str], list[UUID]] = {}
    for link in links:
        groups.setdefault((link.module, link.kind), []).append(link.record_id)
    moved = 0
    for (module, kind), ids in groups.items():
        moved += userlinks.provider(module).move(kind, ids, source, target)
    return moved


def _lock_pair(a: UUID, b: UUID) -> None:
    # A fixed order, so two procedures on the same accounts do not wait for each other.
    for user_id in sorted([a, b]):
        accounts.lock_user(user_id)


def merge(
    ctx: StaffContext,
    main: UserAccount,
    secondary: UserAccount,
    *,
    reason: str,
    ticket: SupportTicket | None,
    expected: list[str] | None,
) -> AccountMerge:
    _lock_pair(main.id, secondary.id)
    main.refresh_from_db()
    secondary.refresh_from_db()
    shown = preview(main, secondary)
    if shown["conflicts"]:
        raise errors.business_rule("MERGE_CONFLICTS", count=len(shown["conflicts"]))
    moving = [link for link in userlinks.links_of(secondary.id) if not link.blocking]
    if expected is not None and sorted(expected) != sorted(str(x.record_id) for x in moving):
        # The links changed since the support looked at them.
        raise errors.business_rule("MERGE_CHANGED")
    kept = [link for link in userlinks.links_of(main.id) if not link.blocking]
    now = timezone.now()
    found = AccountMerge.objects.create(
        main_user=main,
        secondary_user=secondary,
        reason=reason,
        ticket=ticket,
        merged_at=now,
        merged_by_user_id=ctx.staff.user.id,
    )
    AccountMergeItem.objects.bulk_create(
        [
            AccountMergeItem(
                merge=found,
                phase=phase,
                module=link.module,
                kind=link.kind,
                record_id=link.record_id,
                label=link.label,
            )
            for phase, links in (
                (AccountMergeItem.Phase.MOVED, moving),
                (AccountMergeItem.Phase.KEPT, kept),
            )
            for link in links
        ]
    )
    moved = _move(moving, secondary.id, main.id)
    ended = accounts.merge_account(secondary.id, main.id, now)
    ctx.journal(
        "account.merged",
        object_type="account_merge",
        object_id=found.id,
        target_user_id=main.id,
        ticket_id=ticket.id if ticket else None,
        reason=reason,
        before={"main": main.public_id, "secondary": secondary.public_id},
        after={
            "moved": [userlinks.payload(link) for link in moving],
            "moved_count": moved,
            "sessions_ended": ended,
        },
    )
    ctx.journal(
        "account.merged_into",
        object_type="user_account",
        object_id=secondary.public_id,
        target_user_id=secondary.id,
        after={"merged_into": main.public_id, "merge": str(found.id)},
    )
    return found


def get(merge_id: UUID) -> AccountMerge:
    found = (
        AccountMerge.objects.select_related("main_user", "secondary_user", "ticket")
        .filter(pk=merge_id)
        .first()
    )
    if found is None:
        raise errors.not_found()
    return found


def locked(merge_id: UUID, expected_version: int | None) -> AccountMerge:
    found = (
        AccountMerge.objects.select_for_update(of=("self",))
        .select_related("main_user", "secondary_user")
        .filter(pk=merge_id)
        .first()
    )
    if found is None:
        raise errors.not_found()
    if expected_version is not None and found.version != expected_version:
        raise errors.version_conflict()
    return found


def merges(limit: int = 100) -> list[AccountMerge]:
    return list(
        AccountMerge.objects.select_related("main_user", "secondary_user").order_by("-merged_at")[
            :limit
        ]
    )


def _items(found: AccountMerge, phase: str) -> set[Ref]:
    return {
        (i.module, i.kind, str(i.record_id))
        for i in AccountMergeItem.objects.filter(merge=found, phase=phase)
    }


def _assignments(found: AccountMerge) -> dict[Ref, AccountSplitAssignment]:
    return {
        (a.module, a.kind, str(a.record_id)): a
        for a in AccountSplitAssignment.objects.filter(merge=found, replaced_at__isnull=True)
    }


def split_plan(found: AccountMerge) -> dict[str, Any]:
    """What the split does now: the links returned by the history, the links that stay on the
    main account, the new links with their assignment, and the conflicts of the result."""
    moved, kept = _items(found, AccountMergeItem.Phase.MOVED), _items(found, "kept")
    assigned = _assignments(found)
    restored: list[UserLink] = []
    staying: list[UserLink] = []
    new: list[tuple[UserLink, str | None]] = []
    for link in userlinks.links_of(found.main_user_id):
        if link.ref in moved:
            restored.append(link)
        elif link.ref in kept:
            staying.append(link)
        else:
            choice = assigned.get(link.ref)
            new.append((link, choice.target if choice else None))
    on_secondary = userlinks.links_of(found.secondary_user_id)
    main_side = [("main", x) for x in staying] + [
        ("new", x) for x, target in new if target == AccountSplitAssignment.Target.MAIN
    ]
    secondary_side = (
        [("restored", x) for x in restored]
        + [("new", x) for x, target in new if target == AccountSplitAssignment.Target.SECONDARY]
        + [("secondary", x) for x in on_secondary]
    )
    blocking_new = [("new", x) for x, _ in new if x.blocking]
    unassigned = [x for x, target in new if target is None and not x.blocking]
    current = {link.ref for link in restored}
    return {
        "restored": [userlinks.payload(x) for x in restored],
        "staying": [userlinks.payload(x) for x in staying],
        "new": [
            {**userlinks.payload(x), "assigned_to": target} for x, target in new if not x.blocking
        ],
        # Links of the history that are no longer on the main account (ended or moved since):
        # their own module keeps them, nothing is returned.
        "gone": [{"module": m, "kind": k, "record_id": r} for m, k, r in sorted(moved - current)],
        "unassigned": len(unassigned),
        "conflicts": userlinks.conflicts(
            {"main": main_side + blocking_new, "secondary": secondary_side}
        ),
        "_restored": restored,
        "_to_secondary": [x for x, t in new if t == AccountSplitAssignment.Target.SECONDARY],
    }


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def start_split(ctx: StaffContext, found: AccountMerge, reason: str) -> AccountMerge:
    if found.state != State.MERGED:
        raise errors.business_rule("SPLIT_NOT_ALLOWED", state=found.state)
    now = timezone.now()
    found.state = State.SPLITTING
    found.split_reason = reason
    found.split_started_at = now
    found.split_started_by_user_id = ctx.staff.user.id
    found.version += 1
    found.save(
        update_fields=[
            "state",
            "split_reason",
            "split_started_at",
            "split_started_by_user_id",
            "version",
        ]
    )
    ctx.journal(
        "account.split_started",
        object_type="account_merge",
        object_id=found.id,
        target_user_id=found.main_user_id,
        reason=reason,
    )
    return found


def assign(
    ctx: StaffContext, found: AccountMerge, *, module: str, kind: str, record_id: UUID, target: str
) -> AccountSplitAssignment:
    if found.state != State.SPLITTING:
        raise errors.business_rule("SPLIT_NOT_STARTED")
    if target not in AccountSplitAssignment.Target.values:
        raise errors.validation_error("target")
    ref = (module, kind, str(record_id))
    if ref in _items(found, AccountMergeItem.Phase.MOVED) | _items(found, "kept"):
        # The history decides these links.
        raise errors.business_rule("SPLIT_LINK_KNOWN")
    link = next(
        (x for x in userlinks.links_of(found.main_user_id) if x.ref == ref and not x.blocking),
        None,
    )
    if link is None:
        raise errors.not_found()
    now = timezone.now()
    previous = AccountSplitAssignment.objects.filter(
        merge=found, module=module, kind=kind, record_id=record_id, replaced_at__isnull=True
    ).first()
    if previous is not None:
        previous.replaced_at = now
        previous.save(update_fields=["replaced_at"])
    choice = AccountSplitAssignment.objects.create(
        merge=found,
        module=module,
        kind=kind,
        record_id=record_id,
        label=link.label,
        target=target,
        decided_by_user_id=ctx.staff.user.id,
        decided_at=now,
    )
    found.version += 1
    found.save(update_fields=["version"])
    ctx.journal(
        "account.split_link_assigned",
        object_type="account_merge",
        object_id=found.id,
        before={"target": previous.target} if previous else None,
        after={**userlinks.payload(link), "target": target},
    )
    return choice


def complete_split(ctx: StaffContext, found: AccountMerge) -> AccountMerge:
    if found.state != State.SPLITTING:
        raise errors.business_rule("SPLIT_NOT_STARTED")
    _lock_pair(found.main_user_id, found.secondary_user_id)
    plan = split_plan(found)
    if plan["unassigned"]:
        raise errors.business_rule("SPLIT_UNASSIGNED", count=plan["unassigned"])
    if plan["conflicts"]:
        raise errors.business_rule("SPLIT_CONFLICTS", count=len(plan["conflicts"]))
    links = plan["_restored"] + plan["_to_secondary"]
    accounts.split_account(found.secondary_user_id, found.main_user_id)
    moved = _move(links, found.main_user_id, found.secondary_user_id)
    now = timezone.now()
    found.state = State.SPLIT
    found.split_at = now
    found.split_by_user_id = ctx.staff.user.id
    found.version += 1
    found.save(update_fields=["state", "split_at", "split_by_user_id", "version"])
    ctx.journal(
        "account.split",
        object_type="account_merge",
        object_id=found.id,
        target_user_id=found.secondary_user_id,
        reason=found.split_reason,
        after={
            "restored": plan["restored"],
            "assigned_to_secondary": [userlinks.payload(x) for x in plan["_to_secondary"]],
            "assigned_to_main": [x for x in plan["new"] if x["assigned_to"] == "main"],
            "moved_count": moved,
        },
    )
    return found


def payload(found: AccountMerge, *, detail: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(found.id),
        "state": found.state,
        "main": {
            "public_id": found.main_user.public_id,
            "name": directory.user_summary(found.main_user)["name"],
        },
        "secondary": {
            "public_id": found.secondary_user.public_id,
            "name": directory.user_summary(found.secondary_user)["name"],
        },
        "reason": found.reason,
        "ticket_id": str(found.ticket_id) if found.ticket_id else None,
        "merged_at": found.merged_at.isoformat(),
        "split_reason": found.split_reason,
        "split_started_at": found.split_started_at.isoformat() if found.split_started_at else None,
        "split_at": found.split_at.isoformat() if found.split_at else None,
        "version": found.version,
    }
    if detail:
        body["history"] = [
            {
                "phase": i.phase,
                "module": i.module,
                "kind": i.kind,
                "record_id": str(i.record_id),
                "label": i.label,
            }
            for i in AccountMergeItem.objects.filter(merge=found).order_by("phase", "module")
        ]
        if found.state == State.SPLITTING:
            body["split"] = public_plan(split_plan(found))
    return body
