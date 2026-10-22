import uuid
from typing import Any
from unittest import mock

import pytest
from django.db import DatabaseError, IntegrityError, transaction

from c4rsys.platform import access
from c4rsys.platform.errors import ApiError
from c4rsys.platform.models import AuditEvent, PublicIdRegistry
from c4rsys.platform.registry import allocate_public_id


@pytest.mark.django_db
def test_allocate_public_id_records_the_id() -> None:
    entity_id = uuid.uuid4()

    public_id = allocate_public_id("CMP", entity_type="company", entity_id=entity_id)

    row = PublicIdRegistry.objects.get(public_id=public_id)
    assert row.entity_type == "company"
    assert row.entity_id == entity_id


@pytest.mark.django_db
def test_allocate_public_id_retries_on_collision() -> None:
    PublicIdRegistry.objects.create(
        public_id="CMP-AAAAAAAA", entity_type="company", entity_id=uuid.uuid4()
    )
    with mock.patch(
        "c4rsys.platform.registry.generate_public_id",
        side_effect=["CMP-AAAAAAAA", "CMP-BBBBBBBB"],
    ):
        public_id = allocate_public_id("CMP", entity_type="company", entity_id=uuid.uuid4())

    assert public_id == "CMP-BBBBBBBB"


@pytest.mark.django_db
def test_entity_cannot_receive_two_ids() -> None:
    entity_id = uuid.uuid4()
    allocate_public_id("CMP", entity_type="company", entity_id=entity_id)

    with pytest.raises(IntegrityError):
        allocate_public_id("CMP", entity_type="company", entity_id=entity_id)


def _event(**overrides: Any) -> AuditEvent:
    values: dict[str, Any] = {
        "company_id": uuid.uuid4(),
        "actor_user_id": uuid.uuid4(),
        "action": "test.action",
        "object_type": "test",
        "object_id": "1",
    }
    values.update(overrides)
    return AuditEvent.objects.create(**values)


@pytest.mark.django_db
def test_audit_event_cannot_be_updated() -> None:
    event = _event()

    with pytest.raises(DatabaseError), transaction.atomic():
        AuditEvent.objects.filter(pk=event.pk).update(action="changed")


@pytest.mark.django_db
def test_audit_event_cannot_be_deleted() -> None:
    event = _event()

    with pytest.raises(DatabaseError), transaction.atomic():
        AuditEvent.objects.filter(pk=event.pk).delete()

    assert AuditEvent.objects.filter(pk=event.pk).exists()


@pytest.mark.django_db
def test_audit_event_requires_user_or_system_process() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _event(actor_user_id=None)

    system_event = _event(actor_user_id=None, system_process="scheduler")
    assert system_event.pk is not None


@pytest.mark.django_db
def test_object_of_another_company_is_not_found() -> None:
    own_company, other_company = uuid.uuid4(), uuid.uuid4()
    foreign = _event(company_id=other_company)

    with pytest.raises(ApiError) as caught:
        access.get_for_company(AuditEvent.objects.all(), own_company, pk=foreign.pk)

    assert caught.value.code == "NOT_FOUND"
    assert access.get_for_company(AuditEvent.objects.all(), other_company, pk=foreign.pk) == foreign
