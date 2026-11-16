import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { Company, ContextType } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Icon, type IconName } from "../../components/icons";
import { ErrorMessage, Loading, Modal } from "../../components/ui";
import { AVAILABLE_CONTEXTS } from "../../contexts";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { CompanyTabs } from "../../layout/CompanyTabs";
import { useLocations } from "../settings/LocationsPage";

const ICONS: Record<ContextType, IconName> = {
  rental: "rentals",
  service: "service",
  warehouse: "warehouse",
};

/** Future applications named by the customer (decision of 16.11.2026): shown in the tab with the
 * mark «Soon» and not switched on. A list of the interface only — a type enters the system
 * catalog of contexts with its module (§ 4.1.9); the clock stands for their icons until the
 * customer chooses them. */
const UPCOMING = ["tyre_fitting", "car_wash", "parking", "shop"] as const;

/** Tab «Applications» of the company (customer's decisions of 16.11.2026): the directions of
 * work — rental, service, warehouse — are applications the company switches on itself. */
export function AppsPage() {
  const { t } = useTranslation();
  return (
    <div className="page full">
      <CompanyTabs current="apps" />
      <p className="muted">{t("apps.intro")}</p>
      <AppsEditor upcoming />
    </div>
  );
}

/** The applications with their marks; also a step of the setup wizard (§ 22.1.3). A mark asks
 * in which locations the application works; with one location it is switched on there at once,
 * without locations it is only connected. Whoever may change the settings of the company marks;
 * the others see the list. `upcoming`: the future applications follow below — in the tab only,
 * the wizard offers what can be switched on. */
export function AppsEditor({ upcoming = false }: { upcoming?: boolean }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const locations = useLocations();
  const may = can("company.settings");
  const modules = company.modules ?? [];
  const connect = useCommand<{ module: ContextType; locations: string[] }, { company: Company }>(
    (body) => `/c/${company.public_id}/modules/${body.module}/connect`,
  );
  const disconnect = useCommand<{ module: ContextType }, { company: Company }>(
    (body) => `/c/${company.public_id}/modules/${body.module}/disconnect`,
  );
  // The application whose mark was touched last: its refusal is shown in its own card.
  const [touched, setTouched] = useState<ContextType | null>(null);
  // The application waiting for the choice of locations, and the locations marked so far.
  const [asking, setAsking] = useState<ContextType | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);

  if (!locations.data) return <Loading />;
  const all = locations.data.locations;
  const open = all.filter((location) => location.state !== "deactivated");

  async function refresh() {
    await client.invalidateQueries({ queryKey: ["company", company.public_id] });
    await client.invalidateQueries({ queryKey: ["locations", company.public_id] });
    await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
  }

  async function switchOn(module: ContextType, where: string[]) {
    const result = await connect.run({ module, locations: where });
    if (result) {
      setAsking(null);
      await refresh();
    }
  }

  async function toggle(module: ContextType) {
    setTouched(module);
    connect.clearError();
    disconnect.clearError();
    if (modules.includes(module)) {
      if (await disconnect.run({ module })) await refresh();
      return;
    }
    if (open.length > 1) {
      // Nothing is marked in advance: the company names the locations itself.
      setChosen([]);
      setAsking(module);
      return;
    }
    await switchOn(
      module,
      open.map((location) => location.id),
    );
  }

  const pending = connect.pending || disconnect.pending;
  return (
    <>
      <div className="tiles">
        {AVAILABLE_CONTEXTS.map((type) => {
          const on = modules.includes(type);
          const working = all.filter((location) =>
            location.contexts.some((c) => c.type === type && c.state === "enabled"),
          );
          return (
            <section key={type} className="card tile">
              <div className="tile-head">
                <h3 style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <Icon name={ICONS[type]} size={22} /> {t(`contexts.${type}`)}
                </h3>
              </div>
              <p>{t(`apps.about_${type}`)}</p>
              {on ? (
                <p className="muted small">
                  {working.length
                    ? t("apps.works_in", { locations: working.map((location) => location.name).join(", ") })
                    : t("apps.no_locations")}
                </p>
              ) : null}
              {touched === type && !asking ? <ErrorMessage error={connect.error ?? disconnect.error} /> : null}
              <div className="actions">
                <label>
                  <input
                    type="checkbox"
                    // Three marks with the same caption: each is named with its application.
                    aria-label={`${t(`contexts.${type}`)} — ${t("apps.enabled")}`}
                    checked={on}
                    disabled={!may || pending}
                    onChange={() => void toggle(type)}
                  />{" "}
                  {t("apps.enabled")}
                </label>
              </div>
            </section>
          );
        })}
      </div>
      {upcoming ? (
        // A row of their own: the cards without a mark are lower than the working ones.
        <div className="tiles" style={{ marginTop: "var(--gap)" }}>
          {UPCOMING.map((key) => (
            <section key={key} className="card tile">
              <div className="tile-head">
                <h3 style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <Icon name="clock" size={22} /> {t(`apps.upcoming_${key}`)}
                </h3>
                <span className="badge">{t("apps.soon")}</span>
              </div>
            </section>
          ))}
        </div>
      ) : null}
      {asking ? (
        <Modal
          title={t("apps.where_title", { app: t(`contexts.${asking}`) })}
          onClose={() => setAsking(null)}
        >
          <p className="muted">{t("apps.where_text")}</p>
          <div style={{ display: "grid", gap: 8, margin: "12px 0" }}>
            {open.map((location) => (
              <label key={location.id}>
                <input
                  type="checkbox"
                  checked={chosen.includes(location.id)}
                  onChange={() =>
                    setChosen(
                      chosen.includes(location.id)
                        ? chosen.filter((id) => id !== location.id)
                        : [...chosen, location.id],
                    )
                  }
                />{" "}
                {location.name}
              </label>
            ))}
          </div>
          <ErrorMessage error={connect.error} />
          <div className="actions">
            <button type="button" disabled={connect.pending} onClick={() => void switchOn(asking, chosen)}>
              {t("apps.connect")}
            </button>
            <button type="button" className="secondary" onClick={() => setAsking(null)}>
              {t("common.cancel")}
            </button>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
