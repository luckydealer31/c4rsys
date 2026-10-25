"""Income and expense articles of the company (ТЗ § 17.2.8).

The starting articles are listed by the ТЗ; their internal codes are a PROPOSAL (the ТЗ leaves
them unset, гл. 26). A company gets them when it is created; the names are translated by the
interface until the company renames an article.
"""

from typing import Any
from uuid import UUID

from django.utils import timezone

from c4rsys.finance.models import FinanceArticle, MoneyMovement
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

INCOME = FinanceArticle.Kind.INCOME
EXPENSE = FinanceArticle.Kind.EXPENSE

# TODO(spec): codes are a PROPOSAL of the agent (question Ф-В6).
STARTING: tuple[tuple[str, str], ...] = (
    (INCOME, "rental"),
    (INCOME, "service"),
    (INCOME, "goods_sale"),
    (INCOME, "reimbursement"),
    (INCOME, "other_income"),
    (EXPENSE, "goods_materials"),
    (EXPENSE, "vehicle_upkeep"),
    (EXPENSE, "contractor_services"),
    (EXPENSE, "owner_settlements"),
    (EXPENSE, "staff"),
    (EXPENSE, "administrative"),
    (EXPENSE, "taxes_fees"),
    (EXPENSE, "other_expense"),
)


def create_starting(company_id: UUID) -> None:
    """Called in the transaction that creates the company."""
    now = timezone.now()
    FinanceArticle.objects.bulk_create(
        [
            FinanceArticle(company_id=company_id, kind=kind, code=code, created_at=now)
            for kind, code in STARTING
        ]
    )


_ORDER = {code: index for index, (_, code) in enumerate(STARTING)}


def articles_of(company_id: UUID) -> list[FinanceArticle]:
    """Starting articles in the order of § 17.2.8, then own ones as created."""
    items = FinanceArticle.objects.filter(company_id=company_id)
    return sorted(items, key=lambda a: (_ORDER.get(a.code, len(_ORDER)), a.created_at, a.id))


def used_ids(company_id: UUID) -> set[UUID]:
    return set(
        MoneyMovement.objects.filter(company_id=company_id, article__isnull=False)
        .values_list("article_id", flat=True)
        .distinct()
    )


def usable(company_id: UUID, article_id: UUID, kind: str) -> FinanceArticle:
    """An active article of the right kind for a new record."""
    article = FinanceArticle.objects.filter(company_id=company_id, pk=article_id).first()
    if article is None:
        raise errors.validation_error("article_id", code="ARTICLE_INVALID")
    if article.state != FinanceArticle.State.ACTIVE:
        raise errors.business_rule("ARTICLE_ARCHIVED", field="article_id")
    if article.kind != kind:
        raise errors.business_rule("ARTICLE_KIND_MISMATCH", field="article_id")
    return article


def snapshot(article: FinanceArticle) -> dict[str, Any]:
    return {
        "kind": article.kind,
        "code": article.code,
        "name": article.name,
        "state": article.state,
    }


def create_article(ctx: CommandContext, company_id: UUID, kind: str, name: str) -> FinanceArticle:
    """An own article for detail or a separate category; it has no code."""
    article = FinanceArticle.objects.create(
        company_id=company_id,
        kind=kind,
        name=name,
        created_by_user_id=ctx.actor.user_id,
        created_at=timezone.now(),
    )
    ctx.audit(
        "finance_article.created",
        object_type="finance_article",
        object_id=article.id,
        after=snapshot(article),
    )
    return article


def _locked(company_id: UUID, article_id: UUID, expected_version: int) -> FinanceArticle:
    article = (
        FinanceArticle.objects.select_for_update()
        .filter(company_id=company_id, pk=article_id)
        .first()
    )
    if article is None:
        raise errors.not_found()
    if article.version != expected_version:
        raise errors.version_conflict()
    return article


def rename_article(
    ctx: CommandContext, company_id: UUID, article_id: UUID, name: str, expected_version: int
) -> FinanceArticle:
    """The records keep their amounts and article; a renamed starting article keeps its code
    and is shown as entered."""
    article = _locked(company_id, article_id, expected_version)
    before = article.name
    article.name = name
    article.version += 1
    article.save(update_fields=["name", "version", "updated_at"])
    ctx.audit(
        "finance_article.renamed",
        object_type="finance_article",
        object_id=article.id,
        before={"name": before},
        after={"name": name},
    )
    return article


def set_state(
    ctx: CommandContext, company_id: UUID, article_id: UUID, state: str, expected_version: int
) -> FinanceArticle:
    """Archive or restore. TODO(spec): restoring an archived article is not described in
    § 17.2.8 (PROPOSAL)."""
    article = _locked(company_id, article_id, expected_version)
    if article.state == state:
        code = "ARTICLE_ARCHIVED" if state == FinanceArticle.State.ARCHIVED else "ARTICLE_ACTIVE"
        raise errors.business_rule(code)
    before = article.state
    article.state = state
    article.version += 1
    article.save(update_fields=["state", "version", "updated_at"])
    ctx.audit(
        "finance_article.archived"
        if state == FinanceArticle.State.ARCHIVED
        else "finance_article.restored",
        object_type="finance_article",
        object_id=article.id,
        before={"state": before},
        after={"state": state},
    )
    return article


def delete_article(
    ctx: CommandContext, company_id: UUID, article_id: UUID, expected_version: int
) -> None:
    """A used article cannot be deleted with its history (§ 17.2.8). TODO(spec): whether an
    unused starting article may be deleted is not set; only own articles are (PROPOSAL)."""
    article = _locked(company_id, article_id, expected_version)
    if article.code:
        raise errors.business_rule("ARTICLE_STARTING")
    if MoneyMovement.objects.filter(company_id=company_id, article_id=article.id).exists():
        raise errors.business_rule("ARTICLE_USED")
    before = snapshot(article)
    article.delete()
    ctx.audit(
        "finance_article.deleted",
        object_type="finance_article",
        object_id=article_id,
        before=before,
    )
