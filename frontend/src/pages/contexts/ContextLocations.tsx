import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { ContextType } from "../../api/types";
import { Card, Loading } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { moduleWorks } from "../../contexts";
import { useLocations } from "../settings/LocationsPage";

/** Locations that have a context of this type (decision of 24.10.2026): each direction shows
 * its own locations. */
export function ContextLocations({ type }: { type: ContextType }) {
  const { t } = useTranslation();
  const company = useCompany();
  const locations = useLocations();
  const items = (locations.data?.locations ?? []).filter((l) =>
    l.contexts.some((c) => c.type === type),
  );
  return (
    <Card title={t("contexts.locations_title")}>
      {locations.isLoading ? <Loading /> : null}
      <div className="chips">
        {items.map((location) => {
          const context = location.contexts.find((c) => c.type === type);
          const state = !moduleWorks(type)
            ? "module_later"
            : location.state === "deactivated"
              ? "deactivated"
              : context?.state === "disabled"
                ? "disabled"
                : context?.ready
                  ? "ready"
                  : "not_ready";
          return (
            <Link key={location.id} className="chip" to={companyPath(company, "/company")}>
              <strong>{location.name}</strong>{" "}
              <span className={`badge ${state === "ready" ? "ok" : "warn"}`}>
                {t(`contexts.state_${state}`)}
              </span>
            </Link>
          );
        })}
      </div>
    </Card>
  );
}
/** Service and warehouse: the modules come with later stages (STAGE1 README); until then the
 * section shows its locations. */
export function ContextSectionPage({ type }: { type: Exclude<ContextType, "rental"> }) {
  const { t } = useTranslation();
  return (
    <div className="page">
      <h1>{t(`nav.${type}`)}</h1>
      <ContextLocations type={type} />
      <p className="muted">{t("contexts.module_later")}</p>
    </div>
  );
}
