import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { DataRequest, IncomingLink } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";
import { orderedFields, SharedTable, sharedValue } from "./ProfileLinkCard";

/** § 6.4.4: the fresh data the holder of the linked profile gave on his own (customer's decision
 * of 26.11.2026 — the company does not ask for them). Each is a new snapshot whose values are
 * taken one by one (§ 6.4.2). Nothing is shown until something is given. */
export function GivenDataPanel({
  linkId,
  editable,
  applicable,
  version,
  onApplied,
}: {
  linkId: string;
  editable: boolean;
  applicable: string[];
  version: number;
  onApplied: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const base = `/c/${company.public_id}`;
  const key = ["data-requests", company.public_id, linkId];
  const given = useApi<{ requests: DataRequest[] }>(key, `${base}/profile-links/${linkId}/data-requests`);
  const apply = useCommand<{ field: string; expected_version: number; data_request_id: string }, unknown>(
    `${base}/profile-links/${linkId}/apply`,
  );
  const when = (value: string) => formatMoment(value, company.timezone || deviceTimeZone(), i18n.language);
  const items = (given.data?.requests ?? []).filter((r) => Object.keys(r.shared).length);
  if (!items.length) return null;

  return (
    <div className="stack" data-testid="given-data">
      <h3>{t("data_requests.title")}</h3>
      {items.map((r) => (
        <div key={r.id} className="stack">
          <p className="muted small">{t("data_requests.given_at", { at: when(r.shared_at ?? r.created_at) })}</p>
          <SharedTable
            data={r.shared}
            compare={r.compare}
            applicable={editable ? applicable : []}
            pending={apply.pending}
            onApply={async (field) => {
              const body = { field, expected_version: version, data_request_id: r.id };
              if ((await apply.run(body)) !== null) {
                await queryClient.invalidateQueries({ queryKey: key });
                await onApplied();
              }
            }}
          />
        </div>
      ))}
      <ErrorMessage error={apply.error} />
    </div>
  );
}

/** The holder of a profile gives its fresh data to a linked company (§ 6.4.4): a person in the
 * cabinet (`base` is `/profile-links`), a company in its interface (`/c/<CMP>/incoming-links`).
 * A button; after it — the choice of what to give. */
export function GiveFreshData({ base, link }: { base: string; link: IncomingLink }) {
  const { t } = useTranslation();
  const offered = orderedFields(link.available).filter(([, value]) => value !== "" && value !== null);
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(() => new Set());
  const give = useCommand<{ fields: string[] }, unknown>(`${base}/${link.id}/give`);

  if (!open) {
    return (
      <div className="actions" style={{ alignItems: "center" }}>
        <button
          type="button"
          className="secondary small"
          data-testid="give-data"
          onClick={() => {
            setDone(false);
            setOpen(true);
          }}
        >
          {t("data_requests.give_start")}
        </button>
        {done ? <span className="badge ok">{t("data_requests.given")}</span> : null}
      </div>
    );
  }
  return (
    <div className="stack" data-testid="give-data-form">
      <p className="small">{t("data_requests.choose", { company: link.company.display_name })}</p>
      {offered.map(([field, value]) => (
        <div key={field}>
          <label className="small">
            <input
              type="checkbox"
              checked={chosen.has(field)}
              onChange={() => {
                const next = new Set(chosen);
                if (next.has(field)) next.delete(field);
                else next.add(field);
                setChosen(next);
              }}
            />{" "}
            {t(`links.field_${field}`)}: <span className="muted">{sharedValue(value, t)}</span>
          </label>
        </div>
      ))}
      <ErrorMessage error={give.error} />
      <div className="actions">
        <button
          type="button"
          className="small"
          disabled={give.pending || chosen.size === 0}
          onClick={async () => {
            if ((await give.run({ fields: [...chosen] })) !== null) {
              setChosen(new Set());
              setOpen(false);
              setDone(true);
            }
          }}
        >
          {t("data_requests.give")}
        </button>
        <button type="button" className="secondary small" onClick={() => setOpen(false)}>
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}
