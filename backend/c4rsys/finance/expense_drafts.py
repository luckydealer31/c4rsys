"""Drafts of expenses made by a photo of a receipt (customer's decisions of 23.11.2026). A receipt
sent to the Telegram bot is read — the date, the amount, the currency, the seller and what was paid
for — and kept as a draft with the photo. The draft changes no money. Its author — who sent the
photo — later opens the list «Drafts of expenses» in «Finance», checks it, chooses the cash desk or
account and the article, and records the expense. Only the author sees a draft.

The reading is done by the outer service that reads the other documents (the socket is the same as
in warehouse/recognition.py): it is given `INSTRUCTION` and answers by `SCHEMA`; `parse` checks the
answer."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db.models import QuerySet

from c4rsys.companies.models import Company
from c4rsys.finance import records
from c4rsys.finance.models import ExpenseDraft, MoneyMovement
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

INSTRUCTION = (
    "The document is a receipt, a bill or another proof of a payment made by a company: fuel, a "
    "car wash, parking, spare parts, a toll, a fine and the like. It may be in Russian, Thai or "
    "English, printed or handwritten. Read the date of the payment, the total amount paid, its "
    "currency, the seller as written, and in a few words what was paid for. Leave out whatever "
    "cannot be read with confidence: do not guess."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["spent_on", "amount", "currency", "seller", "purpose"],
    "properties": {
        "spent_on": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "amount": {"type": ["string", "null"], "description": "a decimal number, the total paid"},
        "currency": {"type": ["string", "null"], "description": "ISO 4217"},
        "seller": {"type": ["string", "null"]},
        "purpose": {"type": ["string", "null"], "description": "what was paid for, briefly"},
    },
}
MAX_AMOUNT = Decimal("10000000000")


@dataclass(frozen=True)
class Read:
    spent_on: date | None = None
    amount: Decimal | None = None
    currency: str = ""
    seller: str = ""
    purpose: str = ""


Recognizer = Callable[[bytes, str], dict[str, Any]]
_recognizers: dict[str, Recognizer] = {}


def register(name: str, recognizer: Recognizer) -> None:
    _recognizers[name] = recognizer


def configured() -> bool:
    return (
        bool(settings.C4R_INVOICE_RECOGNITION) and settings.C4R_INVOICE_RECOGNITION in _recognizers
    )


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def parse(answer: Any) -> Read:
    """The answer of a service, checked: a wrong value is as good as not read."""
    if not isinstance(answer, dict):
        raise errors.business_rule("RECOGNITION_FAILED")
    amount = None
    raw = answer.get("amount")
    if isinstance(raw, (str, int, float)) and not isinstance(raw, bool):
        try:
            number = Decimal(str(raw).replace(" ", "").replace(",", "."))
            if number.is_finite() and 0 < number < MAX_AMOUNT:
                amount = number.quantize(Decimal("0.0001"))
        except InvalidOperation:
            amount = None
    try:
        spent_on = date.fromisoformat(answer["spent_on"])
    except (KeyError, TypeError, ValueError):
        spent_on = None
    currency = _text(answer.get("currency"), 3).upper()
    return Read(
        spent_on=spent_on,
        amount=amount,
        currency=currency if len(currency) == 3 and currency.isalpha() else "",
        seller=_text(answer.get("seller"), 200),
        purpose=_text(answer.get("purpose"), 500),
    )


def read(content: bytes, content_type: str) -> Read:
    if not configured():
        raise errors.business_rule("RECOGNITION_NOT_CONFIGURED")
    try:
        answer = _recognizers[settings.C4R_INVOICE_RECOGNITION](content, content_type)
    except errors.ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 -- whatever the outer service fails with
        raise errors.business_rule("RECOGNITION_FAILED") from exc
    return parse(answer)


# --- Drafts -------------------------------------------------------------------------------------


def of_author(company_id: UUID, user_id: UUID) -> QuerySet[ExpenseDraft]:
    """The open drafts of the person: nobody else sees them (customer's decision)."""
    return ExpenseDraft.objects.filter(
        company_id=company_id, author_user_id=user_id, state=ExpenseDraft.State.DRAFT
    ).order_by("-created_at")


def create(
    ctx: CommandContext, company: Company, file_id: UUID, data: Read, through: str
) -> ExpenseDraft:
    draft = ExpenseDraft.objects.create(
        company=company,
        author_user_id=ctx.actor.user_id,
        file_id=file_id,
        spent_on=data.spent_on,
        amount=data.amount,
        currency=data.currency,
        seller=data.seller,
        purpose=data.purpose,
        through=through,
    )
    ctx.audit(
        "expense_draft.created",
        object_type="expense_draft",
        object_id=draft.id,
        after={
            "file": str(file_id),
            "amount": str(data.amount) if data.amount is not None else None,
            "currency": data.currency,
            "through": through,
        },
    )
    return draft


def _own(company_id: UUID, draft_id: UUID, user_id: UUID) -> ExpenseDraft:
    draft = (
        ExpenseDraft.objects.select_for_update()
        .filter(company_id=company_id, pk=draft_id, author_user_id=user_id)
        .first()
    )
    if draft is None:
        raise errors.not_found()
    if draft.state != ExpenseDraft.State.DRAFT:
        raise errors.business_rule("EXPENSE_DRAFT_CLOSED")
    return draft


def remove(ctx: CommandContext, company_id: UUID, draft_id: UUID) -> None:
    draft = _own(company_id, draft_id, ctx.actor.user_id)
    draft.state = ExpenseDraft.State.REMOVED
    draft.save(update_fields=["state"])
    ctx.audit("expense_draft.removed", object_type="expense_draft", object_id=draft.id)


def record(
    ctx: CommandContext, company_id: UUID, draft_id: UUID, data: records.ManualRecord
) -> MoneyMovement:
    """The expense of the draft is recorded as a payout, and the draft is closed with it."""
    draft = _own(company_id, draft_id, ctx.actor.user_id)
    movement = records.record_manual(ctx, company_id, data)
    draft.state = ExpenseDraft.State.RECORDED
    draft.movement = movement
    draft.save(update_fields=["state", "movement"])
    ctx.audit(
        "expense_draft.recorded",
        object_type="expense_draft",
        object_id=draft.id,
        after={"movement": str(movement.id)},
    )
    return movement
