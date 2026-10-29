import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { CompanyDocument, DocumentTemplate } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { useDocumentKinds, useKindName } from "./DocumentsPage";
import { KindSigningCard } from "./Signing";

type Catalog = {
  fields: { code: string; source: string | null }[];
  tables: { code: string; columns: string[]; source: string }[];
  sources: Record<string, string[]>;
};

const SOURCES = ["rental", "order", "service_operation", "delivery", "client", "vehicle", "none"] as const;

export function useTemplates() {
  const company = useCompany();
  return useApi<{ templates: DocumentTemplate[] }>(
    ["document-templates", company.public_id],
    `/c/${company.public_id}/document-templates`,
  );
}

function useCatalog() {
  const company = useCompany();
  return useApi<Catalog>(["document-fields", company.public_id], `/c/${company.public_id}/document-fields`);
}

export function fieldLabel(code: string, t: ReturnType<typeof useTranslation>["t"]): string {
  return t(`document_fields.${code.replace(".", "__")}`, { defaultValue: code });
}

/** Templates: the starting ones of the system and the company's own (§ 18.1.5). */
export function TemplatesPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const list = useTemplates();
  const kinds = useDocumentKinds();
  const kindName = useKindName();
  const copy = useCommand<Record<string, unknown>, { template: DocumentTemplate }>(`/c/${company.public_id}/document-templates`);
  const items = list.data?.templates ?? [];
  async function copyOf(id: string) {
    const result = await copy.run({ copy_from: id });
    if (result) {
      await queryClient.invalidateQueries({ queryKey: ["document-templates"] });
      navigate(companyPath(company, `/documents/templates/${result.template.id}`));
    }
  }
  const row = (x: DocumentTemplate) => (
    <tr key={x.id}>
      <td>
        {x.system ? x.title : <Link to={companyPath(company, `/documents/templates/${x.id}`)}>{x.title}</Link>}{" "}
        {x.state === "archived" ? <span className="badge">{t("documents.archived")}</span> : null}
      </td>
      <td className="small">{kindName(kinds.data?.kinds.find((k) => k.id === x.kind_id))}</td>
      <td className="small">{t(`documents.source_${x.source}`)}</td>
      <td className="small">{x.language ? x.language.toUpperCase() : "—"}</td>
      <td>
        {can("documents.templates") ? (
          <button type="button" className="secondary small" disabled={copy.pending} onClick={() => void copyOf(x.id)}>
            {t("documents.copy_template")}
          </button>
        ) : null}
      </td>
    </tr>
  );
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/documents")}>← {t("nav.documents")}</Link>
      </p>
      <h1>{t("documents.templates")}</h1>
      {list.isLoading ? <Loading /> : null}
      <ErrorMessage error={copy.error} />
      <Card title={t("documents.own_templates")}>
        {items.filter((x) => !x.system).length === 0 ? <p className="muted">{t("documents.no_own_templates")}</p> : null}
        <table>
          <tbody>{items.filter((x) => !x.system).map(row)}</tbody>
        </table>
      </Card>
      <Card title={t("documents.starting_templates")}>
        <table>
          <tbody>{items.filter((x) => x.system).map(row)}</tbody>
        </table>
      </Card>
      <KindSigningCard />
    </div>
  );
}

/** Editing a template of the company: fields picked from the list are put as codes (§ 18.2.4). */
export function TemplatePage() {
  const { t } = useTranslation();
  const { id } = useParams();
  const company = useCompany();
  const queryClient = useQueryClient();
  const query = useApi<{ template: DocumentTemplate }>(["document-template", company.public_id, id], `/c/${company.public_id}/document-templates/${id}`, Boolean(id));
  const template = query.data?.template;
  if (query.isLoading) return <Loading />;
  if (!template) return <ErrorMessage error={query.error instanceof ApiError ? query.error : null} />;
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/documents/templates")}>← {t("documents.templates")}</Link>
      </p>
      <h1>{template.title}</h1>
      <TemplateForm
        key={template.version}
        template={template}
        onSaved={async () => {
          await queryClient.invalidateQueries({ queryKey: ["document-template"] });
          await queryClient.invalidateQueries({ queryKey: ["document-templates"] });
        }}
      />
    </div>
  );
}

function TemplateForm({ template, onSaved }: { template: DocumentTemplate; onSaved: () => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const kinds = useDocumentKinds();
  const kindName = useKindName();
  const catalog = useCatalog();
  const area = useRef<HTMLTextAreaElement>(null);
  const [title, setTitle] = useState(template.title);
  const [kindId, setKindId] = useState(template.kind_id);
  const [source, setSource] = useState(template.source);
  const [language, setLanguage] = useState(template.language);
  const [content, setContent] = useState(template.content);
  const [required, setRequired] = useState<string[]>(template.required_fields);
  const [signers, setSigners] = useState<Record<string, boolean>>(template.signers);
  const save = useCommand<Record<string, unknown>, { template: DocumentTemplate }>(`/c/${company.public_id}/document-templates/${template.id}`, "PATCH");
  const available = new Set(catalog.data?.sources[source] ?? []);
  const fields = (catalog.data?.fields ?? []).filter((f) => !f.source || available.has(f.source));
  const tables = (catalog.data?.tables ?? []).filter((f) => available.has(f.source));
  const used = [...new Set([...content.matchAll(/\{\{\s*([a-z_]+\.[a-z_]+)\s*\}\}/g)].map((m) => m[1] ?? ""))];

  function insert(code: string) {
    const element = area.current;
    const start = element?.selectionStart ?? content.length;
    const end = element?.selectionEnd ?? content.length;
    setContent(content.slice(0, start) + code + content.slice(end));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await save.run({
      title,
      kind_id: kindId,
      source,
      language,
      content,
      required_fields: required.filter((c) => used.includes(c)),
      signers,
      expected_version: template.version,
    });
    if (result) await onSaved();
  }

  return (
    <Card>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("documents.title")}>
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field label={t("documents.kind")}>
            <select value={kindId} onChange={(e) => setKindId(e.target.value)}>
              {(kinds.data?.kinds ?? []).map((k) => (
                <option key={k.id} value={k.id}>
                  {kindName(k)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("documents.source")}>
            <select value={source} onChange={(e) => setSource(e.target.value as DocumentTemplate["source"])}>
              {SOURCES.map((x) => (
                <option key={x} value={x}>
                  {t(`documents.source_${x}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("documents.language")}>
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="">—</option>
              <option value="ru">RU</option>
              <option value="en">EN</option>
              <option value="th">TH</option>
            </select>
          </Field>
        </div>
        <Field label={t("documents.content")} hint={t("documents.content_hint")}>
          <textarea ref={area} rows={18} value={content} onChange={(e) => setContent(e.target.value)} style={{ fontFamily: "monospace" }} />
        </Field>
        <Field label={t("documents.insert_field")}>
          <select value="" onChange={(e) => e.target.value && insert(e.target.value)}>
            <option value="">—</option>
            {fields.map((f) => (
              <option key={f.code} value={`{{${f.code}}}`}>
                {fieldLabel(f.code, t)} · {`{{${f.code}}}`}
              </option>
            ))}
            {tables.map((f) => (
              <option key={f.code} value={`\n{{table:${f.code}}}\n`}>
                {fieldLabel(f.code, t)} · {`{{table:${f.code}}}`}
              </option>
            ))}
          </select>
        </Field>
        {used.length ? (
          <Field label={t("documents.required_fields")} hint={t("documents.required_hint")}>
            <div className="toggle-grid">
              {used.map((code) => (
                <label key={code} className="small">
                  <input
                    type="checkbox"
                    checked={required.includes(code)}
                    onChange={() => setRequired(required.includes(code) ? required.filter((x) => x !== code) : [...required, code])}
                  />{" "}
                  {fieldLabel(code, t)} <span className="muted">{`{{${code}}}`}</span>
                </label>
              ))}
            </div>
          </Field>
        ) : null}
        <Field label={t("documents.signers")}>
          <div className="choice-row">
            {(["company", "client", "counterparty"] as const).map((x) => (
              <label key={x} className="small">
                <input type="checkbox" checked={Boolean(signers[x])} onChange={() => setSigners({ ...signers, [x]: !signers[x] })} /> {t(`documents.signer_${x}`)}
              </label>
            ))}
          </div>
        </Field>
        <ErrorMessage error={save.error} />
        <div className="actions">
          <button type="submit" disabled={save.pending || !title.trim()}>
            {t("common.save")}
          </button>
        </div>
      </form>
    </Card>
  );
}

const TEMPLATE_SOURCE: Record<string, string> = {
  rental: "rental",
  order: "order",
  service_operation: "service_operation",
  delivery: "delivery",
  client: "client",
  vehicle: "vehicle",
};

/** A document made by a template for a card: preview with the fields without data, then the
 * document with the values fixed (§ 18.2.1, § 18.2.4). */
export function MakeDocument({ objectType, objectId, onDone }: { objectType: string; objectId: string; onDone: () => Promise<void> }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const list = useTemplates();
  const source = TEMPLATE_SOURCE[objectType];
  const lang = i18n.language.slice(0, 2);
  const options = (list.data?.templates ?? []).filter(
    (x) => x.source === source && x.state === "active" && (!x.system || x.language === lang),
  );
  const [templateId, setTemplateId] = useState("");
  const [preview, setPreview] = useState<{ html: string; missing_required: string[]; missing_optional: string[] } | null>(null);
  const show = useCommand<Record<string, unknown>, { html: string; missing_required: string[]; missing_optional: string[] }>(`/c/${company.public_id}/documents/preview`);
  const make = useCommand<Record<string, unknown>, { document: CompanyDocument }>(`/c/${company.public_id}/documents/from-template`);
  if (!source) return null;

  async function look(id: string) {
    setTemplateId(id);
    setPreview(null);
    if (!id) return;
    const result = await show.run({ template_id: id, source_id: objectId });
    if (result) setPreview(result);
  }

  async function create() {
    const result = await make.run({ template_id: templateId, source_id: objectId });
    if (result) {
      await onDone();
      navigate(companyPath(company, `/documents/${result.document.id}`));
    }
  }

  return (
    <Card title={t("documents.make")}>
      <Field label={t("documents.template")}>
        <select value={templateId} onChange={(e) => void look(e.target.value)}>
          <option value="">—</option>
          {options.map((x) => (
            <option key={x.id} value={x.id}>
              {x.title}
            </option>
          ))}
        </select>
      </Field>
      {preview ? (
        <>
          {preview.missing_required.length ? (
            <p className="alert error">
              {t("documents.missing_required")} {preview.missing_required.map((c) => fieldLabel(c, t)).join(", ")}
            </p>
          ) : null}
          {preview.missing_optional.length ? (
            <p className="alert warn">
              {t("documents.missing_optional")} {preview.missing_optional.map((c) => fieldLabel(c, t)).join(", ")}
            </p>
          ) : null}
          <div className="document-content" dangerouslySetInnerHTML={{ __html: preview.html }} />
          <div className="actions">
            <button type="button" disabled={make.pending || preview.missing_required.length > 0} onClick={() => void create()}>
              {t("documents.make_button")}
            </button>
          </div>
        </>
      ) : null}
      <ErrorMessage error={show.error ?? make.error} />
    </Card>
  );
}

/** The content of a document by itself, for printing or saving as PDF from the browser
 * (decision Р-1). */
export function DocumentPrintPage() {
  const { t } = useTranslation();
  const { id, version } = useParams();
  const company = useCompany();
  const query = useApi<{ document: CompanyDocument }>(["document", company.public_id, id], `/c/${company.public_id}/documents/${id}`, Boolean(id));
  const d = query.data?.document;
  if (query.isLoading) return <Loading />;
  if (!d) return <ErrorMessage error={query.error instanceof ApiError ? query.error : null} />;
  const chosen = (d.versions ?? []).find((v) => String(v.number) === version) ?? d.versions?.[0];
  return (
    <div className="page print-page">
      <div className="actions no-print">
        <Link to={companyPath(company, `/documents/${d.id}`)}>← {d.title}</Link>
        <button type="button" onClick={() => window.print()}>
          {t("documents.print")}
        </button>
      </div>
      <div className="document-content" dangerouslySetInnerHTML={{ __html: chosen?.content ?? "" }} />
    </div>
  );
}
