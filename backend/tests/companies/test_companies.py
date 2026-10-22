"""Company creation, active company and profile (ТЗ § 3.1, § 3.3.4–3.3.5, § 22.1.4, § 22.1.7)."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.test import Client

from c4rsys.companies.models import Company, Employment, OwnershipShare, Position
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import register
from tests.companies.helpers import create_company, named_user, patch_company

pytestmark = pytest.mark.django_db


def test_create_company_requires_full_name(client: Client) -> None:
    # § 2.1.3: without first and last name the user cannot create a company.
    register(client)

    response = create_company(client)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NAME_REQUIRED"
    assert not Company.objects.exists()


def test_display_name_is_the_only_required_field(owner: Client) -> None:
    # § 3.1.1, § 3.1.5: only the display name is required at creation.
    assert create_company(owner, display_name="").json()["error"]["code"] == "FIELD_REQUIRED"

    response = create_company(owner, key="c-2")

    assert response.status_code == 201, response.content
    company = response.json()["company"]
    assert company["display_name"] == "Sunrise Rent"
    assert company["timezone"] is None
    assert company["main_currency"] is None
    assert company["public_id"].startswith("CMP-") and len(company["public_id"]) == 12


def test_creation_creates_share_director_position_and_employment(owner: Client) -> None:
    # § 3.1.4, § 3.3.4–3.3.5: 100% share, top-authority position, employment without invitation.
    public_id = create_company(owner).json()["company"]["public_id"]

    company = Company.objects.get(public_id=public_id)
    share = OwnershipShare.objects.get(company=company)
    assert share.owner_user_id == company.created_by_id
    assert share.percent == Decimal("100")
    assert share.valid_to is None

    employment = Employment.objects.select_related("position").get(company=company)
    assert employment.user_id == company.created_by_id
    assert employment.state == Employment.State.ACTIVE
    assert employment.position.code == Position.Code.DIRECTOR
    assert employment.position.is_top_authority
    assert employment.position.is_owner_position


def test_creation_is_journaled_in_the_company(owner: Client) -> None:
    # § 3.1.4: the fact of creation stays in the history.
    public_id = create_company(owner).json()["company"]["public_id"]
    company = Company.objects.get(public_id=public_id)

    actions = set(AuditEvent.objects.filter(company_id=company.id).values_list("action", flat=True))

    assert actions == {"company.created", "company.share_granted", "employment.created"}


def test_repeat_with_same_key_creates_one_company(owner: Client) -> None:
    first = create_company(owner)
    second = create_company(owner)

    assert second.status_code == 201
    assert second.json() == first.json()
    assert Company.objects.count() == 1


def test_timezone_and_currency_may_be_given_at_creation(owner: Client) -> None:
    response = create_company(owner, timezone="Asia/Bangkok", main_currency="thb")

    company = response.json()["company"]
    assert company["timezone"] == "Asia/Bangkok"
    assert company["main_currency"] == "THB"
    assert company["setup"] == {"profile": True, "finance": True}


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("timezone", "Mars/Olympus", "TIMEZONE_INVALID"),
        ("main_currency", "XXX", "CURRENCY_INVALID"),
    ],
)
def test_invalid_timezone_or_currency_is_rejected(
    owner: Client, field: str, value: str, code: str
) -> None:
    response = create_company(owner, **{field: value})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert not Company.objects.exists()


def test_my_companies_lists_only_related_companies(owner: Client) -> None:
    # § 2.1.8, § 3.3.7: a user may have several companies; others are not listed.
    create_company(owner, key="c-1", display_name="B Company")
    create_company(owner, key="c-2", display_name="A Company")
    stranger = Client()
    named_user(stranger, "stranger@example.com")
    create_company(stranger, display_name="Foreign")

    names = [c["display_name"] for c in owner.get("/api/companies").json()["companies"]]

    assert names == ["A Company", "B Company"]


def test_company_of_another_user_is_not_found(owner: Client) -> None:
    # § 2.8.1, § 3.3.8: a passed company ID gives no access; existence is not revealed.
    public_id = create_company(owner).json()["company"]["public_id"]
    stranger = Client()
    named_user(stranger, "stranger@example.com")

    for response in (
        stranger.get(f"/api/c/{public_id}"),
        stranger.get("/api/c/CMP-00000000"),
        patch_company(stranger, public_id, {"timezone": "UTC", "expected_version": 1}),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
    assert AuditEvent.objects.filter(action="security.company_access_denied").count() == 3


def test_active_company_is_resolved_case_insensitively(owner: Client) -> None:
    public_id = create_company(owner).json()["company"]["public_id"]

    body = owner.get(f"/api/c/{public_id.lower()}").json()["company"]

    assert body["public_id"] == public_id
    assert body["employment"]["is_top_authority"] is True


def test_profile_completed_by_name_and_confirmed_timezone(owner: Client) -> None:
    # § 22.1.4, § 22.1.7, § 3.1.6.
    company = create_company(owner).json()["company"]
    assert company["setup"] == {"profile": False, "finance": False}

    response = patch_company(
        owner,
        company["public_id"],
        {"timezone": "Asia/Bangkok", "main_currency": "THB", "expected_version": 1},
    )

    assert response.status_code == 200, response.content
    updated = response.json()["company"]
    assert updated["setup"] == {"profile": True, "finance": True}
    assert updated["version"] == 2
    event = AuditEvent.objects.get(action="company.profile_changed")
    assert event.before == {"timezone": "", "main_currency": None}
    assert event.after == {"timezone": "Asia/Bangkok", "main_currency": "THB"}


def test_rename_keeps_company_id(owner: Client) -> None:
    # § 3.1.1: the CMP ID does not change on renaming.
    public_id = create_company(owner).json()["company"]["public_id"]

    response = patch_company(
        owner, public_id, {"display_name": "Moonset Rent", "expected_version": 1}
    )

    assert response.json()["company"]["public_id"] == public_id
    assert response.json()["company"]["display_name"] == "Moonset Rent"


def test_stale_version_is_refused(owner: Client) -> None:
    public_id = create_company(owner).json()["company"]["public_id"]
    patch_company(owner, public_id, {"timezone": "UTC", "expected_version": 1}, key="p-1")

    response = patch_company(
        owner, public_id, {"timezone": "Asia/Bangkok", "expected_version": 1}, "p-2"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert Company.objects.get().timezone == "UTC"


def test_timezone_cannot_be_cleared(owner: Client) -> None:
    # § 22.1.4: the company time zone is required once confirmed.
    public_id = create_company(owner, timezone="UTC").json()["company"]["public_id"]

    response = patch_company(owner, public_id, {"timezone": "", "expected_version": 1})

    assert response.status_code == 400
    assert Company.objects.get().timezone == "UTC"


def test_employee_without_top_authority_cannot_change_profile(owner: Client) -> None:
    # Until permission sets arrive (T05), only the director with top authority holds
    # company.settings.
    public_id = create_company(owner).json()["company"]["public_id"]
    Position.objects.filter(code=Position.Code.DIRECTOR).update(is_top_authority=False)

    response = patch_company(owner, public_id, {"timezone": "UTC", "expected_version": 1})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


def test_position_of_employment_must_belong_to_same_company(owner: Client) -> None:
    # A05: the database forbids links to objects of another company.
    first = Company.objects.get(
        public_id=create_company(owner, key="c-1").json()["company"]["public_id"]
    )
    second = Company.objects.get(
        public_id=create_company(owner, key="c-2").json()["company"]["public_id"]
    )
    foreign_position = Position.objects.get(company=second)

    with pytest.raises(IntegrityError), transaction.atomic():
        Employment.objects.filter(company=first).update(position=foreign_position)
