import { useTranslation } from "react-i18next";

import { Card } from "../../components/ui";
import { useCan } from "../../layout/CompanyShell";
import { WarehouseDashboard } from "../warehouse/Dashboard";

/** Dashboards (decision of 24.10.2026): the starter dashboard «Warehouse» (§ 21.6.1); the
 * others come with their modules. */
export function DashboardsPage() {
  const { t } = useTranslation();
  const can = useCan();
  return (
    <div className="page">
      <h1>{t("nav.dashboards")}</h1>
      {can("warehouse.view") ? <WarehouseDashboard /> : null}
      <Card>
        <p className="muted">{t("dashboards.later")}</p>
      </Card>
    </div>
  );
}
