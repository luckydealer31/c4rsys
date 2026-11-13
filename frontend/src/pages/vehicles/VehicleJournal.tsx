/** Who did what with the card (customer's decision of 13.11.2026): the entries of the journal
 * about the card with the author and the moment, newest first. */
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { Vehicle } from "../../api/types";
import { Card } from "../../components/ui";
import { formatMoment } from "../../time";
import { useCompany } from "../../layout/CompanyShell";

type Entry = { id: string; at: string; action: string; author: string; subject: string };

export function VehicleJournal({ vehicle }: { vehicle: Vehicle }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const journal = useApi<{ items: Entry[] }>(
    ["vehicle", company.public_id, vehicle.id, "journal"],
    `/c/${company.public_id}/vehicles/${vehicle.id}/journal`,
  );
  const items = journal.data?.items ?? [];
  return (
    <Card title={t("vehicles.journal_title")}>
      {journal.isLoading ? <p className="muted">{t("common.loading")}</p> : null}
      {!journal.isLoading && items.length === 0 ? <p className="muted">{t("vehicles.journal_none")}</p> : null}
      {items.length ? (
        <table>
          <thead>
            <tr>
              <th>{t("vehicles.journal_when")}</th>
              <th>{t("vehicles.journal_who")}</th>
              <th>{t("vehicles.journal_what")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.id}>
                <td className="small">{formatMoment(e.at, company.timezone ?? "UTC", i18n.language)}</td>
                <td>{e.author || t("vehicles.journal_system")}</td>
                <td>
                  {t(`vehicles.journal_actions.${e.action.replace(".", "-")}`, { defaultValue: t("vehicles.journal_other") })}
                  {e.subject ? `: ${e.subject}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}
