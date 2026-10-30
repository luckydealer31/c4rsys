import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";

type DataExport = {
  id: string;
  created_at: string;
  expires_at: string;
  available: boolean;
  size: number;
  tables: number;
  rows: number;
  documents: number;
};

/** The full export of the company's data (§ 22.6.1; decision Б-6): a ZIP with a CSV per table
 * and the files of the documents; the link works 24 hours and checks the right when opened. */
export function ExportPage() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const base = `/c/${company.public_id}/exports`;
  const exports = useApi<{ exports: DataExport[] }>(["exports", company.public_id], base);
  const create = useCommand<Record<string, never>, unknown>(base);
  const when = (value: string) => formatMoment(value, company.timezone || deviceTimeZone(), i18n.language);

  return (
    <div className="page">
      <h1>{t("exports.title")}</h1>
      <Card>
        <p>{t("exports.hint")}</p>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button
            type="button"
            disabled={create.pending}
            onClick={async () => {
              if ((await create.run({})) !== null) {
                await client.invalidateQueries({ queryKey: ["exports", company.public_id] });
              }
            }}
          >
            {create.pending ? t("exports.preparing") : t("exports.create")}
          </button>
        </div>
      </Card>
      <Card title={t("exports.list")}>
        {exports.isLoading ? <Loading /> : null}
        <ErrorMessage error={exports.error as never} />
        {exports.data?.exports.length === 0 ? <p className="muted">{t("exports.none")}</p> : null}
        {(exports.data?.exports ?? []).map((e) => (
          <div key={e.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--border, #ddd)" }}>
            <div>
              {when(e.created_at)} ·{" "}
              {t("exports.summary", {
                tables: e.tables,
                rows: e.rows,
                documents: e.documents,
                size: e.size < 1024 * 1024 ? `${Math.max(1, Math.round(e.size / 1024))} KB` : `${(e.size / 1024 / 1024).toFixed(1)} MB`,
              })}
            </div>
            {e.available ? (
              <div className="small">
                <a href={`/api${base}/${e.id}/download`}>{t("exports.download")}</a>{" "}
                <span className="muted">{t("exports.until", { at: when(e.expires_at) })}</span>
              </div>
            ) : (
              <div className="small muted">{t("exports.expired")}</div>
            )}
          </div>
        ))}
      </Card>
    </div>
  );
}
