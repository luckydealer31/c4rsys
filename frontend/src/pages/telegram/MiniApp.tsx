import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate } from "react-router";

import { api } from "../../api/client";
import { useApi } from "../../api/queries";
import type { CompanySummary, Inspection } from "../../api/types";
import { Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { InspectionList } from "../inspections/InspectionsPage";

/** The first page of the window of the Telegram bot (customer's decision of 23.11.2026): with one
 * company — its menu at once, with several — the choice of the company. */
export function AppHome() {
  const { t } = useTranslation();
  const companies = useQuery({
    queryKey: ["companies"],
    queryFn: async () => (await api<{ companies: CompanySummary[] }>("/companies")).companies,
  });
  if (!companies.data) {
    return (
      <div className="narrow">
        <Loading />
      </div>
    );
  }
  const [only] = companies.data;
  if (companies.data.length === 1 && only) return <Navigate to={`/c/${only.public_id}/app`} replace />;
  return (
    <div className="narrow">
      <h1>{t("miniapp.choose_company")}</h1>
      {companies.data.length === 0 ? <p className="muted">{t("miniapp.no_company")}</p> : null}
      <div className="tiles">
        {companies.data.map((c) => (
          <Link key={c.public_id} className="tile" to={`/c/${c.public_id}/app`}>
            {c.display_name}
          </Link>
        ))}
      </div>
    </div>
  );
}

/** The menu of the window of the bot: what is done from a phone on the go — a vehicle, a delivery
 * note and a receipt by a photo, and the inspections. Each leads to the page of the site that
 * does it. */
export function MiniMenu() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const items: [boolean, string, string][] = [
    [can("vehicle.create"), "/vehicles/new", t("miniapp.add_vehicle")],
    [can("warehouse.receive"), "/warehouse/receipts/new", t("miniapp.add_invoice")],
    [true, "/finance?tab=drafts", t("miniapp.add_receipt")],
    [true, "/app/inspections", t("miniapp.inspections")],
  ];
  return (
    <div className="page">
      <h1>{company.display_name}</h1>
      <div className="tiles">
        {items
          .filter(([may]) => may)
          .map(([, path, label]) => (
            <Link key={path} className="tile" to={companyPath(company, path)}>
              {label}
            </Link>
          ))}
      </div>
      <p>
        <Link to="/app">{t("miniapp.other_company")}</Link>
      </p>
    </div>
  );
}

const WAITING = new Set(["draft", "planned", "in_progress"]);

/** All the inspections of the company the employee may see, in two tabs (customer's decision of
 * 23.11.2026): the ones to be made — opened and conducted from here — and the ones made. */
export function MiniInspections() {
  const { t } = useTranslation();
  const company = useCompany();
  const [tab, setTab] = useState<"waiting" | "done">("waiting");
  const list = useApi<{ inspections: Inspection[] }>(
    ["inspections-all", company.public_id],
    `/c/${company.public_id}/inspections`,
  );
  const items = (list.data?.inspections ?? []).filter((i) => WAITING.has(i.state) === (tab === "waiting"));
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/app")}>← {company.display_name}</Link>
      </p>
      <h1>{t("miniapp.inspections")}</h1>
      <div className="tabs" role="tablist">
        {(["waiting", "done"] as const).map((key) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
            {t(`miniapp.tab_${key}`)}
          </button>
        ))}
      </div>
      {!list.data ? <Loading /> : null}
      {list.data && items.length === 0 ? <p className="muted">{t(`miniapp.none_${tab}`)}</p> : null}
      <InspectionList items={items} showVehicle />
    </div>
  );
}
