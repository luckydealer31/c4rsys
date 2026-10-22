from collections.abc import Iterator
from unittest import mock

import pytest

from tests.accounts.helpers import CODE


@pytest.fixture(autouse=True)
def fixed_code() -> Iterator[None]:
    with mock.patch("c4rsys.accounts.security.generate_code", return_value=CODE):
        yield
