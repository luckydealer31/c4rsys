import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useCan } from "../../layout/CompanyShell";
import { ReportView } from "../reports/Report";
import { WarehouseDashboard } from "../warehouse/Dashboard";

/** Starter dashboards (§ 21.2.6): the company overview, the warehouse; the others come with the
 * parts of the reports block. Each shows only the data the employee may see (§ 21.1.1). */
export function DashboardsPage() {
  const { t } = useTranslation();
  const can = useCan();
  const tabs = [
    ["overview", can("reports.view")],
    ["rental", can("reports.view")],
    ["finance", can("reports.view")],
    ["warehouse", can("warehouse.view")],
  ] as const;
  const visible = tabs.filter(([, shown]) => shown).map(([key]) => key);
  const [tab, setTab] = useState<string>(visible[0] ?? "");
  return (
    <div className="page">
      <h1>{t("nav.dashboards")}</h1>
      <div className="actions no-print" style={{ marginTop: 0, marginBottom: 12 }}>
        {visible.map((key) => (
          <button key={key} type="button" className={key === tab ? "" : "secondary"} onClick={() => setTab(key)}>
            {t(`reports.dashboard_${key}`)}
          </button>
        ))}
      </div>
      {!visible.length ? <p className="muted">{t("reports.no_rights")}</p> : null}
      {tab === "overview" ? <ReportView code="overview" title={t("reports.dashboard_overview")} /> : null}
      {tab === "rental" ? <ReportView code="rental" title={t("reports.dashboard_rental")} /> : null}
      {tab === "finance" ? <ReportView code="finance" title={t("reports.dashboard_finance")} /> : null}
      {tab === "warehouse" ? <WarehouseDashboard /> : null}
    </div>
  );
}
