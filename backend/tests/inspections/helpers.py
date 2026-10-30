from datetime import timedelta
from typing import Any

from django.test import Client

from tests.accounts.helpers import post
from tests.companies.helpers import key
from tests.files.helpers import upload
from tests.rentals.scenario import NOW, Scenario


def at(hours: float) -> str:
    return (NOW + timedelta(hours=hours)).isoformat()


def code(response: Any) -> str:
    found: str = response.json()["error"]["code"]
    return found


def create(client: Client, s: Scenario, **body: Any) -> Any:
    data = {
        "vehicle_card_id": s.vehicle,
        "kind": "out_of_rental",
        "application": "standalone",
        **body,
    }
    return post(client, f"/api/c/{s.cmp}/inspections", data, key())


def created(client: Client, s: Scenario, **body: Any) -> dict[str, Any]:
    response = create(client, s, **body)
    assert response.status_code == 201, response.content
    found: dict[str, Any] = response.json()["inspection"]
    return found


def act(client: Client, s: Scenario, item: dict[str, Any], action: str, **body: Any) -> Any:
    data = {"expected_version": item["version"], **body}
    return post(client, f"/api/c/{s.cmp}/inspections/{item['id']}/{action}", data, key())


def ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.content
    found: dict[str, Any] = response.json()["inspection"]
    return found


def change(client: Client, s: Scenario, item: dict[str, Any], **body: Any) -> Any:
    return client.patch(
        f"/api/c/{s.cmp}/inspections/{item['id']}",
        data={"expected_version": item["version"], **body},
        content_type="application/json",
        headers=key(),
    )


def photo(client: Client, name: str = "p") -> str:
    response = upload(client, "inspection_photo", key=f"photo-{name}")
    assert response.status_code == 201, response.content
    file_id: str = response.json()["file"]["id"]
    return file_id


def saved_with_damage(
    client: Client, s: Scenario, damage: dict[str, Any] | None = None, **body: Any
) -> dict[str, Any]:
    """A saved inspection recording one damage with a photo."""
    file_id = photo(client, str(body.get("performed_at", "x")))
    entry = {
        "zone_scheme": "sedan",
        "zone_x": "0.25",
        "zone_y": "0.5",
        "description": "Scratch on the front door",
        "photo_file_ids": [file_id],
        **(damage or {}),
    }
    item = created(
        client,
        s,
        files=[{"file_id": file_id, "kind": "photo", "position": "left"}],
        damages=[entry],
        **body,
    )
    return ok(act(client, s, item, "complete", conducted=True))


def damages_of(client: Client, s: Scenario, vehicle: str | None = None) -> list[dict[str, Any]]:
    response = client.get(f"/api/c/{s.cmp}/vehicles/{vehicle or s.vehicle}/damages")
    assert response.status_code == 200, response.content
    found: list[dict[str, Any]] = response.json()["damages"]
    return found
