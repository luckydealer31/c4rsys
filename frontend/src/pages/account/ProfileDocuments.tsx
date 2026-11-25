import { useTranslation } from "react-i18next";

import type { ProfileDocument } from "../../api/types";
import { Field, IconButton } from "../../components/ui";

/** The documents of the profile (customer's remarks of 25–26.11.2026): the person adds those they
 * wish, choosing the kind of each — a passport, an ID card, a driving licence or another one;
 * none is required. Each has a number, the dates and who issued it; a licence has categories,
 * another document a name. */
export const PROFILE_DOCUMENT_TYPES = ["passport", "national_id", "driver_license", "other"] as const;

/** A row of the form; the categories are typed as «B, C». */
export type DocumentDraft = {
  type: string;
  title: string;
  number: string;
  issued_on: string;
  expires_on: string;
  issued_by: string;
  categories: string;
};

const EMPTY: DocumentDraft = {
  type: "",
  title: "",
  number: "",
  issued_on: "",
  expires_on: "",
  issued_by: "",
  categories: "",
};

export function draftsOf(documents: ProfileDocument[] | undefined): DocumentDraft[] {
  return (documents ?? []).map((d) => ({
    type: d.type,
    title: d.title ?? "",
    number: d.number,
    issued_on: d.issued_on ?? "",
    expires_on: d.expires_on ?? "",
    issued_by: d.issued_by ?? "",
    categories: (d.categories ?? []).join(", "),
  }));
}

/** What is sent: a row left untouched is not a document. */
export function documentsOf(rows: DocumentDraft[]): Record<string, unknown>[] {
  return rows
    .filter((r) => r.type || r.number.trim() || r.title.trim())
    .map((r) => ({
      type: r.type,
      title: r.type === "other" ? r.title : "",
      number: r.number,
      issued_on: r.issued_on || null,
      expires_on: r.expires_on || null,
      issued_by: r.issued_by,
      categories: r.type === "driver_license" ? r.categories.split(/[,\s]+/).filter(Boolean) : [],
    }));
}

function formatDate(value: string | null | undefined, locale: string): string {
  if (!value) return "";
  return new Intl.DateTimeFormat(locale, { timeZone: "UTC", dateStyle: "long" }).format(
    new Date(`${value}T00:00:00Z`),
  );
}

export function documentKind(t: (key: string) => string, d: { type: string; title?: string }): string {
  return d.type === "other" ? d.title || t("profile.document_other") : t(`profile.document_${d.type}`);
}

/** «AB 1234567 · categories: B, C · issued 10 January 2020 · valid until … · Issued by: …». */
export function documentLine(
  t: (key: string, options?: Record<string, string>) => string,
  locale: string,
  d: ProfileDocument,
): string {
  return [
    d.number,
    d.categories?.length ? `${t("cards.categories_short")}: ${d.categories.join(", ")}` : "",
    d.issued_on ? t("profile.issued_on_short", { date: formatDate(d.issued_on, locale) }) : "",
    d.expires_on ? t("profile.valid_until_short", { date: formatDate(d.expires_on, locale) }) : "",
    d.issued_by ? `${t("cards.issued_by")}: ${d.issued_by}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

export function DocumentsEditor({
  rows,
  onChange,
}: {
  rows: DocumentDraft[];
  onChange: (rows: DocumentDraft[]) => void;
}) {
  const { t } = useTranslation();
  const change = (index: number, row: Partial<DocumentDraft>) =>
    onChange(rows.map((r, i) => (i === index ? { ...r, ...row } : r)));
  return (
    <div className="field" data-testid="profile-documents">
      <span>{t("profile.documents_title")}</span>
      {rows.map((row, index) => (
        <div
          key={index}
          className="row"
          style={{ alignItems: "flex-end", borderTop: "1px solid var(--border)", paddingTop: 8 }}
          data-testid="profile-document"
        >
          <Field label={t("profile.document_type")}>
            <select value={row.type} onChange={(e) => change(index, { type: e.target.value })}>
              <option value="">{t("clients.contact_choose")}</option>
              {PROFILE_DOCUMENT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {t(`profile.document_${type}`)}
                </option>
              ))}
            </select>
          </Field>
          {row.type === "other" ? (
            <Field label={t("cards.document_title")}>
              <input value={row.title} onChange={(e) => change(index, { title: e.target.value })} />
            </Field>
          ) : null}
          <Field label={t("profile.document_number")}>
            <input value={row.number} onChange={(e) => change(index, { number: e.target.value })} />
          </Field>
          <Field label={t("profile.document_issued_on")}>
            <input type="date" value={row.issued_on} onChange={(e) => change(index, { issued_on: e.target.value })} />
          </Field>
          <Field label={t("profile.document_expires_on")}>
            <input type="date" value={row.expires_on} onChange={(e) => change(index, { expires_on: e.target.value })} />
          </Field>
          <Field label={t("cards.issued_by")}>
            <input value={row.issued_by} onChange={(e) => change(index, { issued_by: e.target.value })} />
          </Field>
          {row.type === "driver_license" ? (
            <Field label={t("profile.document_categories")} hint={t("profile.document_categories_hint")}>
              <input value={row.categories} onChange={(e) => change(index, { categories: e.target.value })} />
            </Field>
          ) : null}
          <span style={{ flex: "0 0 auto", paddingBottom: 6 }}>
            <IconButton
              icon="trash"
              label={t("profile.document_remove")}
              onClick={() => onChange(rows.filter((_, i) => i !== index))}
            />
          </span>
        </div>
      ))}
      <div>
        <button type="button" className="secondary small" onClick={() => onChange([...rows, EMPTY])}>
          + {t("cards.add_document")}
        </button>
      </div>
    </div>
  );
}
