/** The act of inspection (ТЗ § 10.2.2, § 18.1.8; decision В-8.3): the client's signature under a
 * saved inspection is a document of the registry. It is made here, signed on the screen of this
 * device or by a personal link; an invalid inspection keeps its act with the signatures. */
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { CompanyDocument, Inspection } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { SigningBadge, SigningCard } from "../documents/Signing";
import { useTemplates } from "../documents/Templates";

function ActSigning({ documentId }: { documentId: string }) {
  const company = useCompany();
  const query = useApi<{ document: CompanyDocument }>(
    ["document", company.public_id, documentId],
    `/c/${company.public_id}/documents/${documentId}`,
  );
  if (query.isLoading) return <Loading />;
  if (!query.data) return <ErrorMessage error={query.error instanceof ApiError ? query.error : null} />;
  return <SigningCard d={query.data.document} />;
}

export function InspectionActCard({ inspection }: { inspection: Inspection }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const templates = useTemplates();
  const lang = i18n.language.slice(0, 2);
  const path = `object_type=inspection&object_id=${inspection.id}&archived=1`;
  const list = useApi<{ documents: CompanyDocument[] }>(
    ["documents", company.public_id, path],
    `/c/${company.public_id}/documents?${path}`,
    can("documents.view"),
  );
  const create = useCommand<Record<string, unknown>, { document: CompanyDocument }>(
    `/c/${company.public_id}/inspections/${inspection.id}/act`,
  );
  const options = (templates.data?.templates ?? []).filter(
    (x) => x.source === "inspection" && x.state === "active" && (!x.system || x.language === lang),
  );
  const [templateId, setTemplateId] = useState("");
  if (!can("documents.view") && !can("documents.create")) return null;
  const acts = (list.data?.documents ?? []).filter((d) => d.source_type === "inspection" && d.source_id === inspection.id);
  const active = acts.find((d) => d.state === "active");
  const canCreate = inspection.state === "saved" && !active && can("documents.create");

  async function make() {
    const chosen = templateId || options.find((x) => x.system)?.id || "";
    const result = await create.run({ template_id: chosen || null, language: lang });
    if (result) {
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
      await queryClient.invalidateQueries({ queryKey: ["document"] });
    }
  }

  return (
    <>
      <Card title={t("inspections.act_title")}>
        {list.isLoading ? <Loading /> : null}
        {inspection.state === "invalid" && acts.length ? <p className="small muted">{t("inspections.act_of_invalid")}</p> : null}
        {acts.length === 0 && !list.isLoading ? <p className="muted small">{t("inspections.act_none")}</p> : null}
        {acts.map((d) => (
          <div key={d.id} className="small">
            <Link to={companyPath(company, `/documents/${d.id}`)}>
              {t("documents.number", { number: d.number })} · {d.title}
            </Link>{" "}
            {d.state === "archived" ? <span className="badge">{t("documents.archived")}</span> : null} <SigningBadge state={d.signing.state} />
            {d.signing.state === "none" ? <span className="muted"> {t("inspections.act_no_signer")}</span> : null}
          </div>
        ))}
        {canCreate ? (
          <>
            <p className="small muted">{t("inspections.act_hint")}</p>
            {options.length > 1 ? (
              <Field label={t("documents.template")}>
                <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
                  <option value="">{options.find((x) => x.system)?.title ?? "—"}</option>
                  {options
                    .filter((x) => !x.system)
                    .map((x) => (
                      <option key={x.id} value={x.id}>
                        {x.title}
                      </option>
                    ))}
                </select>
              </Field>
            ) : null}
            <div className="actions">
              <button type="button" disabled={create.pending} onClick={() => void make()}>
                {t("inspections.act_create")}
              </button>
            </div>
          </>
        ) : null}
        <ErrorMessage error={create.error} />
      </Card>
      {active && can("documents.view") ? <ActSigning documentId={active.id} /> : null}
    </>
  );
}
