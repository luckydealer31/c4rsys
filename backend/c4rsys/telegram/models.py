from django.db import models
from django.utils import timezone

from c4rsys.accounts.models import UserAccount
from c4rsys.companies.models import Company
from c4rsys.platform.ids import uuid7


class TelegramLink(models.Model):
    """A Telegram account bound to a profile (customer's decision of 22.11.2026). The phone is
    the one Telegram itself gave with «share my number»: it is confirmed by Telegram, not typed.
    One profile — one Telegram account, and the other way round."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.OneToOneField(UserAccount, on_delete=models.PROTECT, related_name="telegram")
    telegram_user_id = models.BigIntegerField(unique=True)
    chat_id = models.BigIntegerField()
    username = models.CharField(max_length=64, blank=True, default="")
    phone = models.CharField(max_length=20)
    linked_at = models.DateTimeField(default=timezone.now)
    # The company chosen in the chat, and where the talk stands: technical data of the chat.
    company = models.ForeignKey(
        Company, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    state = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "telegram_link"

    def __str__(self) -> str:
        return f"telegram:{self.telegram_user_id}"


class TelegramLinkCode(models.Model):
    """A one-time code of the link «connect Telegram» made in the profile. `telegram_user_id` is
    who opened the bot by it and is asked for the number."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(UserAccount, on_delete=models.CASCADE, related_name="+")
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    telegram_user_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "telegram_link_code"

    def __str__(self) -> str:
        return f"telegram-code:{self.id}"


class TelegramUpdate(models.Model):
    """An update already taken: Telegram sends one again when the answer was slow."""

    update_id = models.BigIntegerField(primary_key=True)
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "telegram_update"

    def __str__(self) -> str:
        return f"telegram-update:{self.update_id}"
