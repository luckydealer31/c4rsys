import { useTranslation } from "react-i18next";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import { ErrorMessage, Loading } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";

type Entry = {
  id: string;
  occurred_at: string;
  started_at: string;
  ended_at: string | null;
  unit: string;
  roles: string[];
  reason_category: string;
  sections: string[];
  downloads: number;
};

/** § 24.3.2: finished privileged sessions of the c4rsys staff in the company — the moment, the
 * unit, the reason category and the viewed sections, without the person of the staff member. */
export function StaffAccessPage() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const when = (value: string | null) => formatMoment(value, company.timezone ?? deviceTimeZone(), i18n.language);
  const entries = useApi<{ entries: Entry[] }>(
    [company.public_id, "staff-access"],
    `/c/${company.public_id}/staff-access`,
  );
  return (
    <div className="page">
      <h1>{t("settings.staff_access")}</h1>
      <p className="muted">{t("staff_access.hint")}</p>
      {entries.isLoading ? <Loading /> : null}
      <ErrorMessage error={entries.error as ApiError | null} />
      {entries.data && entries.data.entries.length === 0 ? <p className="muted">{t("staff_access.none")}</p> : null}
      {entries.data && entries.data.entries.length > 0 ? (
        <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>{t("staff_access.period")}</th>
              <th>{t("staff_access.unit")}</th>
              <th>{t("staff_access.reason")}</th>
              <th>{t("staff_access.sections")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.data.entries.map((e) => (
              <tr key={e.id}>
                <td>
                  {when(e.started_at)} — {when(e.ended_at)}
                </td>
                <td>{e.unit || e.roles.map((r) => t(`staff.role_${r}`, { defaultValue: r })).join(", ")}</td>
                <td>{t(`staff.reason_${e.reason_category}`, { defaultValue: e.reason_category })}</td>
                <td>
                  {e.sections.map((x) => t(`staff.section_${x}`, { defaultValue: x })).join(", ") || "—"}
                  {e.downloads ? ` · ${t("staff_access.downloads", { count: e.downloads })}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      ) : null}
    </div>
  );
}
