"""The act of inspection (ТЗ § 10.2.1–10.2.2, § 18.1.8, § 18.4.2; customer's decision В-8.3 of
30.10.2026).

* The client's signature under an inspection is given through a document of the registry, the
  act of inspection, made by a template of the source «inspection» from a saved inspection. The
  version keeps what the inspection showed when the act was made (§ 18.2.4): the confirmation is
  of that version (§ 10.2.2).
* The act is linked to the inspection, its rental or service operation, the vehicle and the
  signer's card. It is sent at once to the signer: the participant when it is a client or a
  counterparty, else the client of the rental or of the order of the service operation.
* An inspection recognised invalid keeps its act and signatures; a new inspection has its own
  act, nothing is carried over (§ 10.1.6, § 10.2.2).
* Whether the signature is required is set by the documents of the rental's stage (§ 18.4.1).

TODO(spec): one active act per inspection — PROPOSAL; an archived act lets a new one be made.
TODO(spec): whether the company's representative also signs the act is not stated — PROPOSAL:
the act is sent to the client's side only; the company may add its signer by a new version.
"""

from uuid import UUID

from django.utils import timezone

from c4rsys.companies.models import Company
from c4rsys.documents import services, signing, templates
from c4rsys.documents.models import Document, DocumentTemplate
from c4rsys.platform import errors
from c4rsys.platform.commands import CommandContext

CODE = "inspection_act"
LANGUAGES = ("en", "ru", "th")


def acts_of(company_id: UUID, inspection_id: UUID) -> list[Document]:
    return list(
        services.linked_to(company_id, "inspection", inspection_id)
        .filter(source_type="inspection", source_id=inspection_id)
        .order_by("number")
    )


def _template(company_id: UUID, template_id: UUID | None, language: str) -> DocumentTemplate:
    if template_id is not None:
        t = templates.get(company_id, template_id)
        if t.source != DocumentTemplate.Source.INSPECTION:
            raise errors.validation_error("template_id", code="TEMPLATE_SOURCE_MISMATCH")
        return t
    wanted = language if language in LANGUAGES else "en"
    found = DocumentTemplate.objects.filter(
        company__isnull=True, code=CODE, language=wanted
    ).first()
    if found is None:
        raise errors.validation_error("template_id", code="TEMPLATE_UNKNOWN")
    return found


def _signer(company_id: UUID, d: Document) -> signing.Person | None:
    """The participant's card if it is a client or a counterparty, else the operation's client:
    both are among the links the template's data gave (`fields._inspection`)."""
    roles = {link.object_type: link.object_id for link in services.links_of(d)}
    if "counterparty" in roles:
        return signing.Person(signing.Role.COUNTERPARTY, roles["counterparty"])
    if "client" in roles:
        return signing.Person(signing.Role.CLIENT, roles["client"])
    return None


def make(
    ctx: CommandContext,
    company: Company,
    inspection_id: UUID,
    *,
    template_id: UUID | None,
    language: str,
) -> Document:
    """The act of a saved inspection, sent to its signer. The inspection is locked while the act
    is made, so two acts of one inspection are not made at once."""
    from c4rsys.inspections import services as inspections

    inspection = inspections.get(company.id, inspection_id, lock=True)
    if inspection.state != "saved":
        raise errors.business_rule("INSPECTION_ACT_NOT_SAVED", state=inspection.state)
    if any(d.state == Document.State.ACTIVE for d in acts_of(company.id, inspection_id)):
        raise errors.business_rule("INSPECTION_ACT_EXISTS")
    t = _template(company.id, template_id, language)
    d = templates.make(ctx, company, t, inspection_id, title="", on=timezone.localdate(), act=True)
    person = _signer(company.id, d)
    # A company's own template may need more signers: then the act is sent from its card.
    wanted = {role for role, needed in t.signers.items() if needed}
    if person is not None and wanted <= {person.role}:
        signing.send(ctx, d, [person])
    return d
