from typing import Any

from django.test import Client

from tests.accounts.helpers import CODE, post, register

BROAD = {"company_admin", "employee.rights.manage"}


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


_keys = iter(range(10**6))


def key() -> dict[str, str]:
    """A fresh Idempotency-Key header."""
    return {"Idempotency-Key": f"h-{next(_keys)}"}


def broad_code(client: Client) -> dict[str, str]:
    """The one-time code of the director granting especially broad rights (§ 2.1.1, block 14);
    needs the mocked code of the fixture `fixed_code`."""
    response = post(client, "/api/me/confirmations", {"action": "grant_broad_rights"})
    assert response.status_code == 200, response.content
    return {"flow": response.json()["flow"], "code": CODE}


def is_broad(rights: list[dict[str, Any]]) -> bool:
    return any(r.get("set") in BROAD or r.get("permission") in BROAD for r in rights)


def hire(owner: Client, cmp: str, email: str, rights: list[dict[str, Any]]) -> tuple[Client, str]:
    """Invites a new named user with `rights` and accepts; returns their client and employment.
    Especially broad rights are confirmed by the director's one-time code."""
    client = Client()
    named_user(client, email)
    public_id = client.get("/api/me").json()["user"]["public_id"]
    body: dict[str, Any] = {"target": public_id, "position_name": "Staff", "rights": rights}
    if is_broad(rights) and rights[0].get("type") != "deny":
        body.update(broad_code(owner))
    invitation = post(owner, f"/api/c/{cmp}/employees/invitations", body, headers=key())
    assert invitation.status_code == 201, invitation.content
    data = invitation.json()["invitation"]
    accepted = post(client, f"/api/invitations/{data['id']}/accept", {}, headers=key())
    assert accepted.status_code == 200, accepted.content
    employment_id: str = data["employment_id"]
    return client, employment_id
