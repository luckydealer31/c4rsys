import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import type { Company } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { currencies, deviceTimeZone, timeZones } from "../../time";

/** Company profile: display name, time zone (§ 3.1.6, § 22.1.4), main currency (§ 22.1.7). */
export function CompanyProfilePage() {
  const { t } = useTranslation();
  return (
    <div className="page">
      <h1>{t("settings.company")}</h1>
      <CompanyProfileForm />
    </div>
  );
}

export function CompanyProfileForm({ onSaved }: { onSaved?: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const [form, setForm] = useState({
    display_name: company.display_name,
    timezone: company.timezone ?? deviceTimeZone(),
    main_currency: company.main_currency ?? "",
  });
  const [saved, setSaved] = useState(false);
  const save = useCommand<Record<string, unknown>, { company: Company }>(
    `/c/${company.public_id}`,
    "PATCH",
  );
  const editable = can("company.settings");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    const changes: Record<string, unknown> = { expected_version: company.version };
    if (form.display_name !== company.display_name) changes.display_name = form.display_name;
    if (form.timezone !== company.timezone) changes.timezone = form.timezone;
    if (form.main_currency && form.main_currency !== company.main_currency)
      changes.main_currency = form.main_currency;
    if ((await save.run(changes)) !== null) {
      await client.invalidateQueries({ queryKey: ["company", company.public_id] });
      setSaved(true);
      onSaved?.();
    }
  }

  return (
    <Card>
      <form className="stack" onSubmit={submit}>
        <p className="muted small">
          {t("companies.company_id")}: {company.public_id}
        </p>
        <div className="row">
          <Field label={t("companies.display_name")}>
            <input
              value={form.display_name}
              disabled={!editable}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            />
          </Field>
          <Field
            label={t("companies.timezone")}
            hint={company.timezone ? undefined : t("companies.timezone_confirm")}
          >
            <select
              value={form.timezone}
              disabled={!editable}
              onChange={(e) => setForm({ ...form, timezone: e.target.value })}
            >
              {timeZones().map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("companies.main_currency")} hint={t("companies.main_currency_hint")}>
            <select
              value={form.main_currency}
              disabled={!editable}
              onChange={(e) => setForm({ ...form, main_currency: e.target.value })}
            >
              <option value="">—</option>
              {currencies().map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <ErrorMessage error={save.error} />
        {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
        {editable ? (
          <div className="actions">
            <button type="submit" disabled={save.pending}>
              {t("common.save")}
            </button>
          </div>
        ) : null}
      </form>
    </Card>
  );
}
