"""Checks the internal interface adds to the sign-in and to every command."""

from c4rsys.accounts.models import UserAccount, UserSession
from c4rsys.accounts.services import Client
from c4rsys.backoffice import core
from c4rsys.platform import errors, sites, suspension
from c4rsys.platform.commands import Actor


def login_check(user: UserAccount, client: Client) -> None:
    block = suspension.user_block(user.id)
    if block is not None:
        # § 24.2.2: with the reason that may be disclosed and the support contact.
        raise errors.ApiError("USER_BLOCKED", status=403, params=block)
    if client.site == sites.STAFF and core.active_member(user.id) is None:
        # § 24.1.1: a personal account alone gives no access to the internal interface.
        raise errors.ApiError("STAFF_ACCESS_DENIED", status=403)


def signed_in(user: UserAccount, session: UserSession, client: Client) -> None:
    if client.site != sites.STAFF:
        return
    member = core.active_member(user.id)
    if member is None:
        return
    staff = core.staff_of(member, user, session)
    core._entry(staff, "staff.signed_in", object_type="user_session", object_id=session.id).save(
        force_insert=True
    )


def company_guard(command: str, actor: Actor) -> None:
    """§ 24.2.2: no new operations of a company blocked by the system. The row of the company is
    read with FOR KEY SHARE: a block (FOR UPDATE on it) waits for commands in flight, and a
    command started after it sees the block."""
    if actor.company_id is None:
        return
    from c4rsys.companies import services as companies

    companies.share_lock_company(actor.company_id)
    block = suspension.company_block(actor.company_id)
    if block is not None:
        raise errors.ApiError("COMPANY_BLOCKED", status=403, params=block)
