"""Tags of the company and personal tags (customer's decisions of 17.11.2026): a tag of the
company is made with the right «Manage tags» and chosen by the others; it is put on records of
any section and finds them together; a personal tag is seen by its owner alone."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import hire, key
from tests.rentals.scenario import Scenario

pytestmark = pytest.mark.django_db


def _send(client: Client, method: str, path: str, data: dict[str, Any]) -> Any:
    return getattr(client, method)(path, data=data, content_type="application/json", headers=key())


def _tag(client: Client, s: Scenario, name: str, color: str = "blue", **more: Any) -> Any:
    return post(client, f"/api/c/{s.cmp}/tags", {"name": name, "color": color, **more}, key())


def _put(client: Client, s: Scenario, kind: str, record: str, tags: list[str]) -> Any:
    body = {"type": kind, "id": record, "tag_ids": tags}
    return _send(client, "put", f"/api/c/{s.cmp}/tag-links", body)


def _of(client: Client, s: Scenario, kind: str, record: str) -> list[str]:
    answer = client.get(f"/api/c/{s.cmp}/tag-links?type={kind}&id={record}")
    assert answer.status_code == 200, answer.content
    return [t["name"] for t in answer.json()["tags"]]


def _names(client: Client, s: Scenario) -> list[str]:
    return [t["name"] for t in client.get(f"/api/c/{s.cmp}/tags").json()["tags"]]


def _code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def test_tag_of_the_company_is_made_with_the_right_and_chosen_by_the_others(
    owner: Client, s: Scenario
) -> None:
    mechanic, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}])

    made = _tag(owner, s, "VIP", "red")
    assert made.status_code == 201, made.content
    vip = made.json()["tag"]
    assert (vip["name"], vip["color"], vip["personal"], vip["archived"]) == (
        "VIP",
        "red",
        False,
        False,
    )
    # The same name in another letter case is the same tag; a colour is one of the palette.
    assert _code(_tag(owner, s, "vip")) == "TAG_EXISTS"
    assert _code(_tag(owner, s, "Gold", "#ff0000")) == "TAG_COLOR_UNKNOWN"
    assert _code(_tag(owner, s, "   ")) == "FIELD_REQUIRED"

    # Without the right a tag of the company is not made; the list is seen and chosen from.
    assert _tag(mechanic, s, "Urgent").status_code == 403
    listed = mechanic.get(f"/api/c/{s.cmp}/tags").json()
    assert [t["name"] for t in listed["tags"]] == ["VIP"] and listed["can_manage"] is False
    assert owner.get(f"/api/c/{s.cmp}/tags").json()["can_manage"] is True
    assert _put(mechanic, s, "vehicle", s.vehicle, [vip["id"]]).status_code == 200
    assert _of(owner, s, "vehicle", s.vehicle) == ["VIP"]
    # Nor is it changed or archived without the right.
    body = {"name": "V", "expected_version": vip["version"]}
    assert _send(mechanic, "patch", f"/api/c/{s.cmp}/tags/{vip['id']}", body).status_code == 403
    archive = f"/api/c/{s.cmp}/tags/{vip['id']}/archive"
    assert post(mechanic, archive, {"expected_version": vip["version"]}, key()).status_code == 403


def test_a_tag_finds_the_records_of_every_section_the_employee_sees(
    owner: Client, s: Scenario
) -> None:
    mechanic, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}])
    vip = _tag(owner, s, "VIP").json()["tag"]
    assert _put(owner, s, "client", s.client, [vip["id"]]).status_code == 200
    assert _put(owner, s, "vehicle", s.vehicle, [vip["id"]]).status_code == 200
    # Put twice, a tag stands once; an unknown record or kind is refused.
    assert _put(owner, s, "vehicle", s.vehicle, [vip["id"]]).json()["tags"][0]["name"] == "VIP"
    assert _put(owner, s, "vehicle", s.client, [vip["id"]]).status_code == 404
    assert _code(_put(owner, s, "payment", s.client, [vip["id"]])) == "TAG_OBJECT_TYPE_UNKNOWN"

    page = owner.get(f"/api/c/{s.cmp}/tags/{vip['id']}/objects").json()
    assert set(page["objects"]) == {"client", "vehicle"}
    assert page["objects"]["vehicle"][0]["id"] == s.vehicle
    assert page["objects"]["client"][0]["id"] == s.client
    assert owner.get(f"/api/c/{s.cmp}/tags").json()["tags"][0]["count"] == 2
    # The tags of every record of a kind, for the lists.
    links = owner.get(f"/api/c/{s.cmp}/tag-links?type=vehicle").json()["links"]
    assert links == {s.vehicle: [vip["id"]]}

    # The mechanic does not see the clients: the tag shows them none, and is not put on one.
    seen = mechanic.get(f"/api/c/{s.cmp}/tags/{vip['id']}/objects").json()["objects"]
    assert set(seen) == {"vehicle"}
    assert _put(mechanic, s, "client", s.client, []).status_code == 404
    assert mechanic.get(f"/api/c/{s.cmp}/tag-links?type=client&id={s.client}").status_code == 404

    # The tags of the company are told in the history of the record; the tag is found by name.
    event = AuditEvent.objects.get(action="tag.links_changed", object_type="client_card")
    assert (event.object_id, event.after) == (s.client, {"tags": "VIP"})
    groups = owner.get(f"/api/c/{s.cmp}/search?q=vip").json()["groups"]
    found = {group["kind"]: group["hits"] for group in groups}
    assert [hit["label"] for hit in found["tag"]] == ["VIP"]


def test_a_tag_is_renamed_everywhere_and_archived_with_its_marks(
    owner: Client, s: Scenario
) -> None:
    vip = _tag(owner, s, "VIP").json()["tag"]
    _put(owner, s, "vehicle", s.vehicle, [vip["id"]])
    path = f"/api/c/{s.cmp}/tags/{vip['id']}"

    stale = {"name": "Gold", "expected_version": vip["version"] + 5}
    assert _send(owner, "patch", path, stale).status_code == 409
    body = {"name": "Gold", "color": "yellow", "expected_version": vip["version"]}
    gold = _send(owner, "patch", path, body).json()["tag"]
    assert (gold["name"], gold["color"]) == ("Gold", "yellow")
    assert _of(owner, s, "vehicle", s.vehicle) == ["Gold"]

    archived = post(owner, f"{path}/archive", {"expected_version": gold["version"]}, key()).json()
    assert archived["tag"]["archived"] is True
    # Archived, it is shown on no record and is not put; restored, its marks are back.
    assert _of(owner, s, "vehicle", s.vehicle) == []
    assert _code(_put(owner, s, "vehicle", s.vehicle, [vip["id"]])) == "TAG_UNKNOWN"
    version = archived["tag"]["version"]
    assert post(owner, f"{path}/restore", {"expected_version": version}, key()).status_code == 200
    assert _of(owner, s, "vehicle", s.vehicle) == ["Gold"]


def test_a_personal_tag_is_seen_by_its_owner_alone(owner: Client, s: Scenario) -> None:
    mechanic, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}])
    vip = _tag(owner, s, "VIP").json()["tag"]
    # Any employee makes a personal tag; its name may repeat a tag of the company or of another.
    mine = _tag(mechanic, s, "Check later", "green", personal=True)
    assert mine.status_code == 201, mine.content
    later = mine.json()["tag"]
    assert later["personal"] is True
    assert _tag(owner, s, "Check later", personal=True).status_code == 201
    assert _code(_tag(mechanic, s, "check LATER", personal=True)) == "TAG_EXISTS"

    assert _names(mechanic, s) == ["Check later", "VIP"]
    assert _put(mechanic, s, "vehicle", s.vehicle, [vip["id"], later["id"]]).status_code == 200
    assert _of(mechanic, s, "vehicle", s.vehicle) == ["VIP", "Check later"]
    # The owner of the company sees neither the tag, nor its mark, nor its page.
    assert _of(owner, s, "vehicle", s.vehicle) == ["VIP"]
    assert owner.get(f"/api/c/{s.cmp}/tags/{later['id']}/objects").status_code == 404
    assert _code(_put(owner, s, "vehicle", s.vehicle, [later["id"]])) == "TAG_UNKNOWN"
    # What another puts does not take the personal mark off.
    assert _put(owner, s, "vehicle", s.vehicle, []).status_code == 200
    assert _of(mechanic, s, "vehicle", s.vehicle) == ["Check later"]
    page = mechanic.get(f"/api/c/{s.cmp}/tags/{later['id']}/objects").json()
    assert page["can_manage"] is True and set(page["objects"]) == {"vehicle"}
    # Its owner renames it without the right of the company's tags.
    body = {"name": "Later", "expected_version": later["version"]}
    assert _send(mechanic, "patch", f"/api/c/{s.cmp}/tags/{later['id']}", body).status_code == 200
    # The journal of the company keeps the fact, not the name.
    personal = AuditEvent.objects.filter(action__startswith="tag.personal_")
    assert personal.count() >= 3
    assert all(e.before is None and e.after is None for e in personal)
    assert "Later" not in str(list(AuditEvent.objects.values_list("after", "before")))


def test_the_search_inside_a_section_finds_by_the_tag(owner: Client, s: Scenario) -> None:
    """Customer's decision of 17.11.2026: in the garage a search by a tag gives the vehicles it
    is put on, among the clients — the clients, and so in every section."""
    gold = _tag(owner, s, "Gold partner").json()["tag"]
    for kind, record in (("vehicle", s.vehicle), ("client", s.client)):
        assert _put(owner, s, kind, record, [gold["id"]]).status_code == 200

    def found(path: str, key: str) -> list[str]:
        return [x["id"] for x in owner.get(f"/api/c/{s.cmp}/{path}").json()[key]]

    assert found("vehicles?q=gold", "vehicles") == [s.vehicle]
    assert found("clients?q=gold", "clients") == [s.client]
    # By the name, as before; what has neither the text nor the tag is not found.
    assert found("vehicles?q=yaris", "vehicles") == [s.vehicle]
    assert found("vehicles?q=silver", "vehicles") == []
    assert found("clients?q=silver", "clients") == []
    # An archived tag finds nothing.
    path = f"/api/c/{s.cmp}/tags/{gold['id']}/archive"
    assert post(owner, path, {"expected_version": gold["version"]}, key()).status_code == 200
    assert found("vehicles?q=gold", "vehicles") == []


def test_groups_of_tags_name_who_makes_their_tags_and_where_they_are_offered(
    owner: Client, s: Scenario
) -> None:
    """Customer's decisions of 17.11.2026: a group names the employees who make its tags; it is
    offered for the kinds of records chosen for it; everybody sees the groups and their tags."""
    keeper, keeper_id = hire(owner, s.cmp, "k@example.com", [{"type": "set", "set": "storekeeper"}])
    mechanic, _ = hire(owner, s.cmp, "m@example.com", [{"type": "set", "set": "mechanic"}])
    groups = f"/api/c/{s.cmp}/tag-groups"
    body = {"name": "Places", "kinds": ["item"], "member_ids": [str(keeper_id)]}
    # A group is made with the right.
    assert post(keeper, groups, body, key()).status_code == 403
    assert post(owner, groups, body, key()).status_code == 201
    assert _code(post(owner, groups, {"name": "places"}, key())) == "TAG_GROUP_EXISTS"
    listed = mechanic.get(f"/api/c/{s.cmp}/tags").json()
    group = listed["groups"][0]
    assert (group["name"], group["kinds"], group["can_create"]) == ("Places", ["item"], False)
    assert [m["id"] for m in group["members"]] == [str(keeper_id)]
    assert "employees" not in listed and "employees" in owner.get(f"/api/c/{s.cmp}/tags").json()

    # The named employee makes, renames and archives the tags of the group — and no other tag.
    shelf = _tag(keeper, s, "Shelf 3", "teal", group_id=group["id"])
    assert shelf.status_code == 201, shelf.content
    shelf_tag = shelf.json()["tag"]
    assert shelf_tag["group_id"] == group["id"]
    assert _tag(keeper, s, "VIP").status_code == 403
    assert _tag(mechanic, s, "Shelf 4", group_id=group["id"]).status_code == 403
    path = f"/api/c/{s.cmp}/tags/{shelf_tag['id']}"
    body = {"name": "Shelf 3B", "expected_version": shelf_tag["version"]}
    renamed = _send(keeper, "patch", path, body)
    assert renamed.status_code == 200, renamed.content
    assert _send(mechanic, "patch", path, {**body, "name": "X"}).status_code == 403
    # Out of the group — with the right only.
    out = {"group_id": None, "expected_version": renamed.json()["tag"]["version"]}
    assert _send(keeper, "patch", path, out).status_code == 403

    # The group is for the items of the warehouse: its tag is not put on a vehicle.
    assert _code(_put(owner, s, "vehicle", s.vehicle, [shelf_tag["id"]])) == "TAG_NOT_FOR_KIND"

    # The group is changed and removed with the right; its tags stay, without a group.
    change = {"kinds": [], "member_ids": [], "expected_version": group["version"]}
    assert _send(keeper, "patch", f"{groups}/{group['id']}", change).status_code == 403
    assert _send(owner, "patch", f"{groups}/{group['id']}", change).status_code == 200
    assert _put(owner, s, "vehicle", s.vehicle, [shelf_tag["id"]]).status_code == 200
    assert _tag(keeper, s, "Shelf 5", group_id=group["id"]).status_code == 403
    assert owner.delete(f"{groups}/{group['id']}", headers=key()).status_code == 200
    after = owner.get(f"/api/c/{s.cmp}/tags").json()
    assert after["groups"] == [] and after["tags"][0]["group_id"] is None
