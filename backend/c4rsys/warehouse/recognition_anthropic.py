"""Claude of Anthropic as the service that reads the documents of suppliers (customer's decision
of 21.11.2026; the socket is recognition.py). The platform pays for it: the key is the platform's,
`ANTHROPIC_API_KEY` of the environment, and without it the service is not registered at all.

The file goes to Anthropic as it is — a picture or a PDF — with `recognition.INSTRUCTION`; the
answer is held to `recognition.SCHEMA` by the structured output of the API and is checked once
more by `recognition.parse`."""

import base64
import json
import os
from typing import TYPE_CHECKING, Any, cast

from django.conf import settings

from c4rsys.warehouse import recognition

if TYPE_CHECKING:
    from anthropic.types import MessageParam, OutputConfigParam

NAME = "anthropic"
IMAGES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
PDF = "application/pdf"
# A request is answered within a web request of the employee: gunicorn waits 120 s.
TIMEOUT_SECONDS = 90.0
MAX_TOKENS = 16000


def _schema(node: Any) -> Any:
    """`recognition.SCHEMA` in the form the structured output takes: a list of types becomes
    `anyOf`."""
    if isinstance(node, list):
        return [_schema(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: _schema(v) for k, v in node.items()}
    if isinstance(node.get("type"), list):
        del out["type"]
        out["anyOf"] = [{"type": kind} for kind in node["type"]]
    return out


def _file(content: bytes, content_type: str) -> dict[str, Any]:
    if content_type in IMAGES:
        kind = "image"
    elif content_type == PDF:
        kind = "document"
    else:
        raise ValueError(f"a file of the type {content_type} is not read")
    data = base64.standard_b64encode(content).decode("ascii")
    return {"type": kind, "source": {"type": "base64", "media_type": content_type, "data": data}}


def read(content: bytes, content_type: str) -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=1)
    message = {
        "role": "user",
        "content": [_file(content, content_type), {"type": "text", "text": "Read this document."}],
    }
    output = {"format": {"type": "json_schema", "schema": _schema(recognition.SCHEMA)}}
    response = client.messages.create(
        model=settings.C4R_ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        system=recognition.INSTRUCTION,
        messages=[cast("MessageParam", message)],
        output_config=cast("OutputConfigParam", output),
    )
    # A refusal or an answer cut by the limit does not hold to the schema.
    if response.stop_reason != "end_turn":
        raise ValueError(f"the answer was stopped: {response.stop_reason}")
    text = next(block.text for block in response.content if block.type == "text")
    answer: dict[str, Any] = json.loads(text)
    return answer


def register() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        recognition.register(NAME, read)
