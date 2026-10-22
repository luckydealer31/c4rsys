"""Command runner: one transaction with journal and idempotency (A07, decision 22.10.2026)."""

import uuid
from unittest import mock

import pytest

from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.platform.errors import ApiError, business_rule
from c4rsys.platform.models import AuditEvent, IdempotencyRecord, PublicIdRegistry


@pytest.fixture
def actor() -> Actor:
    return Actor(user_id=uuid.uuid4(), company_id=uuid.uuid4())


class FakeCommand:
    """Creates one registry row as a stand-in for a business change and journals it."""

    def __init__(self, *, journal: bool = True, fail_with: Exception | None = None) -> None:
        self.calls = 0
        self.journal = journal
        self.fail_with = fail_with

    def __call__(self, ctx: CommandContext) -> CommandResult:
        self.calls += 1
        entity_id = uuid.uuid4()
        PublicIdRegistry.objects.create(
            public_id=f"LOT-{self.calls:012d}", entity_type="fake", entity_id=entity_id
        )
        if self.journal:
            ctx.audit("fake.created", object_type="fake", object_id=entity_id, after={"n": 1})
        if self.fail_with is not None:
            raise self.fail_with
        return CommandResult(status=201, body={"id": str(entity_id)})


def _run(
    actor: Actor, handler: FakeCommand, *, key: str = "key-1", amount: int = 2000
) -> CommandResult:
    return run_command(
        command="fake.create",
        actor=actor,
        idempotency_key=key,
        payload={"amount": amount},
        handler=handler,
        request_id="req-1",
    )


@pytest.mark.django_db
def test_success_saves_change_journal_and_result(actor: Actor) -> None:
    handler = FakeCommand()

    result = _run(actor, handler)

    assert result.status == 201
    assert PublicIdRegistry.objects.filter(entity_type="fake").count() == 1
    event = AuditEvent.objects.get()
    assert event.action == "fake.created"
    assert event.actor_user_id == actor.user_id
    assert event.company_id == actor.company_id
    assert event.request_id == "req-1"
    assert event.idempotency_key == "key-1"
    record = IdempotencyRecord.objects.get()
    assert record.response_body == result.body


@pytest.mark.django_db
def test_repeat_with_same_key_returns_stored_result_without_reposting(actor: Actor) -> None:
    # Acceptance criterion 4 / example E10: a lost response is retried with the same key.
    handler = FakeCommand()

    first = _run(actor, handler)
    second = _run(actor, handler)

    assert second == first
    assert handler.calls == 1
    assert PublicIdRegistry.objects.filter(entity_type="fake").count() == 1
    assert AuditEvent.objects.count() == 1


@pytest.mark.django_db
def test_same_amount_with_new_key_is_a_new_operation(actor: Actor) -> None:
    handler = FakeCommand()

    _run(actor, handler, key="key-1")
    _run(actor, handler, key="key-2")

    assert handler.calls == 2


@pytest.mark.django_db
def test_same_key_with_different_request_is_rejected(actor: Actor) -> None:
    handler = FakeCommand()
    _run(actor, handler, amount=2000)

    with pytest.raises(ApiError) as caught:
        _run(actor, handler, amount=1500)

    assert caught.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert handler.calls == 1


@pytest.mark.django_db
def test_same_key_of_another_user_does_not_collide(actor: Actor) -> None:
    other = Actor(user_id=uuid.uuid4(), company_id=actor.company_id)
    handler = FakeCommand()

    _run(actor, handler)
    _run(other, handler)

    assert handler.calls == 2


@pytest.mark.django_db
def test_missing_key_is_rejected(actor: Actor) -> None:
    with pytest.raises(ApiError) as caught:
        _run(actor, FakeCommand(), key="  ")

    assert caught.value.code == "IDEMPOTENCY_KEY_REQUIRED"
    assert caught.value.status == 400


@pytest.mark.django_db
def test_journal_failure_rolls_back_everything(actor: Actor) -> None:
    # Decision 22.10.2026: if the journal is not saved, the operation is not posted.
    handler = FakeCommand()

    with (
        mock.patch.object(AuditEvent.objects, "bulk_create", side_effect=RuntimeError("disk")),
        pytest.raises(ApiError) as caught,
    ):
        _run(actor, handler)

    assert caught.value.code == "OPERATION_NOT_POSTED"
    assert caught.value.status == 500
    assert not PublicIdRegistry.objects.filter(entity_type="fake").exists()
    assert not IdempotencyRecord.objects.exists()

    # After the failure is fixed, a retry with the same key posts exactly once.
    result = _run(actor, handler)
    assert result.status == 201
    assert PublicIdRegistry.objects.filter(entity_type="fake").count() == 1


@pytest.mark.django_db
def test_command_without_journal_entry_is_not_posted(actor: Actor) -> None:
    with pytest.raises(ApiError) as caught:
        _run(actor, FakeCommand(journal=False))

    assert caught.value.code == "OPERATION_NOT_POSTED"
    assert not PublicIdRegistry.objects.filter(entity_type="fake").exists()


@pytest.mark.django_db
def test_business_refusal_rolls_back_and_is_not_stored(actor: Actor) -> None:
    handler = FakeCommand(fail_with=business_rule("VEHICLE_NOT_AVAILABLE"))

    with pytest.raises(ApiError) as caught:
        _run(actor, handler)

    assert caught.value.code == "VEHICLE_NOT_AVAILABLE"
    assert caught.value.status == 422
    assert not PublicIdRegistry.objects.filter(entity_type="fake").exists()
    assert not AuditEvent.objects.exists()
    assert not IdempotencyRecord.objects.exists()
