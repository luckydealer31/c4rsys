"""Creation card of a vehicle: catalog, characteristics, name, identifier matches (ТЗ § 8.1.8–
8.1.12, § 8.6.1, § 8.6.3; customer's decisions of 24.10.2026)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.fleet.models import VehicleMake, VehicleModel, VehicleOwnerClaim
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post, register
from tests.companies.helpers import create_company, key

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok").json()["company"]["public_id"]
    return public_id


@pytest.fixture
def location(owner: Client, cmp: str) -> str:
    location_id: str = post(owner, f"/api/c/{cmp}/locations", {"name": "Phuket"}, key()).json()[
        "location"
    ]["id"]
    return location_id


def create(owner: Client, cmp: str, location: str, **data: Any) -> Any:
    body = {"location_id": location, "odometer_unit": "km", **data}
    return post(owner, f"/api/c/{cmp}/vehicles", body, key())


def test_passenger_car_catalog_is_loaded(owner: Client) -> None:
    assert VehicleMake.objects.filter(vehicle_type="passenger_car").count() == 396
    assert VehicleModel.objects.count() == 4521
    toyota = owner.get("/api/catalog/vehicle-makes?type=passenger_car").json()["makes"]
    assert any(m["code"] == "TOYOTA" and m["popular"] for m in toyota)
    models = owner.get("/api/catalog/vehicle-makes/TOYOTA/models").json()["models"]
    assert any(m["name"] == "Yaris" for m in models)


def test_name_is_formed_from_make_model_and_plate(owner: Client, cmp: str, location: str) -> None:
    model = VehicleModel.objects.filter(make_id="TOYOTA", name="Yaris").first()
    assert model is not None

    response = create(
        owner,
        cmp,
        location,
        vehicle_type="passenger_car",
        make="TOYOTA",
        model=model.code,
        plate="กข 1234",
        plate_country="th",
        year=2022,
        color="White",
        body_type="hatchback",
        fuel_type="petrol",
        transmission="automatic",
        engine_volume_l="1.5",
        seats=5,
    )

    assert response.status_code == 201, response.content
    vehicle = response.json()["vehicle"]
    assert vehicle["name"] == "Toyota Yaris กข 1234"
    assert vehicle["characteristics"]["plate_country"] == "TH"
    assert vehicle["characteristics"]["engine_volume_l"] == "1.5"
    assert vehicle["characteristics"]["identifier_matches"] == []


def test_manual_name_and_other_make_as_text(owner: Client, cmp: str, location: str) -> None:
    response = create(
        owner, cmp, location, name="Белый скутер", vehicle_type="moped", make_other="Vespa"
    )

    vehicle = response.json()["vehicle"]
    assert vehicle["name"] == "Белый скутер"
    assert vehicle["characteristics"]["make_name"] == "Vespa"


def test_name_is_required_when_nothing_forms_it(owner: Client, cmp: str, location: str) -> None:
    response = create(owner, cmp, location, vehicle_type="passenger_car")

    assert response.json()["error"]["field"] == "name"


def test_vin_and_plate_matches_are_marked_not_blocked(
    owner: Client, cmp: str, location: str
) -> None:
    first = create(owner, cmp, location, name="A", vin="jtdkb20u093123456", plate="AB 123")
    assert first.status_code == 201

    second = create(owner, cmp, location, name="B", vin=" JTDKB20U093123456 ", plate="ab-123")

    assert second.status_code == 201
    assert second.json()["vehicle"]["characteristics"]["identifier_matches"] == ["vin", "plate"]


def test_unusual_vin_is_only_a_warning(owner: Client, cmp: str, location: str) -> None:
    response = create(owner, cmp, location, name="Old", vin="123")

    assert response.status_code == 201
    assert response.json()["warnings"] == ["VIN_FORMAT"]


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"transport_class": "air"}, "transport_class"),
        ({"vehicle_type": "spaceship"}, "vehicle_type"),
        ({"vehicle_type": "passenger_car", "make": "NO_SUCH"}, "make"),
        ({"vehicle_type": "passenger_car", "make": "TOYOTA", "model": "BMW_X5"}, "model"),
        ({"year": 1500}, "year"),
        ({"body_type": "boat"}, "body_type"),
    ],
)
def test_invalid_characteristics_are_refused(
    owner: Client, cmp: str, location: str, data: dict[str, Any], field: str
) -> None:
    response = create(owner, cmp, location, name="X", **data)

    assert response.status_code == 400
    assert response.json()["error"]["field"] == field


# --- Declared owner (§ 8.1.6, § 8.3.13) ------------------------------------------------------


def test_own_vehicle_has_the_company_as_declared_owner(
    owner: Client, cmp: str, location: str
) -> None:
    vehicle = create(owner, cmp, location, name="A").json()["vehicle"]

    assert vehicle["relation"] == "own"
    assert vehicle["owner"]["kind"] == "company"
    assert vehicle["owner"]["company"]["public_id"] == cmp
    assert vehicle["owner"]["confirmed"] is False


def test_managed_vehicle_owner_by_profile_does_not_reveal_it(
    owner: Client, cmp: str, location: str
) -> None:
    other = Client()
    register(other, "carowner@example.com")

    response = create(
        owner,
        cmp,
        location,
        name="B",
        relation="managed",
        owner_kind="profile",
        owner_lookup="CarOwner@Example.com",
    )

    assert response.status_code == 201, response.content
    owner_body = response.json()["vehicle"]["owner"]
    assert owner_body == {
        "kind": "profile",
        "lookup": "carowner@example.com",
        "name": "",
        "contact": "",
        "confirmed": False,
    }
    claim = VehicleOwnerClaim.objects.get(kind="profile")
    assert claim.owner_user_id is not None
    event = AuditEvent.objects.get(action="vehicle.owner_declared", object_id=claim.vehicle_card_id)
    assert "owner_user_id" not in str(event.after)


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"owner_kind": "profile"}, "owner_lookup"),
        ({"owner_kind": "profile", "owner_lookup": "nobody"}, "owner_lookup"),
        ({"owner_kind": "person"}, "owner_name"),
        ({"owner_kind": "company"}, "owner_kind"),
    ],
)
def test_managed_owner_input_is_checked(
    owner: Client, cmp: str, location: str, data: dict[str, Any], field: str
) -> None:
    response = create(owner, cmp, location, name="C", relation="managed", **data)

    assert response.json()["error"]["field"] == field


def test_managed_owner_described_by_hand_or_not_given(
    owner: Client, cmp: str, location: str
) -> None:
    by_hand = create(
        owner,
        cmp,
        location,
        name="D",
        relation="managed",
        owner_kind="organization",
        owner_name="ООО «Авто»",
        owner_contact="+7 900 000-00-00",
    ).json()["vehicle"]
    none = create(owner, cmp, location, name="E", relation="managed").json()["vehicle"]

    assert by_hand["owner"]["name"] == "ООО «Авто»"
    assert none["owner"]["kind"] == "none"
    assert none["rental_readiness"]["missing"] != ["not_owned_or_managed"]


def test_client_vehicle_has_no_location_and_only_service(
    owner: Client, cmp: str, location: str
) -> None:
    post(owner, f"/api/c/{cmp}/locations/{location}/contexts", {"type": "service"}, key())
    client_id = post(
        owner,
        f"/api/c/{cmp}/clients",
        {
            "kind": "person",
            "first_name": "Ivan",
            "last_name": "Petrov",
            "contacts": [{"kind": "phone", "value": "+66812345678"}],
        },
        key(),
    ).json()["client"]["id"]

    response = post(
        owner,
        f"/api/c/{cmp}/vehicles",
        {
            "name": "Client car",
            "relation": "client",
            "odometer_unit": "km",
            "location_id": location,  # ignored for a client vehicle
            "owner_client_id": client_id,
        },
        key(),
    )

    assert response.status_code == 201, response.content
    vehicle = response.json()["vehicle"]
    assert vehicle["location_id"] is None
    assert vehicle["contexts"] == ["service"]
    assert vehicle["owner"]["client"]["name"] == "Ivan Petrov"
    assert "not_owned_or_managed" in vehicle["rental_readiness"]["missing"]


def test_client_of_another_company_cannot_be_owner(owner: Client, cmp: str) -> None:
    response = post(
        owner,
        f"/api/c/{cmp}/vehicles",
        {
            "name": "X",
            "relation": "client",
            "odometer_unit": "km",
            "owner_client_id": "01a1628f-ca1c-71ab-a48b-80eede8d99b1",
        },
        key(),
    )

    assert response.json()["error"]["code"] == "CLIENT_UNKNOWN"


def test_client_vehicle_owner_by_hand_without_profile(owner: Client, cmp: str) -> None:
    response = post(
        owner,
        f"/api/c/{cmp}/vehicles",
        {
            "name": "Guest car",
            "relation": "client",
            "odometer_unit": "km",
            "owner_kind": "person",
            "owner_name": "Сидоров Пётр",
            "owner_contact": "+7 900 111-22-33",
        },
        key(),
    )

    assert response.status_code == 201, response.content
    assert response.json()["vehicle"]["owner"]["name"] == "Сидоров Пётр"
    refused = post(
        owner,
        f"/api/c/{cmp}/vehicles",
        {"name": "Y", "relation": "client", "odometer_unit": "km", "owner_kind": "profile"},
        key(),
    )
    assert refused.json()["error"]["field"] == "owner_kind"
