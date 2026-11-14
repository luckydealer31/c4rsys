/** The directories of the company stand inside «Company» (customer's decision of 14.11.2026):
 * its card with the locations, the garage and the contacts are tabs of one section, not items
 * of the side menu. The addresses of the garage and of the contacts stay as they were. */
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { contextAvailable } from "../contexts";
import { companyPath, useCompany } from "./CompanyShell";

export type CompanyTab = "about" | "garage" | "contacts";

const PATHS: Record<CompanyTab, string> = { about: "/company", garage: "/vehicles", contacts: "/clients" };

export function CompanyTabs({ current }: { current: CompanyTab }) {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  // The garage exists with a rental or a service context (decision of 24.10.2026).
  const garage = (["rental", "service"] as const).some(
    (type) => contextAvailable(type) && company.context_types.includes(type),
  );
  const tabs: CompanyTab[] = ["about", ...(garage ? (["garage"] as CompanyTab[]) : []), "contacts"];
  return (
    <div className="tabs left section-tabs" role="tablist" aria-label={t("nav.company")}>
      {tabs.map((key) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={current === key}
          className={current === key ? "active" : ""}
          onClick={() => navigate(companyPath(company, PATHS[key]))}
        >
          {t(`company.tab_${key}`)}
        </button>
      ))}
    </div>
  );
}

/** Where the schedule and the trips live: in «Rental», or in «Service» for a company without
 * rental (decision of 14.11.2026). */
export function workHome(company: { context_types: string[] }): string {
  return company.context_types.includes("rental") || !company.context_types.includes("service") ? "/rentals" : "/service";
}
