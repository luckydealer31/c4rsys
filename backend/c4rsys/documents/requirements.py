"""Required documents (ТЗ § 18.4.1, § 16.7.5–16.7.6; customer's decisions Р-8, Р-9 of 29.10.2026).

* Rentals: the company names templates or kinds for the stages «before the issue», «at the issue»
  and «at the return», each «present» or «signed». A document counts when it is active, linked to
  the rental and made by that template (a starting template in any of its languages) or of that
  kind. At the issue an unmet one goes through the issue policy «Documents»; at the return it
  keeps the formalization from completing.
* Service: «act required» and «act signed by the client». The act is an active document of the
  kind «act» linked to the service operation; signed — all signers of its last sent version have
  signed and a client is among them (§ 18.3.6). An internal order needs no act (§ 16.6.4).
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.documents import services, signing
from c4rsys.documents.models import (
    Document,
    DocumentKind,
    DocumentRequirement,
    DocumentSigner,
    DocumentTemplate,
    ServiceActSetting,
)
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

Stage = DocumentRequirement.Stage
Need = DocumentRequirement.Need
AT_ISSUE = (Stage.BEFORE_ISSUE, Stage.AT_ISSUE)


# --- Rentals (§ 18.4.1) --------------------------------------------------------------------------


def of_company(company_id: UUID) -> list[DocumentRequirement]:
    return list(
        DocumentRequirement.objects.filter(company_id=company_id)
        .select_related("template", "kind")
        .order_by("stage", "created_at")
    )


def snapshot(r: DocumentRequirement) -> dict[str, Any]:
    return {
        "stage": r.stage,
        "template": str(r.template_id) if r.template_id else None,
        "kind": str(r.kind_id) if r.kind_id else None,
        "need": r.need,
    }


def add(
    ctx: CommandContext,
    company: Company,
    *,
    stage: str,
    need: str,
    template_id: UUID | None,
    kind_id: UUID | None,
) -> DocumentRequirement:
    if stage not in Stage.values:
        raise errors.validation_error("stage", code="CHOICE_REQUIRED")
    if need not in Need.values:
        raise errors.validation_error("need", code="CHOICE_REQUIRED")
    if (template_id is None) == (kind_id is None):
        raise errors.validation_error("template_id", code="CHOICE_REQUIRED")
    if template_id is not None:
        found = (
            DocumentTemplate.objects.filter(pk=template_id)
            .filter(Q(company__isnull=True) | Q(company_id=company.id))
            .first()
        )
        if found is None:
            raise errors.validation_error("template_id", code="TEMPLATE_UNKNOWN")
    if kind_id is not None:
        services.kind_of(company.id, kind_id)
    duplicate = DocumentRequirement.objects.filter(
        company=company, stage=stage, template_id=template_id, kind_id=kind_id
    ).exists()
    if duplicate:
        raise errors.business_rule("DOCUMENT_REQUIREMENT_EXISTS")
    item = DocumentRequirement.objects.create(
        company=company,
        stage=stage,
        need=need,
        template_id=template_id,
        kind_id=kind_id,
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.audit(
        "document_requirement.added",
        object_type="document_requirement",
        object_id=item.id,
        after=snapshot(item),
    )
    return item


def remove(ctx: CommandContext, company: Company, requirement_id: UUID) -> None:
    item = (
        DocumentRequirement.objects.select_for_update()
        .filter(company=company, pk=requirement_id)
        .first()
    )
    if item is None:
        raise errors.not_found()
    before = snapshot(item)
    item.delete()
    ctx.audit(
        "document_requirement.removed",
        object_type="document_requirement",
        object_id=requirement_id,
        before=before,
    )


def _templates_like(template: DocumentTemplate) -> list[UUID]:
    """A starting template counts in each of its languages."""
    if template.company_id is None:
        return list(
            DocumentTemplate.objects.filter(company__isnull=True, code=template.code).values_list(
                "id", flat=True
            )
        )
    return [template.id]


def _met(r: DocumentRequirement, documents: list[Document]) -> bool:
    if r.template is not None:
        ids = set(_templates_like(r.template))
        matching = [d for d in documents if d.template_id in ids]
    else:
        matching = [d for d in documents if d.kind_id == r.kind_id]
    if r.need == Need.PRESENT:
        return bool(matching)
    return any(signing.is_signed(d) for d in matching)


def _label(r: DocumentRequirement) -> dict[str, Any]:
    if r.template is not None:
        return {"template": r.template.title}
    kind: DocumentKind | None = r.kind
    return {"kind_code": kind.code or None, "kind_name": kind.name or None} if kind else {}


def unmet_for_rental(
    company_id: UUID, rental_id: UUID | None, stages: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Requirements of the stages not met by the documents linked to the rental. Before a rental
    exists (an issue without a booking) nothing is linked to it yet."""
    wanted = [r for r in of_company(company_id) if r.stage in stages]
    if not wanted:
        return []
    documents = (
        list(
            services.linked_to(company_id, "rental", rental_id).filter(state=Document.State.ACTIVE)
        )
        if rental_id is not None
        else []
    )
    return [
        {"id": str(r.id), "stage": r.stage, "need": r.need, **_label(r)}
        for r in wanted
        if not _met(r, documents)
    ]


# --- Service act (§ 16.7.6) ----------------------------------------------------------------------


@dataclass(frozen=True)
class ActSetting:
    act_required: bool
    act_signed: bool
    version: int


def service_act(company_id: UUID) -> ActSetting:
    found = ServiceActSetting.objects.filter(company_id=company_id).first()
    if found is None:
        return ActSetting(False, False, 0)
    return ActSetting(found.act_required, found.act_signed, found.version)


def set_service_act(
    ctx: CommandContext, company: Company, act_required: bool, act_signed: bool, expected: int
) -> ActSetting:
    if act_signed and not act_required:
        raise errors.validation_error("act_signed", code="SERVICE_ACT_SIGNED_NEEDS_REQUIRED")
    found = ServiceActSetting.objects.select_for_update().filter(company=company).first()
    if (found.version if found else 0) != expected:
        raise errors.version_conflict()
    before = {"act_required": found.act_required, "act_signed": found.act_signed} if found else None
    if found is None:
        found = ServiceActSetting(company=company, version=0)
    found.act_required = act_required
    found.act_signed = act_signed
    found.updated_by_user_id = ctx.actor.user_id
    found.updated_at = timezone.now()
    found.version += 1
    found.save()
    ctx.audit(
        "service.act_setting_changed",
        object_type="service_act_setting",
        object_id=company.id,
        before=before,
        after={"act_required": act_required, "act_signed": act_signed},
    )
    return ActSetting(act_required, act_signed, found.version)


def _client_signed(d: Document) -> bool:
    version = signing.sent_version(d)
    return (
        version is not None
        and signing.is_signed(d)
        and DocumentSigner.objects.filter(
            document_version=version, role=DocumentSigner.Role.CLIENT
        ).exists()
    )


def service_act_blockers(company_id: UUID, operation_id: UUID) -> tuple[str, ...]:
    """Conditions of closing a client's order with this service operation."""
    setting = service_act(company_id)
    if not setting.act_required:
        return ()
    acts = [
        d
        for d in services.linked_to(company_id, "service_operation", operation_id).filter(
            state=Document.State.ACTIVE, kind__company__isnull=True, kind__code="act"
        )
    ]
    if not acts:
        return ("service_act_missing",)
    if setting.act_signed and not any(_client_signed(d) for d in acts):
        return ("service_act_not_signed",)
    return ()
