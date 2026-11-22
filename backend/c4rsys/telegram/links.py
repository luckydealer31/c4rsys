"""Binding a Telegram account to a profile (customer's decision of 22.11.2026). The profile makes
a one-time link to the bot; who opens the bot by it shares the phone number, which Telegram
itself gives and confirms; with that the Telegram account is bound and the number is known as
confirmed. A link is made only by a signed-in person, and the number is never typed."""

import re
import secrets
from datetime import timedelta
from uuid import UUID

from django.utils import timezone

from c4rsys.accounts import security
from c4rsys.accounts.models import UserAccount
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext
from c4rsys.telegram.models import TelegramLink, TelegramLinkCode

# PROPOSAL: how long a link of the profile lives.
CODE_LIFETIME = timedelta(minutes=15)


def link_of(user_id: UUID) -> TelegramLink | None:
    return TelegramLink.objects.filter(user_id=user_id).first()


def link_of_telegram(telegram_user_id: int) -> TelegramLink | None:
    return (
        TelegramLink.objects.select_related("user", "company")
        .filter(telegram_user_id=telegram_user_id)
        .first()
    )


def issue_code(ctx: CommandContext, user: UserAccount) -> str:
    """A new one-time code of the link; the earlier unused ones stop working."""
    TelegramLinkCode.objects.filter(user=user, used_at__isnull=True).delete()
    code = secrets.token_urlsafe(24)
    made = TelegramLinkCode.objects.create(
        user=user, code_hash=security.token_hash(code), expires_at=timezone.now() + CODE_LIFETIME
    )
    ctx.audit("telegram.link_code_issued", object_type="telegram_link_code", object_id=made.id)
    return code


def open_code(code: str, telegram_user_id: int) -> bool:
    """Who opened the bot by the link is remembered until the number is shared. Technical data of
    the chat: nothing of the profile changes yet."""
    changed = TelegramLinkCode.objects.filter(
        code_hash=security.token_hash(code), used_at__isnull=True, expires_at__gt=timezone.now()
    ).update(telegram_user_id=telegram_user_id)
    return changed == 1


def pending(telegram_user_id: int) -> TelegramLinkCode | None:
    return (
        TelegramLinkCode.objects.select_related("user")
        .filter(
            telegram_user_id=telegram_user_id, used_at__isnull=True, expires_at__gt=timezone.now()
        )
        .order_by("-expires_at")
        .first()
    )


def bind(
    ctx: CommandContext,
    code_id: UUID,
    *,
    telegram_user_id: int,
    chat_id: int,
    username: str,
    phone: str,
) -> TelegramLink:
    """The Telegram account is bound to the profile the code was made in. A Telegram account of
    another profile is refused; the earlier Telegram account of this profile is replaced."""
    code = TelegramLinkCode.objects.select_for_update().filter(pk=code_id).first()
    if code is None or code.used_at is not None or code.expires_at <= timezone.now():
        raise errors.business_rule("TELEGRAM_LINK_EXPIRED")
    if TelegramLink.objects.filter(telegram_user_id=telegram_user_id).exclude(user=code.user):
        raise errors.business_rule("TELEGRAM_TAKEN")
    digits = re.sub(r"\D", "", phone)[:19]
    before = TelegramLink.objects.filter(user=code.user).first()
    old = {"telegram_user_id": before.telegram_user_id, "phone": before.phone} if before else None
    if before is not None:
        before.delete()
    link = TelegramLink.objects.create(
        user=code.user,
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        username=username[:64],
        phone=f"+{digits}",
    )
    code.used_at = timezone.now()
    code.save(update_fields=["used_at"])
    ctx.audit(
        "telegram.linked",
        object_type="telegram_link",
        object_id=link.id,
        before=old,
        after={"telegram_user_id": telegram_user_id, "phone": link.phone},
    )
    return link


def unbind(ctx: CommandContext, user: UserAccount) -> None:
    link = TelegramLink.objects.select_for_update().filter(user=user).first()
    if link is None:
        raise errors.not_found()
    before = {"telegram_user_id": link.telegram_user_id, "phone": link.phone}
    link_id = link.id
    link.delete()
    ctx.audit("telegram.unlinked", object_type="telegram_link", object_id=link_id, before=before)
