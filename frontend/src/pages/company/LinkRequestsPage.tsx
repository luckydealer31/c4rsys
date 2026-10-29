import { useTranslation } from "react-i18next";

import { useCompany } from "../../layout/CompanyShell";
import { LinkRequests } from "../account/LinkRequests";

/** Requests of other companies to link their card with this company's profile (§ 6.3.8): seen and
 * answered by the top-authority director and the employees with the right. */
export function LinkRequestsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  return (
    <div className="page">
      <LinkRequests base={`/c/${company.public_id}/incoming-links`} title={t("links.incoming_title")} />
    </div>
  );
}
