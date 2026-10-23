import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { CompanyProfileForm } from "../settings/CompanyProfilePage";
import { LocationsEditor } from "../settings/LocationsPage";

/** Section «Company» (decision of 24.10.2026): its profile and its locations; contexts of the
 * locations decide which sections appear in the menu. */
export function CompanyPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  return (
    <div className="page">
      <h1>{company.display_name}</h1>
      {company.setup_completed_at === null && can("company.settings") ? (
        <div className="alert warn">
          {t("company.setup_not_completed")}{" "}
          <Link to={companyPath(company, "/setup")}>{t("nav.setup")}</Link>
        </div>
      ) : null}
      <h2>{t("company.profile")}</h2>
      <CompanyProfileForm />
      <h2>{t("settings.locations")}</h2>
      <p className="muted">{t("company.locations_hint")}</p>
      <LocationsEditor />
    </div>
  );
}
