"""HTTP of the Telegram bot: the updates Telegram sends, and the block «Telegram» of the profile."""

import hmac
import json
import logging
from typing import Any

from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from c4rsys.accounts.middleware import require_user
from c4rsys.platform import errors
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.platform.http import idempotency_key, respond
from c4rsys.platform.middleware import get_request_id
from c4rsys.telegram import api, bot, links
from c4rsys.telegram.models import TelegramUpdate

logger = logging.getLogger(__name__)
SECRET_HEADER = "HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"  # noqa: S105 -- the name of a header


@csrf_exempt
@require_POST
def webhook(request: HttpRequest) -> HttpResponse:
    """An update from Telegram. It is known by the secret given at `setWebhook`; an update taken
    once is not taken again. The answer is always «taken»: a failure is in the log, and Telegram
    does not send the same update for hours."""
    if not api.configured() or not hmac.compare_digest(
        str(request.META.get(SECRET_HEADER, "")), api.secret()
    ):
        raise errors.not_found()
    try:
        update: Any = json.loads(request.body)
        update_id = int(update["update_id"])
    except (ValueError, KeyError, TypeError):
        return HttpResponse(status=200)
    try:
        TelegramUpdate.objects.create(update_id=update_id)
    except IntegrityError:
        return HttpResponse(status=200)
    try:
        bot.handle(update)
    except Exception:
        logger.exception("The Telegram update %s failed", update_id)
    return HttpResponse(status=200)


def _state(user_id: Any) -> dict[str, Any]:
    link = links.link_of(user_id)
    return {
        "configured": api.configured(),
        "linked": link is not None,
        "phone": link.phone if link else "",
        "username": link.username if link else "",
    }


@require_GET
def me_telegram(request: HttpRequest) -> JsonResponse:
    user, _ = require_user(request)
    return JsonResponse(_state(user.id))


@require_POST
def me_telegram_link(request: HttpRequest) -> JsonResponse:
    """A one-time link to the bot for the signed-in person."""
    user, session = require_user(request)
    if not api.configured():
        raise errors.business_rule("TELEGRAM_NOT_CONFIGURED")
    name = api.bot_name()

    def handler(ctx: CommandContext) -> CommandResult:
        return CommandResult(
            200, {"url": f"https://t.me/{name}?start={links.issue_code(ctx, user)}"}
        )

    return respond(
        run_command(
            command="telegram.link_code",
            actor=Actor(user_id=user.id, session_id=session.id),
            idempotency_key=idempotency_key(request),
            payload={},
            handler=handler,
            request_id=get_request_id(request),
        )
    )


@require_POST
def me_telegram_unlink(request: HttpRequest) -> JsonResponse:
    user, session = require_user(request)

    def handler(ctx: CommandContext) -> CommandResult:
        links.unbind(ctx, user)
        return CommandResult(200, _state(user.id))

    return respond(
        run_command(
            command="telegram.unlink",
            actor=Actor(user_id=user.id, session_id=session.id),
            idempotency_key=idempotency_key(request),
            payload={},
            handler=handler,
            request_id=get_request_id(request),
        )
    )
