import { useTranslation } from "react-i18next";

import { Card } from "../../components/ui";

/** Dashboards: the item exists in the menu (decision of 24.10.2026); their content is not
 * decided yet (OPEN). */
export function DashboardsPage() {
  const { t } = useTranslation();
  return (
    <div className="page">
      <h1>{t("nav.dashboards")}</h1>
      <Card>
        <p className="muted">{t("dashboards.later")}</p>
      </Card>
    </div>
  );
}
