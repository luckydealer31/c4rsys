/** The directories of the company stand inside «Company» (customer's decision of 14.11.2026):
 * its card with the locations, the garage and the contacts are tabs of one section, not items
 * of the side menu. The addresses of the garage and of the contacts stay as they were. */
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { contextAvailable } from "../contexts";
import { companyPath, useCan, useCompany, useMayDashboards, useMayTasks } from "./CompanyShell";
import { SectionTabs } from "./SectionTabs";
import { useFirstTab } from "./tabOrder";

type StripTab = [key: string, text: string, path: string];

/** The address a section of the menu opens at: its first tab in the user's order (customer's
 * decision of 17.11.2026). These sections have a page of its own for every tab. */
export function useSectionHome(section: string, tabs: StripTab[], usual: string): string {
  const first = useFirstTab(section)(tabs.map(([key]) => key));
  return tabs.find(([key]) => key === first)?.[2] ?? usual;
}

function Strip({
  section,
  label,
  tabs,
  current,
}: {
  section: string;
  label: string;
  tabs: [key: string, text: string, path: string][];
  current: string;
}) {
  const company = useCompany();
  const navigate = useNavigate();
  return (
    <SectionTabs
      section={section}
      label={label}
      current={current}
      tabs={tabs.map(([key, text]) => ({ key, text }))}
      onSelect={(key) => navigate(companyPath(company, tabs.find(([other]) => other === key)?.[2] ?? ""))}
    />
  );
}

export type CompanyTab = "about" | "garage" | "contacts" | "documents" | "tags" | "apps" | "settings";

export function useCompanyTabs(): StripTab[] {
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
    // The tags of the company and the user's own (decision of 17.11.2026): seen by everybody.
    ["tags", t("company.tab_tags"), "/company/tags"],
    // The applications of the company (decision of 16.11.2026) are switched on by whoever may
    // change its settings; the tab is shown to them (PROPOSAL) and stands before «Settings»
    // (PROPOSAL).
    ...(can("company.settings")
      ? [["apps", t("company.tab_apps"), "/company/apps"] as [string, string, string]]
      : []),
    // The profile of the company, its public fields and their history: for whoever may change
    // the settings of the company (decision of 14.11.2026).
    ...(can("company.settings")
      ? [["settings", t("company.tab_settings"), "/company/settings"] as [string, string, string]]
      : []),
  ];
  return tabs;
}

export function CompanyTabs({ current }: { current: CompanyTab }) {
  const { t } = useTranslation();
  return <Strip section="company" label={t("nav.company")} tabs={useCompanyTabs()} current={current} />;
}

/** «Productivity» (decisions of 14.11.2026 and 16.11.2026): what is going on and what is to be done — the dashboards
 * and the tasks in one section. Tasks are shown with a right of § 23.9 (decision З-3). */
export function useProductivityTabs(): StripTab[] {
  const { t } = useTranslation();
  const tasks = useMayTasks();
  const dashboards = useMayDashboards();
  const tabs: [string, string, string][] = [
    ...(dashboards || !tasks ? [["dashboards", t("nav.dashboards"), "/dashboards"] as [string, string, string]] : []),
    ...(tasks ? [["tasks", t("nav.tasks"), "/tasks"] as [string, string, string]] : []),
  ];
  return tabs;
}

export function ProductivityTabs({ current }: { current: "dashboards" | "tasks" }) {
  const { t } = useTranslation();
  return <Strip section="productivity" label={t("nav.productivity")} tabs={useProductivityTabs()} current={current} />;
}

export type FinanceTab = "money" | "orders" | "settlements" | "accounts" | "articles";
export const FINANCE_TABS: FinanceTab[] = ["money", "orders", "settlements", "accounts", "articles"];

/** The registry of orders is a tab of «Finance» (decision of 14.11.2026); it keeps its address. */
export function useFinanceTabs(): StripTab[] {
  const { t } = useTranslation();
  const tabs = FINANCE_TABS.map(
    (key) =>
      [
        key,
        key === "orders" ? t("nav.orders") : t(`money.tab_${key}`),
        key === "orders" ? "/orders" : key === "money" ? "/finance" : `/finance?tab=${key}`,
      ] as [string, string, string],
  );
  return tabs;
}

export function FinanceTabs({ current }: { current: FinanceTab }) {
  const { t } = useTranslation();
  return <Strip section="finance" label={t("nav.finance")} tabs={useFinanceTabs()} current={current} />;
}

/** Where the schedule and the trips live: in «Rental», or in «Service» for a company without
 * rental (decision of 14.11.2026). */
export function workHome(company: { context_types: string[] }): string {
  return company.context_types.includes("rental") || !company.context_types.includes("service") ? "/rentals" : "/service";
}
