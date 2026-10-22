from typing import Any

from django.test import Client

from tests.accounts.helpers import post, register


def named_user(client: Client, email: str = "owner@example.com") -> None:
    register(client, email)
    response = post(
        client,
        "/api/me/personal-data",
        {"first_name": "Anna", "last_name": "Petrova"},
        headers={"Idempotency-Key": "name-1"},
    )
    assert response.status_code == 200, response.content


def create_company(client: Client, key: str = "c-1", **data: Any) -> Any:
    body = {"display_name": "Sunrise Rent", **data}
    return post(client, "/api/companies", body, headers={"Idempotency-Key": key})


def patch_company(client: Client, cmp: str, data: dict[str, Any], key: str = "p-1") -> Any:
    return client.patch(
        f"/api/c/{cmp}",
        data=data,
        content_type="application/json",
        headers={"Idempotency-Key": key},
    )
