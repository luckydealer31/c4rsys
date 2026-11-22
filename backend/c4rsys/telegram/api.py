"""The Bot API of Telegram: the calls the bot makes. The token is the platform's,
`TELEGRAM_BOT_TOKEN` of the environment; without it the bot is off. The tests put their own
functions into `transport` and `fetch`."""

import hashlib
import json
import urllib.request
from collections.abc import Callable
from typing import Any

from django.conf import settings

TIMEOUT_SECONDS = 20
# The largest file the Bot API gives out is 20 MB; a paper is read up to the size of a document.
BASE = "https://api.telegram.org"


def configured() -> bool:
    return bool(settings.C4R_TELEGRAM_BOT_TOKEN)


def secret() -> str:
    """What Telegram sends with every update, so that nobody else can speak for it."""
    raw = f"{settings.C4R_TELEGRAM_BOT_TOKEN}:{settings.SECRET_KEY}".encode()
    return hashlib.sha256(raw).hexdigest()[:40]


def _http(method: str, params: dict[str, Any]) -> Any:
    request = urllib.request.Request(  # noqa: S310 -- the address is the constant of Telegram
        f"{BASE}/bot{settings.C4R_TELEGRAM_BOT_TOKEN}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        answer = json.loads(response.read())
    if not answer.get("ok"):
        raise RuntimeError(f"Telegram refused {method}: {answer.get('description')}")
    return answer.get("result")


def _download(path: str) -> bytes:
    url = f"{BASE}/file/bot{settings.C4R_TELEGRAM_BOT_TOKEN}/{path}"
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        content: bytes = response.read()
    return content


transport: Callable[[str, dict[str, Any]], Any] = _http
fetch: Callable[[str], bytes] = _download
_name: dict[str, str] = {}


def call(method: str, **params: Any) -> Any:
    return transport(method, params)


def send(chat_id: int, text: str, markup: dict[str, Any] | None = None) -> None:
    params: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if markup is not None:
        params["reply_markup"] = markup
    call("sendMessage", **params)


def download(file_id: str) -> bytes:
    info = call("getFile", file_id=file_id)
    return fetch(str(info["file_path"]))


def bot_name() -> str:
    """The username of the bot, asked once."""
    token = settings.C4R_TELEGRAM_BOT_TOKEN
    if token not in _name:
        _name[token] = str(call("getMe")["username"])
    return _name[token]
