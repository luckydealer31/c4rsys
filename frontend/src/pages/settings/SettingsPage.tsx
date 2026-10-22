import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";

/** Settings: company, locations, tariffs, employees (STAGE1_SCREENS, main menu). */
export function SettingsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const links = [
    ["setup", "/setup", can("company.settings")],
    ["company", "/settings/company", true],
    ["locations", "/settings/locations", true],
    ["tariffs", "/settings/tariffs", can("tariff.view")],
    [
      "employees",
      "/settings/employees",
      can("employee.invite") || can("employee.rights.manage") || can("employee.access.block"),
    ],
  ] as const;
  return (
    <div className="page">
      <h1>{t("nav.settings")}</h1>
      {links
        .filter(([, , visible]) => visible)
        .map(([key, path]) => (
          <Link key={key} className="list-link" to={companyPath(company, path)}>
            <strong>{t(`settings.${key}`)}</strong>
            <div className="muted small">{t(`settings.${key}_hint`)}</div>
          </Link>
        ))}
    </div>
  );
}
