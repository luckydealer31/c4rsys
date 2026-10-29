from collections.abc import Iterator
from unittest import mock

import pytest
from django.test import Client

from tests.accounts.helpers import CODE
from tests.companies.helpers import named_user
from tests.rentals.scenario import Scenario, build


@pytest.fixture(autouse=True)
def fixed_code() -> Iterator[None]:
    with mock.patch("c4rsys.accounts.security.generate_code", return_value=CODE):
        yield


@pytest.fixture
def owner(client: Client) -> Client:
    named_user(client)
    return client


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)
