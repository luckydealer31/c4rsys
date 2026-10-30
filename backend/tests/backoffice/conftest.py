from collections.abc import Iterator
from unittest import mock

import pytest
from django.test import Client

from tests.accounts.helpers import CODE
from tests.backoffice.helpers import Member, member
from tests.companies.helpers import named_user
from tests.rentals.scenario import Scenario, build


@pytest.fixture(autouse=True)
def fixed_code() -> Iterator[None]:
    with (
        mock.patch("c4rsys.accounts.security.generate_code", return_value=CODE),
        mock.patch("c4rsys.accounts.otp.check_send_allowed"),
    ):
        yield


@pytest.fixture
def owner(client: Client) -> Client:
    named_user(client)
    return client


@pytest.fixture
def s(owner: Client) -> Scenario:
    return build(owner)


@pytest.fixture
def admin() -> Member:
    return member("admin@c4rsys.test", ["platform_admin"], unit="Platform")


@pytest.fixture
def support() -> Member:
    return member("support@c4rsys.test", ["support"], unit="Support")
