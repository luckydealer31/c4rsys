"""Makes and models of the catalog named in a text (customer's decision of 20.11.2026)."""

import pytest
from django.test import Client

from tests.fleet.conftest import *  # noqa: F403

pytestmark = pytest.mark.django_db


def _match(owner: Client, text: str) -> list[tuple[str, str | None]]:
    response = owner.get("/api/catalog/vehicle-match", {"text": text})
    assert response.status_code == 200, response.content
    return [(m["make"], m["model"]) for m in response.json()["matches"]]


def test_a_make_and_its_model_are_found_in_a_name(owner: Client) -> None:  # noqa: F811
    assert _match(owner, "дверь правая ford focus") == [("FORD", "FORD_FOCUS")]
    # Cyrillic names and capitals are the same make and model.
    assert _match(owner, "Дверь правая Форд Фокус") == [("FORD", "FORD_FOCUS")]
    first = owner.get("/api/catalog/vehicle-match", {"text": "ford focus"}).json()["matches"][0]
    assert first["type"] == "passenger_car"
    assert (first["make_name"], first["model_name"]) == ("Ford", "Focus")


def test_the_longest_name_of_a_model_wins(owner: Client) -> None:  # noqa: F811
    assert _match(owner, "бампер ford focus rs") == [("FORD", "FORD_FOCUS_RS")]


def test_a_make_without_a_model_is_offered_for_all_models(owner: Client) -> None:  # noqa: F811
    assert _match(owner, "фильтр масляный toyota") == [("TOYOTA", None)]


def test_a_model_without_its_make_and_a_part_of_a_word_are_not_found(owner: Client) -> None:  # noqa: F811
    assert _match(owner, "дверь правая focus") == []
    assert _match(owner, "fordzilla") == []
    assert _match(owner, "") == []
