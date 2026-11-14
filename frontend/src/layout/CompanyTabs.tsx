/** The directories of the company stand inside «Company» (customer's decision of 14.11.2026):
 * its card with the locations, the garage and the contacts are tabs of one section, not items
 * of the side menu. The addresses of the garage and of the contacts stay as they were. */
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { contextAvailable } from "../contexts";
import { companyPath, useCan, useCompany, useMayDashboards, useMayTasks } from "./CompanyShell";

function Strip({ label, tabs, current }: { label: string; tabs: [key: string, text: string, path: string][]; current: string }) {
  const company = useCompany();
  const navigate = useNavigate();
  if (tabs.length < 2) return null;
  return (
    <div className="tabs left section-tabs" role="tablist" aria-label={label}>
      {tabs.map(([key, text, path]) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={current === key}
          className={current === key ? "active" : ""}
          onClick={() => navigate(companyPath(company, path))}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

export type CompanyTab = "about" | "garage" | "contacts" | "documents";

export function CompanyTabs({ current }: { current: CompanyTab }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  // The garage exists with a rental or a service context (decision of 24.10.2026).
  const garage = (["rental", "service"] as const).some(
    (type) => contextAvailable(type) && company.context_types.includes(type),
  );
  // Decision Р-6: the registry of documents for whoever may see or add them.
  const documents = can("documents.view") || can("documents.upload");
  const tabs: [string, string, string][] = [
    ["about", t("company.tab_about"), "/company"],
    ...(garage ? [["garage", t("company.tab_garage"), "/vehicles"] as [string, string, string]] : []),
    ["contacts", t("company.tab_contacts"), "/clients"],
    ...(documents ? [["documents", t("nav.documents"), "/documents"] as [string, string, string]] : []),
  ];
  return <Strip label={t("nav.company")} tabs={tabs} current={current} />;
}

/** «Home» (decision of 14.11.2026): what is going on and what is to be done — the dashboards
 * and the tasks in one section. Tasks are shown with a right of § 23.9 (decision З-3). */
export function HomeTabs({ current }: { current: "dashboards" | "tasks" }) {
  const { t } = useTranslation();
  const tasks = useMayTasks();
  const dashboards = useMayDashboards();
  const tabs: [string, string, string][] = [
    ...(dashboards || !tasks ? [["dashboards", t("nav.dashboards"), "/dashboards"] as [string, string, string]] : []),
    ...(tasks ? [["tasks", t("nav.tasks"), "/tasks"] as [string, string, string]] : []),
  ];
  return <Strip label={t("nav.home")} tabs={tabs} current={current} />;
}

export type FinanceTab = "money" | "orders" | "settlements" | "accounts" | "articles";
export const FINANCE_TABS: FinanceTab[] = ["money", "orders", "settlements", "accounts", "articles"];

/** The registry of orders is a tab of «Finance» (decision of 14.11.2026); it keeps its address. */
export function FinanceTabs({ current }: { current: FinanceTab }) {
  const { t } = useTranslation();
  const tabs = FINANCE_TABS.map(
    (key) =>
      [
        key,
        key === "orders" ? t("nav.orders") : t(`money.tab_${key}`),
        key === "orders" ? "/orders" : key === "money" ? "/finance" : `/finance?tab=${key}`,
      ] as [string, string, string],
  );
  return <Strip label={t("nav.finance")} tabs={tabs} current={current} />;
}

/** Where the schedule and the trips live: in «Rental», or in «Service» for a company without
 * rental (decision of 14.11.2026). */
export function workHome(company: { context_types: string[] }): string {
  return company.context_types.includes("rental") || !company.context_types.includes("service") ? "/rentals" : "/service";
}
