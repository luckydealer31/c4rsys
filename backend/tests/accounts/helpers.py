from typing import Any

from django.test import Client

CODE = "123456"
PASSWORD = "Granite-Harbor-2026"


def post(
    client: Client,
    path: str,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        path, data=data or {}, content_type="application/json", headers=headers or {}
    )


def register(client: Client, email: str = "owner@example.com") -> dict[str, Any]:
    flow = post(client, "/api/auth/register/start", {"email": email}).json()["flow"]
    verified = post(client, "/api/auth/register/verify", {"flow": flow, "code": CODE})
    assert verified.status_code == 200
    response = post(client, "/api/auth/register/complete", {"flow": flow, "password": PASSWORD})
    assert response.status_code == 201, response.content
    body: dict[str, Any] = response.json()
    return body


def login(client: Client, email: str = "owner@example.com", password: str = PASSWORD) -> Any:
    flow = post(client, "/api/auth/login/start", {"email": email}).json()["flow"]
    assert post(client, "/api/auth/login/verify", {"flow": flow, "code": CODE}).status_code == 200
    return post(client, "/api/auth/login/complete", {"flow": flow, "password": password})
