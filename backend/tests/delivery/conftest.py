from collections.abc import Iterator
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import Client

from c4rsys.delivery import services as delivery_module
from c4rsys.rentals import bookings as bookings_module
from c4rsys.rentals import issue as issue_module
from c4rsys.rentals import rentals as rentals_module
from c4rsys.rentals import views as rental_views_module
from tests.accounts.helpers import CODE
from tests.companies.helpers import named_user
from tests.rentals.scenario import NOW, Scenario, add_location, build


@pytest.fixture(autouse=True)
def fixed_code() -> Iterator[None]:
    with mock.patch("c4rsys.accounts.security.generate_code", return_value=CODE):
        yield


@pytest.fixture
def owner(client: Client) -> Client:
    named_user(client)
    return client


@pytest.fixture(autouse=True)
def frozen() -> Iterator[None]:
    clock = SimpleNamespace(now=lambda: NOW)
    with (
        mock.patch.object(delivery_module, "timezone", clock),
        mock.patch.object(issue_module, "timezone", clock),
        mock.patch.object(bookings_module, "timezone", clock),
        mock.patch.object(rentals_module, "timezone", clock),
        mock.patch.object(rental_views_module, "timezone", clock),
    ):
        yield


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


@pytest.fixture
def patong(owner: Client, s: Scenario) -> str:
    return add_location(owner, s.cmp, "Patong")
