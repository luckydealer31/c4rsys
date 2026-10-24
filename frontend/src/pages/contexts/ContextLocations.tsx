import { useTranslation } from "react-i18next";

import type { ContextType, Location } from "../../api/types";
import { Card, Loading } from "../../components/ui";
import { moduleWorks } from "../../contexts";
import { useLocations } from "../settings/LocationsPage";

function contextState(location: Location, type: ContextType): string {
  const context = location.contexts.find((c) => c.type === type);
  if (!moduleWorks(type)) return "module_later";
  if (location.state === "deactivated") return "deactivated";
  if (context?.state === "disabled") return "disabled";
  return context?.ready ? "ready" : "not_ready";
}

/** Service and warehouse are divided by location (decision of 24.10.2026): each location with
 * such a context is a section of its own. The modules come with later stages; until then a
 * section shows only its location and state. A separate storage of a service context (§ 11.1.5)
 * appears with the service module. */
export function ContextSectionPage({ type }: { type: Exclude<ContextType, "rental"> }) {
  const { t } = useTranslation();
  const locations = useLocations();
  const items = (locations.data?.locations ?? []).filter((l) =>
    l.contexts.some((c) => c.type === type),
  );
  return (
    <div className="page">
      <h1>{t(`nav.${type}`)}</h1>
      {locations.isLoading ? <Loading /> : null}
      {items.map((location) => {
        const state = contextState(location, type);
        return (
          <Card key={location.id} title={`${t(`contexts.${type}`)} — ${location.name}`}>
            <span className={`badge ${state === "ready" ? "ok" : "warn"}`}>
              {t(`contexts.state_${state}`)}
            </span>
          </Card>
        );
      })}
      <p className="muted">{t("contexts.module_later")}</p>
    </div>
  );
}
