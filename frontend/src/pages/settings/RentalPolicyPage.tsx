import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { useLocations } from "./LocationsPage";

type Policy = {
  location_id: string | null;
  own: boolean;
  enabled: boolean;
  after_minutes: number | null;
  effective: { enabled: boolean; after_minutes?: number | null; source: string };
};

/** Auto-cancel of a booking at no-show (§ 14.8.1): off by default; set for the company, and a
 * rental location may have its own rule. A booking keeps the rule it got when created. */
export function RentalPolicyPage() {
  const { t } = useTranslation();
  const locations = useLocations();
  return (
    <div className="page">
      <h1>{t("bookings.policy_title")}</h1>
      <p className="muted">{t("bookings.policy_hint")}</p>
      <Card title={t("bookings.policy_company")}>
        <PolicyForm locationId={null} />
      </Card>
      {(locations.data?.locations ?? []).map((l) => (
        <Card key={l.id} title={l.name}>
          <PolicyForm locationId={l.id} />
        </Card>
      ))}
    </div>
  );
}

function PolicyForm({ locationId }: { locationId: string | null }) {
  const company = useCompany();
  const query = locationId ? `?location_id=${locationId}` : "";
  const key = ["rental-policy", company.public_id, locationId ?? ""];
  const policy = useApi<{ policy: Policy }>(key, `/c/${company.public_id}/rental-policy${query}`);
  if (!policy.data) return <Loading />;
  return <PolicyFields policy={policy.data.policy} path={`/c/${company.public_id}/rental-policy${query}`} queryKey={key} />;
}

function PolicyFields({ policy, path, queryKey }: { policy: Policy; path: string; queryKey: string[] }) {
  const { t } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const [enabled, setEnabled] = useState(policy.enabled);
  const [after, setAfter] = useState(policy.after_minutes === null ? "" : String(policy.after_minutes));
  const save = useCommand<Record<string, unknown>, { policy: Policy }>(path, "PUT");
  const effective = policy.effective;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await save.run({ enabled, after_minutes: after === "" ? null : Number(after) });
    if (result) {
      queryClient.setQueryData(queryKey, result);
      await queryClient.invalidateQueries({ queryKey: ["rental-policy"] });
    }
  }

  return (
    <form onSubmit={submit}>
      <p className="muted small">
        {effective.enabled
          ? t("bookings.policy_effective_on", {
              minutes: effective.after_minutes,
              source: t(`bookings.policy_source_${effective.source}`),
            })
          : t("bookings.policy_effective_off", { source: t(`bookings.policy_source_${effective.source}`) })}
      </p>
      <label className="small">
        <input type="checkbox" checked={enabled} disabled={!can("company.settings")} onChange={() => setEnabled(!enabled)} />{" "}
        {t("bookings.policy_enabled")}
      </label>
      {enabled ? (
        <div className="row">
          <Field label={t("bookings.policy_after")}>
            <input value={after} inputMode="numeric" onChange={(e) => setAfter(e.target.value.replace(/\D/g, ""))} />
          </Field>
        </div>
      ) : null}
      <ErrorMessage error={save.error} />
      {can("company.settings") ? (
        <div className="actions">
          <button type="submit" disabled={save.pending || (enabled && after === "")}>
            {t("common.save")}
          </button>
        </div>
      ) : null}
    </form>
  );
}
