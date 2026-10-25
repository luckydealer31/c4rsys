"""Income and expense articles of the company (ТЗ § 17.2.8)."""

from typing import Any

import pytest
from django.test import Client

from c4rsys.finance.articles import STARTING
from c4rsys.finance.models import FinanceArticle
from c4rsys.platform.models import AuditEvent
from tests.accounts.helpers import post
from tests.companies.helpers import create_company, hire, key

pytestmark = pytest.mark.django_db


@pytest.fixture
def cmp(owner: Client) -> str:
    public_id: str = create_company(owner, timezone="Asia/Bangkok", main_currency="THB").json()[
        "company"
    ]["public_id"]
    return public_id


def _list(client: Client, cmp: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get(f"/api/c/{cmp}/finance-articles").json()["articles"]
    return items


def _create(client: Client, cmp: str, kind: str = "expense", name: str = "Fuel") -> Any:
    return post(client, f"/api/c/{cmp}/finance-articles", {"kind": kind, "name": name}, key())


def _patch(client: Client, cmp: str, article: dict[str, Any], **data: Any) -> Any:
    return client.patch(
        f"/api/c/{cmp}/finance-articles/{article['id']}",
        data={"expected_version": article["version"], **data},
        content_type="application/json",
        headers=key(),
    )


def test_new_company_gets_starting_articles(owner: Client, cmp: str) -> None:
    # Five income and eight expense articles with a constant code and a translated name.
    items = _list(owner, cmp)

    assert [(a["kind"], a["code"]) for a in items] == list(STARTING)
    assert sum(a["kind"] == "income" for a in items) == 5
    assert sum(a["kind"] == "expense" for a in items) == 8
    assert all(a["name"] is None and a["state"] == "active" for a in items)


def test_own_article_and_rename_keeps_code(owner: Client, cmp: str) -> None:
    created = _create(owner, cmp, name="Fuel")
    assert created.status_code == 201, created.content
    own = created.json()["article"]
    assert (own["code"], own["name"]) == (None, "Fuel")

    starting = next(a for a in _list(owner, cmp) if a["code"] == "staff")
    renamed = _patch(owner, cmp, starting, name="Salaries").json()["article"]

    # A renamed starting article is shown as entered and keeps its identity.
    assert (renamed["id"], renamed["code"], renamed["name"]) == (
        starting["id"],
        "staff",
        "Salaries",
    )
    assert AuditEvent.objects.filter(action="finance_article.renamed").count() == 1


def test_archive_and_restore(owner: Client, cmp: str) -> None:
    article = _create(owner, cmp).json()["article"]
    path = f"/api/c/{cmp}/finance-articles/{article['id']}"

    archived = post(owner, f"{path}/archive", {"expected_version": 1}, key()).json()["article"]
    again = post(owner, f"{path}/archive", {"expected_version": 2}, key())
    restored = post(owner, f"{path}/restore", {"expected_version": 2}, key()).json()["article"]

    assert archived["state"] == "archived"
    assert again.json()["error"]["code"] == "ARTICLE_ARCHIVED"
    assert restored["state"] == "active"


def test_delete_only_unused_own_article(owner: Client, cmp: str) -> None:
    own = _create(owner, cmp).json()["article"]
    starting = _list(owner, cmp)[0]

    def delete(article: dict[str, Any]) -> Any:
        return owner.delete(
            f"/api/c/{cmp}/finance-articles/{article['id']}",
            data={"expected_version": article["version"]},
            content_type="application/json",
            headers=key(),
        )

    assert delete(starting).json()["error"]["code"] == "ARTICLE_STARTING"
    assert delete(own).status_code == 200
    assert not FinanceArticle.objects.filter(pk=own["id"]).exists()


def test_articles_need_their_permission(owner: Client, cmp: str) -> None:
    accountant, _ = hire(owner, cmp, "acc@example.com", [{"type": "set", "set": "accountant"}])
    manager, _ = hire(owner, cmp, "m@example.com", [{"type": "set", "set": "rental_manager"}])
    driver, _ = hire(owner, cmp, "d@example.com", [{"type": "set", "set": "driver"}])

    assert _create(accountant, cmp).status_code == 201
    # The rental manager sees the articles to classify money, but does not manage them.
    assert len(_list(manager, cmp)) == 14
    assert _create(manager, cmp, name="Other").status_code == 403
    assert driver.get(f"/api/c/{cmp}/finance-articles").status_code == 403


def test_name_required_for_own_article(owner: Client, cmp: str) -> None:
    response = _create(owner, cmp, name="")

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "name"
