"""Claude as the service that reads the documents of suppliers (customer's decision of
21.11.2026). Nothing is sent anywhere: the client of the SDK is replaced."""

import base64
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from c4rsys.platform import errors
from c4rsys.warehouse import recognition, recognition_anthropic

ANSWER = {
    "supplier_name": "Phuket Parts Co",
    "document_date": "2026-11-12",
    "document_number": "INV-77",
    "currency": "THB",
    "lines": [{"name": "Brake pads", "article": "BP-100", "quantity": "2", "unit_cost": "1250.50"}],
}


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch, settings: Any) -> Iterator[dict[str, Any]]:
    """The client of the SDK: it keeps what it was asked and answers with `box["response"]`."""
    box: dict[str, Any] = {
        "response": SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(ANSWER))],
        )
    }

    class Messages:
        def create(self, **asked: Any) -> Any:
            box["asked"] = asked
            if isinstance(box["response"], Exception):
                raise box["response"]
            return box["response"]

    def client(**options: Any) -> Any:
        box["options"] = options
        return SimpleNamespace(messages=Messages())

    monkeypatch.setattr(anthropic, "Anthropic", client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    recognition_anthropic.register()
    settings.C4R_INVOICE_RECOGNITION = "anthropic"
    yield box
    recognition._recognizers.pop("anthropic", None)


def test_a_picture_is_sent_and_the_answer_is_read(claude: dict[str, Any]) -> None:
    document = recognition.read(b"picture", "image/jpeg")
    assert document.supplier_name == "Phuket Parts Co"
    assert [(line.article, str(line.unit_cost)) for line in document.lines] == [
        ("BP-100", "1250.50")
    ]
    asked = claude["asked"]
    assert asked["model"] == "claude-opus-5-5"
    assert asked["system"] == recognition.INSTRUCTION
    file = asked["messages"][0]["content"][0]
    assert file["type"] == "image"
    assert file["source"]["media_type"] == "image/jpeg"
    assert base64.standard_b64decode(file["source"]["data"]) == b"picture"
    # The answer is held to the schema, and the schema has no list of types left.
    schema = asked["output_config"]["format"]["schema"]
    assert asked["output_config"]["format"]["type"] == "json_schema"
    assert schema["properties"]["supplier_name"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}]
    }
    assert '"type": [' not in json.dumps(schema)
    assert schema["properties"]["lines"]["items"]["additionalProperties"] is False
    assert claude["options"]["timeout"] < 120


def test_a_pdf_goes_as_a_document(claude: dict[str, Any]) -> None:
    recognition.read(b"%PDF-1.7", "application/pdf")
    file = claude["asked"]["messages"][0]["content"][0]
    assert (file["type"], file["source"]["media_type"]) == ("document", "application/pdf")


@pytest.mark.parametrize("reason", ["refusal", "max_tokens"])
def test_a_stopped_answer_is_a_failure(claude: dict[str, Any], reason: str) -> None:
    claude["response"].stop_reason = reason
    with pytest.raises(errors.ApiError) as failure:
        recognition.read(b"picture", "image/png")
    assert failure.value.code == "RECOGNITION_FAILED"


def test_a_failure_of_the_service_and_an_unknown_file(claude: dict[str, Any]) -> None:
    with pytest.raises(errors.ApiError) as failure:
        recognition.read(b"PK", "application/zip")
    assert failure.value.code == "RECOGNITION_FAILED"
    assert "asked" not in claude
    claude["response"] = RuntimeError("no network")
    with pytest.raises(errors.ApiError) as failure:
        recognition.read(b"picture", "image/png")
    assert failure.value.code == "RECOGNITION_FAILED"


def test_without_the_key_the_service_is_not_registered(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    recognition._recognizers.pop("anthropic", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    recognition_anthropic.register()
    settings.C4R_INVOICE_RECOGNITION = "anthropic"
    assert not recognition.configured()
