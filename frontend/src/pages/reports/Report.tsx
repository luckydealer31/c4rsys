import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { ApiError, downloadFile } from "../../api/client";
import { useApi } from "../../api/queries";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { useLocations } from "../settings/LocationsPage";

export type ReportBlock = {
  code: string;
  section: string;
  unit: "count" | "money" | "accounts";
  value: number | { amount: string; currency: string; missing: string[] } | null;
  rows: { currency?: string; amount?: string; name?: string; balance?: string }[];
  area: { whole: boolean; locations: string[] } | null;
  accounts: string[] | null;
  link: string | null;
};

type Report = {
  dashboard: string;
  filters: { start: string; end: string; zone: string; location_id: string | null };
  main_currency: string | null;
  blocks: ReportBlock[];
};

export type ReportFilters = { start: string; end: string; tz: string; location: string };

export function metricLabel(t: (key: string) => string, code: string): string {
  return t(`reports.metric_${code.replaceAll(".", "_")}`);
}

/** Period, zone and location of a report (§ 21.1.2, § 21.2.2, § 21.2.7). */
function FilterBar({ value, onChange }: { value: ReportFilters; onChange: (next: ReportFilters) => void }) {
  const { t } = useTranslation();
  const locations = useLocations();
  return (
    <div className="row no-print">
      <Field label={t("reports.start")}>
        <input type="date" value={value.start} onChange={(e) => onChange({ ...value, start: e.target.value })} />
      </Field>
      <Field label={t("reports.end")}>
        <input type="date" value={value.end} onChange={(e) => onChange({ ...value, end: e.target.value })} />
      </Field>
      <Field label={t("reports.location")}>
        <select value={value.location} onChange={(e) => onChange({ ...value, location: e.target.value })}>
          <option value="">{t("reports.all_locations")}</option>
          {(locations.data?.locations ?? []).map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t("reports.zone")} hint={t("reports.zone_hint")}>
        <input value={value.tz} placeholder="Asia/Bangkok" onChange={(e) => onChange({ ...value, tz: e.target.value.trim() })} />
      </Field>
    </div>
  );
}

function BlockValue({ block }: { block: ReportBlock }) {
  const { t } = useTranslation();
  if (block.unit === "count") return <div className="metric">{block.value === null ? t("reports.no_data") : String(block.value)}</div>;
  if (block.unit === "money") {
    if (!block.rows.length) return <div className="metric">{block.value === 0 ? "0" : t("reports.no_data")}</div>;
    return (
      <div>
        {block.rows.map((r) => (
          <div key={r.currency} className="metric">
            {r.amount} {r.currency}
          </div>
        ))}
      </div>
    );
  }
  const total = block.value && typeof block.value === "object" ? block.value : null;
  return (
    <div>
      {total ? (
        <div className="metric">
          {total.amount} {total.currency}
          {total.missing.length ? <span className="muted small"> · {t("reports.without_rate", { currencies: total.missing.join(", ") })}</span> : null}
        </div>
      ) : null}
      {block.rows.map((r) => (
        <div key={r.name} className="small">
          {r.name}: {r.balance} {r.currency}
        </div>
      ))}
    </div>
  );
}

function areaText(t: (key: string, o?: Record<string, unknown>) => string, block: ReportBlock): string | null {
  if (block.accounts) return t("reports.area_accounts", { names: block.accounts.join(", ") });
  if (block.area && !block.area.whole) return t("reports.area_locations", { names: block.area.locations.join(", ") });
  return null;
}

/** A starter dashboard: blocks the employee may see, export to XLSX and print to PDF (§ 21.2.9,
 * § 21.2.11; decision О-2). */
export function ReportView({ code, title }: { code: string; title: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [filters, setFilters] = useState<ReportFilters>({ start: "", end: "", tz: "", location: "" });
  const [exportError, setExportError] = useState<ApiError | null>(null);
  const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v) as [string, string][]).toString();
  const report = useApi<Report>(["report", company.public_id, code, query], `/c/${company.public_id}/reports/${code}?${query}`);
  const data = report.data;

  async function exportXlsx() {
    if (!data) return;
    setExportError(null);
    const labels: Record<string, string> = { [code]: title };
    for (const b of data.blocks) labels[b.code] = metricLabel(t, b.code);
    const headers = {
      period: t("reports.period"),
      zone: t("reports.zone"),
      location: t("reports.location"),
      whole_company: t("reports.whole_company"),
      no_data: t("reports.no_data"),
    };
    try {
      await downloadFile(`/c/${company.public_id}/reports/${code}/xlsx?${query}`, { labels, headers }, `${code}-${data.filters.start}-${data.filters.end}.xlsx`);
    } catch (caught) {
      setExportError(caught instanceof ApiError ? caught : new ApiError(0, null));
    }
  }

  return (
    <div>
      <FilterBar value={filters} onChange={setFilters} />
      {data ? (
        <p className="small muted">
          {t("reports.applied", { start: data.filters.start, end: data.filters.end, zone: data.filters.zone })}
        </p>
      ) : null}
      <div className="actions no-print">
        <button type="button" className="secondary" onClick={() => window.print()}>
          {t("reports.print")}
        </button>
        {can("reports.export") ? (
          <button type="button" className="secondary" disabled={!data} onClick={() => void exportXlsx()}>
            {t("reports.xlsx")}
          </button>
        ) : null}
      </div>
      <ErrorMessage error={exportError ?? (report.error instanceof ApiError ? report.error : null)} />
      {report.isLoading ? <Loading /> : null}
      {data && !data.blocks.length ? <p className="muted">{t("reports.nothing_available")}</p> : null}
      <div className="metric-grid">
        {(data?.blocks ?? []).map((b) => {
          const where = areaText(t, b);
          return (
            <Card key={b.code} title={metricLabel(t, b.code)}>
              <BlockValue block={b} />
              {where ? <p className="muted small">{where}</p> : null}
              {b.link ? (
                <Link className="small no-print" to={companyPath(company, b.link)}>
                  {t("reports.open_list")}
                </Link>
              ) : null}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
