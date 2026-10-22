"""Orders: numbering, displayed state, amounts, visibility (ТЗ § 12.1.1, § 12.1.9–12.1.10,
§ 12.2.3–12.2.4)."""

import threading
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from unittest import mock
from uuid import UUID

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import Client

from c4rsys.companies.models import Company, Employment
from c4rsys.orders import services
from c4rsys.orders.models import Order
from c4rsys.parties.models import ClientCard
from c4rsys.platform.commands import Actor, CommandContext, CommandResult, run_command
from c4rsys.platform.errors import ApiError
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db

_keys = iter(range(10**6))


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok").json()["company"]["public_id"]
    return public_id


def _client_card(owner: Client, cmp: str) -> ClientCard:
    body = {
        "kind": "person",
        "first_name": "Ivan",
        "last_name": "Petrov",
        "contacts": [{"kind": "phone", "value": "+66812345678"}],
    }
    client_id = post(owner, f"/api/c/{cmp}/clients", body, key()).json()["client"]["id"]
    return ClientCard.objects.get(pk=client_id)


def _director(company: Company) -> Employment:
    return Employment.objects.get(company=company, position__is_top_authority=True)


def _create_order(card: ClientCard, *, fail: bool = False) -> Any:
    director = _director(card.company)
    assert director.user_id is not None
    actor = Actor(user_id=director.user_id, company_id=card.company_id, employment_id=director.id)

    def handler(ctx: CommandContext) -> CommandResult:
        order = services.create_order(
            ctx,
            company_id=card.company_id,
            client_id=card.id,
            responsible_employment_id=director.id,
        )
        if fail:
            raise RuntimeError("operation failed after the order was created")
        return CommandResult(201, {"id": str(order.id), "number": order.number})

    return run_command(
        command="test.create_order",
        actor=actor,
        idempotency_key=f"o-{next(_keys)}",
        payload={},
        handler=handler,
    )


@pytest.fixture
def card(owner: Client, cmp: str) -> ClientCard:
    return _client_card(owner, cmp)


@pytest.fixture
def providers() -> Iterator[services._Registry]:
    fresh = services._Registry()
    with mock.patch.object(services, "registry", fresh):
        yield fresh


def test_numbers_are_sequential_inside_a_company(owner: Client, card: ClientCard) -> None:
    # § 12.1.1: sequential inside the company; unique together with the company.
    other = _client_card(
        owner, create_company(owner, key="c-2", display_name="B").json()["company"]["public_id"]
    )

    numbers = [_create_order(card).body["number"] for _ in range(3)]
    other_number = _create_order(other).body["number"]

    assert numbers == [1, 2, 3]
    assert other_number == 1


def test_rolled_back_command_does_not_consume_a_number(card: ClientCard) -> None:
    assert _create_order(card).body["number"] == 1
    with pytest.raises(ApiError):
        _create_order(card, fail=True)

    assert _create_order(card).body["number"] == 2
    assert Order.objects.count() == 2


def test_new_order_is_open_with_responsible_creator_and_journal(card: ClientCard) -> None:
    # § 12.1.10, § 12.2.3.
    order = Order.objects.get(pk=_create_order(card).body["id"])

    assert order.state == Order.State.OPEN
    assert order.responsible_employment_id == _director(card.company).id
    assert order.client_id == card.id
    assert AuditEvent.objects.filter(action="order.created", company_id=card.company_id).exists()


def test_display_state_follows_operations(
    owner: Client, cmp: str, card: ClientCard, providers: services._Registry
) -> None:
    # § 12.1.9, § 12.2.3: «ready to close» is computed, not stored.
    order_id = _create_order(card).body["id"]
    rental = services.OperationSummary("rental", UUID(int=1), "returned", None, finished=False)
    services.register_operations(lambda company_id, oid: [rental] if str(oid) == order_id else [])

    body = owner.get(f"/api/c/{cmp}/orders/{order_id}").json()["order"]
    assert body["state"] == "open"
    assert body["closing_conditions"] == [
        {"operation": "rental", "id": str(UUID(int=1)), "condition": "not_finished"}
    ]

    providers.operations.clear()
    done = services.OperationSummary("rental", UUID(int=1), "returned", None, finished=True)
    services.register_operations(lambda company_id, oid: [done])
    assert owner.get(f"/api/c/{cmp}/orders/{order_id}").json()["order"]["state"] == "ready_to_close"


def test_amounts_charged_paid_and_balance(
    owner: Client, cmp: str, card: ClientCard, providers: services._Registry
) -> None:
    # § 12.2.4: charged, paid and what remains or the overpayment, per currency.
    order_id = _create_order(card).body["id"]
    services.register_charged(lambda c, o: {"THB": Decimal("2000.00")})
    services.register_paid(lambda c, o: {"THB": Decimal("1500.00")})

    amounts = owner.get(f"/api/c/{cmp}/orders/{order_id}").json()["order"]["amounts"]
    assert amounts == {
        "THB": {"charged": "2000.00", "paid": "1500.00", "to_pay": "500.00", "overpaid": "0"}
    }

    services.register_paid(lambda c, o: {"THB": Decimal("1000.00")})
    amounts = owner.get(f"/api/c/{cmp}/orders/{order_id}").json()["order"]["amounts"]
    assert amounts["THB"]["to_pay"] == "0" and amounts["THB"]["overpaid"] == "500.00"


def test_visibility_follows_locations_of_operations(
    owner: Client, cmp: str, card: ClientCard, providers: services._Registry
) -> None:
    phuket = post(owner, f"/api/c/{cmp}/locations", {"name": "Phuket"}, key()).json()["location"][
        "id"
    ]
    krabi = post(owner, f"/api/c/{cmp}/locations", {"name": "Krabi"}, key()).json()["location"][
        "id"
    ]
    in_krabi = _create_order(card).body["id"]
    services.register_operations(
        lambda c, o: [
            services.OperationSummary("rental", UUID(int=2), "active", UUID(krabi), False)
        ]
    )
    manager, _ = hire(
        owner,
        cmp,
        "m@example.com",
        [{"type": "set", "set": "rental_manager", "locations": [phuket]}],
    )
    driver, _ = hire(owner, cmp, "d@example.com", [{"type": "set", "set": "driver"}])

    assert owner.get(f"/api/c/{cmp}/orders/{in_krabi}").status_code == 200
    assert manager.get(f"/api/c/{cmp}/orders/{in_krabi}").status_code == 404
    assert manager.get(f"/api/c/{cmp}/orders").json()["orders"] == []
    assert driver.get(f"/api/c/{cmp}/orders").json()["orders"] == []


def test_order_of_another_company_is_not_found(owner: Client, card: ClientCard) -> None:
    order_id = _create_order(card).body["id"]
    other = create_company(owner, key="c-2", display_name="B").json()["company"]["public_id"]

    assert owner.get(f"/api/c/{other}/orders/{order_id}").status_code == 404


def test_client_must_belong_to_same_company(owner: Client, card: ClientCard) -> None:
    # A05: the database forbids cross-company links.
    other = Company.objects.get(
        public_id=create_company(owner, key="c-2", display_name="B").json()["company"]["public_id"]
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.create(
            company=other, number=1, client=card, created_by_user_id=card.created_by_user_id
        )


@pytest.mark.django_db(transaction=True)
def test_concurrent_orders_get_distinct_consecutive_numbers(owner: Client, cmp: str) -> None:
    # Real PostgreSQL: the counter row lock serialises concurrent commands.
    card = _client_card(owner, cmp)
    results: list[int] = []
    failures: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(_create_order(card).body["number"])
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not failures
    assert sorted(results) == list(range(1, 9))
