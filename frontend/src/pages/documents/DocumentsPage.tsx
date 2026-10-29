import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { ApiError, uploadFile } from "../../api/client";
import { useApi } from "../../api/queries";
import type { CompanyDocument, DocumentKind } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { SigningBadge, SigningCard } from "./Signing";
import { MakeDocument } from "./Templates";

type Result = { document: CompanyDocument };
export type LinkTarget = { objectType: string; objectId: string };

export function useDocumentKinds() {
  const company = useCompany();
  return useApi<{ kinds: DocumentKind[] }>(["document-kinds", company.public_id], `/c/${company.public_id}/document-kinds`);
}

export function useKindName(): (kind: DocumentKind | undefined) => string {
  const { t } = useTranslation();
  return (kind) => (kind ? (kind.name ?? t(`documents.kind_${kind.code}`)) : "—");
}

function useRefresh() {
  const queryClient = useQueryClient();
  return async () => {
    await queryClient.invalidateQueries({ queryKey: ["documents"] });
    await queryClient.invalidateQueries({ queryKey: ["document"] });
  };
}

/** The registry of company documents with the warnings of terms (§ 18.1.10, § 18.1.13). */
export function DocumentsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [filter, setFilter] = useState<"" | "warnings=1" | "archived=1">("");
  return (
    <div className="page">
      <h1>{t("nav.documents")}</h1>
      <div className="actions" style={{ marginBottom: 12, marginTop: 0 }}>
        <Link className="button secondary" to={companyPath(company, "/documents/templates")}>
          {t("documents.templates")}
        </Link>
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <Field label={t("documents.show")}>
          <select value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)}>
            <option value="">{t("documents.show_active")}</option>
            <option value="warnings=1">{t("documents.show_warnings")}</option>
            <option value="archived=1">{t("documents.show_all")}</option>
          </select>
        </Field>
      </div>
      <DocumentList query={filter} />
      {can("documents.upload") ? <UploadDocument /> : null}
    </div>
  );
}

function DocumentList({ query, target }: { query: string; target?: LinkTarget }) {
  const { t } = useTranslation();
  const company = useCompany();
  const kinds = useDocumentKinds();
  const kindName = useKindName();
  const path = target ? `object_type=${target.objectType}&object_id=${target.objectId}${query ? `&${query}` : ""}` : query;
  const list = useApi<{ documents: CompanyDocument[] }>(
    ["documents", company.public_id, path],
    `/c/${company.public_id}/documents${path ? `?${path}` : ""}`,
  );
  const items = list.data?.documents ?? [];
  return (
    <Card>
      {list.isLoading ? <Loading /> : null}
      {list.data && items.length === 0 ? <p className="muted">{t("documents.none")}</p> : null}
      {items.length ? (
        <table>
          <tbody>
            {items.map((d) => (
              <tr key={d.id}>
                <td>
                  <Link to={companyPath(company, `/documents/${d.id}`)}>№ {d.number}</Link>
                </td>
                <td>{d.title}</td>
                <td className="small">{kindName(kinds.data?.kinds.find((k) => k.id === d.kind_id))}</td>
                <td className="small">{d.document_date}</td>
                <td>
                  {d.warning ? <span className="badge warn">{t(`documents.warning_${d.warning}`, { date: d.valid_until })}</span> : null}
                  {d.state === "archived" ? <span className="badge">{t("documents.archived")}</span> : null}{" "}
                  <SigningBadge state={d.signing.state} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}

/** A ready document, scan or photo (§ 18.1.3), optionally linked to a card. */
function UploadDocument({ target }: { target?: LinkTarget }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const kinds = useDocumentKinds();
  const kindName = useKindName();
  const [title, setTitle] = useState("");
  const [kindId, setKindId] = useState("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [file, setFile] = useState<File | null>(null);
  const [validUntil, setValidUntil] = useState("");
  const [warnDays, setWarnDays] = useState("");
  const [uploadError, setUploadError] = useState<ApiError | null>(null);
  const create = useCommand<Record<string, unknown>, Result>(`/c/${company.public_id}/documents`);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setUploadError(null);
    let fileId: string;
    try {
      fileId = (await uploadFile("company_document", file)).id;
    } catch (caught) {
      setUploadError(caught instanceof ApiError ? caught : new ApiError(0, null));
      return;
    }
    const result = await create.run({
      title,
      kind_id: kindId,
      document_date: date,
      file_id: fileId,
      file_name: file.name,
      valid_until: validUntil || null,
      warn_days: warnDays || null,
      links: target ? [{ object_type: target.objectType, object_id: target.objectId }] : [],
    });
    if (result) {
      setTitle("");
      setFile(null);
      setValidUntil("");
      setWarnDays("");
      await refresh();
    }
  }
  return (
    <Card title={t("documents.upload")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("documents.title")}>
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field label={t("documents.kind")}>
            <select value={kindId} onChange={(e) => setKindId(e.target.value)}>
              <option value="">—</option>
              {(kinds.data?.kinds ?? []).map((k) => (
                <option key={k.id} value={k.id}>
                  {kindName(k)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("documents.date")}>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
        </div>
        <Field label={t("documents.file")} hint={t("documents.file_hint")}>
          <input type="file" accept=".pdf,.docx,.xlsx,image/jpeg,image/png,image/webp" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Field>
        <div className="row">
          <Field label={t("documents.valid_until")}>
            <input type="date" value={validUntil} onChange={(e) => setValidUntil(e.target.value)} />
          </Field>
          <Field label={t("documents.warn_days")}>
            <input inputMode="numeric" value={warnDays} onChange={(e) => setWarnDays(e.target.value)} />
          </Field>
        </div>
        <ErrorMessage error={uploadError ?? create.error} />
        <div className="actions">
          <button type="submit" disabled={create.pending || !title.trim() || !kindId || !file}>
            {t("documents.upload_button")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** Documents of a card: the same documents, seen through the link, by the rights on documents
 * (§ 18.1.9, § 18.5.3). */
export function LinkedDocuments({ objectType, objectId }: LinkTarget) {
  const { t } = useTranslation();
  const can = useCan();
  const refresh = useRefresh();
  const [adding, setAdding] = useState(false);
  if (!can("documents.view") && !can("documents.upload")) return null;
  return (
    <>
      <h2>{t("nav.documents")}</h2>
      {can("documents.view") ? <DocumentList query="" target={{ objectType, objectId }} /> : null}
      {can("documents.create") ? <MakeDocument objectType={objectType} objectId={objectId} onDone={refresh} /> : null}
      {can("documents.upload") ? (
        adding ? (
          <UploadDocument target={{ objectType, objectId }} />
        ) : (
          <div className="actions" style={{ marginBottom: 16 }}>
            <button type="button" className="secondary" onClick={() => setAdding(true)}>
              {t("documents.upload")}
            </button>
          </div>
        )
      ) : null}
    </>
  );
}

const OBJECT_PATHS: Record<string, string> = {
  client: "/clients/",
  counterparty: "/clients/counterparties/",
  vehicle: "/vehicles/",
  order: "/orders/",
  rental: "/rentals/",
  service_operation: "/service/operations/",
  delivery: "/trips/",
};

/** A document: details, versions, links, term and archive (§ 18.1.9–18.1.14). */
export function DocumentPage() {
  const { t } = useTranslation();
  const { id } = useParams();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh();
  const kinds = useDocumentKinds();
  const kindName = useKindName();
  const query = useApi<Result>(["document", company.public_id, id], `/c/${company.public_id}/documents/${id}`, Boolean(id));
  const d = query.data?.document;
  const [file, setFile] = useState<File | null>(null);
  const [validUntil, setValidUntil] = useState("");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [uploadError, setUploadError] = useState<ApiError | null>(null);
  const base = `/c/${company.public_id}/documents/${id}`;
  const addVersion = useCommand<Record<string, unknown>, Result>(`${base}/versions`);
  const archive = useCommand<Record<string, unknown>, Result>(`${base}/archive`);
  const unlink = useCommand<Record<string, unknown>, Result>(`${base}/unlink`);
  const remake = useCommand<Record<string, unknown>, Result>(`${base}/remake`);
  if (query.isLoading) return <Loading />;
  if (!d) return <ErrorMessage error={query.error instanceof ApiError ? query.error : null} />;
  const active = d.state === "active";

  async function newVersion(event: FormEvent) {
    event.preventDefault();
    if (!file || !d) return;
    setUploadError(null);
    let fileId: string;
    try {
      fileId = (await uploadFile("company_document", file)).id;
    } catch (caught) {
      setUploadError(caught instanceof ApiError ? caught : new ApiError(0, null));
      return;
    }
    const result = await addVersion.run({ expected_version: d.version, file_id: fileId, file_name: file.name, valid_until: validUntil || null, note });
    if (result) {
      setFile(null);
      setNote("");
      await refresh();
    }
  }

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/documents")}>← {t("nav.documents")}</Link>
      </p>
      <h1>
        {t("documents.number", { number: d.number })} · {d.title}{" "}
        {d.state === "archived" ? <span className="badge">{t("documents.archived")}</span> : null}
      </h1>
      <Card>
        <dl className="facts">
          <dt>{t("documents.kind")}</dt>
          <dd>{kindName(kinds.data?.kinds.find((k) => k.id === d.kind_id))}</dd>
          <dt>{t("documents.date")}</dt>
          <dd>{d.document_date}</dd>
          <dt>{t("documents.scope")}</dt>
          <dd>{d.all_locations ? t("documents.whole_company") : t("documents.locations_count", { count: d.locations.length })}</dd>
          <dt>{t("documents.valid_until")}</dt>
          <dd>
            {d.valid_until ?? "—"}
            {d.warning ? <span className="badge warn"> {t(`documents.warning_${d.warning}`, { date: d.valid_until })}</span> : null}
          </dd>
          {d.archive_reason ? (
            <>
              <dt>{t("documents.archive_reason")}</dt>
              <dd>{d.archive_reason}</dd>
            </>
          ) : null}
        </dl>
      </Card>
      {d.versions?.[0]?.content ? (
        <Card title={t("documents.content_title")}>
          <div className="actions" style={{ marginTop: 0 }}>
            <Link className="button secondary" to={companyPath(company, `/documents/${d.id}/print/${d.versions[0].number}`)}>
              {t("documents.print")}
            </Link>
            {active && d.template_id && can("documents.create") ? (
              <button
                type="button"
                className="secondary"
                disabled={remake.pending}
                onClick={() => void remake.run({ expected_version: d.version }).then((r) => (r ? refresh() : undefined))}
              >
                {t("documents.remake")}
              </button>
            ) : null}
          </div>
          <ErrorMessage error={remake.error} />
          <div className="document-content" dangerouslySetInnerHTML={{ __html: d.versions[0].content }} />
        </Card>
      ) : null}
      <SigningCard d={d} />
      <Card title={t("documents.versions")}>
        {(d.versions ?? []).map((v) => (
          <div key={v.number} className="small">
            {t("documents.version", { number: v.number })} ·{" "}
            {v.file_id ? (
              <a href={`/api/files/${v.file_id}`} target="_blank" rel="noreferrer">
                {v.file_name || t("documents.open_file")}
              </a>
            ) : (
              <Link to={companyPath(company, `/documents/${d.id}/print/${v.number}`)}>{t("documents.open_content")}</Link>
            )}
            {v.valid_until ? ` · ${t("documents.valid_until")}: ${v.valid_until}` : ""}
            {v.note ? ` · ${v.note}` : ""}
          </div>
        ))}
        {active && can("documents.upload") ? (
          <form className="row" onSubmit={newVersion} style={{ marginTop: 8 }}>
            <Field label={t("documents.new_version")} hint={t("documents.file_hint")}>
              <input type="file" accept=".pdf,.docx,.xlsx,image/jpeg,image/png,image/webp" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </Field>
            <Field label={t("documents.valid_until")}>
              <input type="date" value={validUntil} onChange={(e) => setValidUntil(e.target.value)} />
            </Field>
            <Field label={t("documents.note")}>
              <input value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>
            <div className="actions">
              <button type="submit" className="secondary" disabled={addVersion.pending || !file}>
                {t("documents.add_version")}
              </button>
            </div>
            <ErrorMessage error={uploadError ?? addVersion.error} />
          </form>
        ) : null}
      </Card>
      <Card title={t("documents.links")}>
        {(d.links ?? []).length === 0 ? <p className="muted">{t("documents.no_links")}</p> : null}
        {(d.links ?? []).map((link) => (
          <div key={link.id} className="small">
            <Link to={companyPath(company, `${OBJECT_PATHS[link.object_type] ?? "/"}${link.object_id}`)}>{t(`documents.object_${link.object_type}`)}</Link>{" "}
            {active && can("documents.upload") ? (
              <button
                type="button"
                className="link"
                onClick={() => void unlink.run({ expected_version: d.version, link_id: link.id }).then((r) => (r ? refresh() : undefined))}
              >
                {t("documents.unlink")}
              </button>
            ) : null}
          </div>
        ))}
        <ErrorMessage error={unlink.error} />
      </Card>
      {active && can("documents.archive") ? (
        <Card title={t("documents.archive")}>
          <div className="row">
            <Field label={t("documents.archive_reason")}>
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
            <div className="actions">
              <button
                type="button"
                className="danger"
                disabled={archive.pending || !reason.trim()}
                onClick={() => void archive.run({ expected_version: d.version, reason }).then((r) => (r ? refresh() : undefined))}
              >
                {t("documents.archive_button")}
              </button>
            </div>
          </div>
          <ErrorMessage error={archive.error} />
        </Card>
      ) : null}
    </div>
  );
}
