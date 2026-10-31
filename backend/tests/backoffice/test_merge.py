"""Merge and split of global accounts by the support (ТЗ § 24.1.8)."""

from typing import Any
from unittest import mock

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from c4rsys.accounts import services as accounts
from c4rsys.accounts.models import LoginContact, UserAccount, UserSession
from c4rsys.backoffice.models import AccountMerge, SystemJournalEntry
from c4rsys.companies.models import Company, Employment, OwnershipShare
from tests.accounts.helpers import login
from tests.backoffice.helpers import Member, err, member, sp
from tests.companies.helpers import hire, key
from tests.companies.test_ownership import company, user

pytestmark = pytest.mark.django_db


def _usr(client: Client) -> str:
    public_id: str = client.get("/api/me").json()["user"]["public_id"]
    return public_id


def _preview(m: Member, main: str, secondary: str) -> Any:
    return sp(m, "merges/preview", {"main": main, "secondary": secondary})


def _merge(m: Member, main: str, secondary: str, **extra: Any) -> Any:
    body = {"main": main, "secondary": secondary, "reason": "Same passport, same phone", **extra}
    return sp(m, "merges", body)


@pytest.fixture
def two(owner: Client) -> tuple[str, Client, str]:
    """The main account owns its company; the second one works in another company."""
    company(owner, "Main Rent")
    other, _ = user("other@example.com")
    cmp_y = company(other, "Other Rent")
    second, _ = hire(other, cmp_y, "second@example.com", [])
    return _usr(owner), second, cmp_y


def test_merge_needs_its_own_permission(two: tuple[str, Client, str]) -> None:
    main, second, _ = two
    verifier = member("verify@c4rsys.test", ["verification"])
    response = _preview(verifier, main, _usr(second))
    assert response.status_code == 403
    assert err(response) == "PERMISSION_DENIED"


def test_preview_shows_what_moves(support: Member, two: tuple[str, Client, str]) -> None:
    main, second, cmp_y = two
    response = _preview(support, main, _usr(second))
    assert response.status_code == 200, response.content
    preview = response.json()["preview"]
    kinds = {(x["module"], x["kind"]) for x in preview["moving"]}
    assert ("accounts", "login_contact") in kinds
    assert ("companies", "employment") in kinds
    employment = next(x for x in preview["moving"] if x["kind"] == "employment")
    assert employment["label"]["company"] == cmp_y
    # The contacts are masked in the preview (§ 24.4.3).
    contact = next(x for x in preview["moving"] if x["kind"] == "login_contact")
    assert contact["label"]["value"] == "s***@example.com"
    assert preview["conflicts"] == []
    assert SystemJournalEntry.objects.filter(action="account.merge_previewed").exists()


def test_merge_moves_links_and_the_second_account_refers_to_the_main_one(
    support: Member, owner: Client, two: tuple[str, Client, str]
) -> None:
    main, second, cmp_y = two
    secondary = _usr(second)
    # The second account made a company of its own: its record of the creator is not rewritten.
    own = company(second, "Second Rent")
    preview = _preview(support, main, secondary).json()["preview"]
    expected = [x["record_id"] for x in preview["moving"]]
    response = _merge(support, main, secondary, expected=expected)
    assert response.status_code == 201, response.content
    main_user = UserAccount.objects.get(public_id=main)
    second_user = UserAccount.objects.get(public_id=secondary)
    assert second_user.state == UserAccount.State.MERGED
    assert second_user.merged_into_id == main_user.id
    # The links moved; the business record keeps the account that made it.
    assert Employment.objects.filter(company__public_id=cmp_y, user=main_user).exists()
    assert OwnershipShare.objects.filter(
        company__public_id=own, owner_user=main_user, valid_to__isnull=True
    ).exists()
    assert Company.objects.get(public_id=own).created_by_id == second_user.id
    # The second account no longer signs in; its sessions ended.
    assert not UserSession.objects.filter(user=second_user, ended_at__isnull=True).exists()
    assert second.get("/api/me").status_code == 401
    # Its ID refers to the main account (an invitation by the ID reaches the main one).
    found = accounts.find_active_user(secondary)
    assert found is not None and found.id == main_user.id
    # Its email signs in to the main account.
    again = Client()
    assert login(again, "second@example.com").status_code == 200
    assert _usr(again) == main
    entry = SystemJournalEntry.objects.get(action="account.merged")
    assert entry.reason == "Same passport, same phone"
    assert AccountMerge.objects.get().items.filter(phase="moved").count() == len(expected)


def test_conflicts_are_shown_and_refuse_the_merge(
    support: Member, owner: Client, two: tuple[str, Client, str]
) -> None:
    main, second, cmp_y = two
    other = Client()
    login(other, "other@example.com")
    # The main account also works in the company of the second one.
    invitation = sp_company_invite(other, cmp_y, main)
    accept = owner.post(
        f"/api/invitations/{invitation}/accept", {}, content_type="application/json", headers=key()
    )
    assert accept.status_code == 200, accept.content
    preview = _preview(support, main, _usr(second)).json()["preview"]
    assert [c["key"] for c in preview["conflicts"]] == [
        f"employment:{Company.objects.get(public_id=cmp_y).id}"
    ]
    response = _merge(support, main, _usr(second))
    assert response.status_code == 422
    assert err(response) == "MERGE_CONFLICTS"
    assert UserAccount.objects.get(public_id=_usr(second)).state == UserAccount.State.ACTIVE


def sp_company_invite(client: Client, cmp: str, target: str) -> str:
    body = {"target": target, "position_name": "Staff", "rights": []}
    response = client.post(
        f"/api/c/{cmp}/employees/invitations", body, content_type="application/json", headers=key()
    )
    assert response.status_code == 201, response.content
    invitation_id: str = response.json()["invitation"]["id"]
    return invitation_id


def test_a_changed_composition_refuses_the_merge(
    support: Member, two: tuple[str, Client, str]
) -> None:
    main, second, _ = two
    response = _merge(
        support, main, _usr(second), expected=["00000000-0000-0000-0000-000000000000"]
    )
    assert response.status_code == 422
    assert err(response) == "MERGE_CHANGED"


def test_without_its_journal_the_merge_is_not_performed(
    support: Member, two: tuple[str, Client, str]
) -> None:
    """§ 24.4.5: if the mandatory entry is not written, the action is not performed."""
    main, second, _ = two
    with mock.patch.object(
        SystemJournalEntry.objects, "bulk_create", side_effect=RuntimeError("disk")
    ):
        response = _merge(support, main, _usr(second))
    assert response.status_code == 500
    assert err(response) == "OPERATION_NOT_POSTED"
    assert not AccountMerge.objects.exists()
    assert UserAccount.objects.get(public_id=_usr(second)).state == UserAccount.State.ACTIVE


def test_the_system_journal_is_append_only(support: Member, two: tuple[str, Client, str]) -> None:
    main, second, _ = two
    _merge(support, main, _usr(second))
    entry = SystemJournalEntry.objects.get(action="account.merged")
    with pytest.raises(DatabaseError), transaction.atomic():
        SystemJournalEntry.objects.filter(pk=entry.pk).update(reason="changed")
    with pytest.raises(DatabaseError), transaction.atomic():
        SystemJournalEntry.objects.filter(pk=entry.pk).delete()


def test_a_staff_member_is_not_merged_away(support: Member, owner: Client) -> None:
    preview = _preview(support, _usr(owner), support.public_id).json()["preview"]
    assert [c["key"] for c in preview["conflicts"]] == ["staff_member"]


def test_split_returns_the_history_and_waits_for_new_links(
    support: Member, owner: Client, two: tuple[str, Client, str]
) -> None:
    main, second, cmp_y = two
    secondary = _usr(second)
    assert _merge(support, main, secondary).status_code == 201
    found = AccountMerge.objects.get()
    # After the merge the main account got a new place of work.
    boss, _ = user("boss@example.com")
    cmp_z = company(boss, "Zeta Rent")
    invitation = sp_company_invite(boss, cmp_z, main)
    accept = owner.post(
        f"/api/invitations/{invitation}/accept", {}, content_type="application/json", headers=key()
    )
    assert accept.status_code == 200, accept.content

    started = sp(support, f"merges/{found.id}/split/start", {"reason": "Two different people"})
    assert started.status_code == 200, started.content
    split = started.json()["merge"]["split"]
    assert {x["kind"] for x in split["restored"]} == {"login_contact", "employment"}
    assert [x["label"]["company"] for x in split["new"]] == [cmp_z]
    assert split["unassigned"] == 1

    refused = sp(support, f"merges/{found.id}/split/complete")
    assert refused.status_code == 422
    assert err(refused) == "SPLIT_UNASSIGNED"

    new = split["new"][0]
    # A link known by the history cannot be assigned by hand.
    known = split["restored"][0]
    wrong = sp(
        support,
        f"merges/{found.id}/split/assign",
        {**{k: known[k] for k in ("module", "kind", "record_id")}, "target": "main"},
    )
    assert err(wrong) == "SPLIT_LINK_KNOWN"
    assigned = sp(
        support,
        f"merges/{found.id}/split/assign",
        {**{k: new[k] for k in ("module", "kind", "record_id")}, "target": "secondary"},
    )
    assert assigned.status_code == 200, assigned.content
    done = sp(support, f"merges/{found.id}/split/complete")
    assert done.status_code == 200, done.content

    second_user = UserAccount.objects.get(public_id=secondary)
    assert second_user.state == UserAccount.State.ACTIVE
    assert second_user.merged_into_id is None
    assert LoginContact.objects.filter(user=second_user, value="second@example.com").exists()
    assert Employment.objects.filter(company__public_id=cmp_y, user=second_user).exists()
    assert Employment.objects.filter(company__public_id=cmp_z, user=second_user).exists()
    assert not Employment.objects.filter(company__public_id=cmp_y, user__public_id=main).exists()
    again = Client()
    assert login(again, "second@example.com").status_code == 200
    assert _usr(again) == secondary
    assert SystemJournalEntry.objects.filter(action="account.split").exists()
    assert AccountMerge.objects.get().state == AccountMerge.State.SPLIT
