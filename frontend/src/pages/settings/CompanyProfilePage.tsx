import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import type { Company } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ImagePicker } from "../../components/images";
import { TimeZoneSelect } from "../../components/TimeZoneSelect";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { currencies, deviceTimeZone } from "../../time";

const REQUISITE_FIELDS = [
  "legal_name",
  "legal_form",
  "legal_address",
  "actual_address",
  "phone",
  "email",
  "website",
] as const;

type Requisites = Pick<
  Company,
  (typeof REQUISITE_FIELDS)[number] | "country" | "description" | "identifiers"
>;

function requisitesOf(company: Company): Requisites {
  return {
    legal_name: company.legal_name,
    legal_form: company.legal_form,
    legal_address: company.legal_address,
    actual_address: company.actual_address,
    phone: company.phone,
    email: company.email,
    website: company.website,
    country: company.country,
    description: company.description,
    identifiers: company.identifiers,
  };
}

/** Settings of the company (decision of 24.10.2026): name, logo, time zone and currency are
 * changed here, not on the company's card; with them the optional data of § 3.1.5. */
export function CompanyProfilePage() {
  const { t } = useTranslation();
  return (
    <div className="page">
      <h1>{t("company.settings")}</h1>
      <CompanyProfileForm withRequisites />
    </div>
  );
}

/** Country codes known to the browser (ISO 3166-1 alpha-2), by their names in the language. */
export function countries(locale: string): { code: string; name: string }[] {
  const names = new Intl.DisplayNames([locale], { type: "region" });
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const pseudo = new Set(["EU", "EZ", "UN", "QO", "XA", "XB", "ZZ"]);
  const result: { code: string; name: string }[] = [];
  for (const a of letters) {
    for (const b of letters) {
      const code = a + b;
      const name = names.of(code);
      if (name && name !== code && !pseudo.has(code)) result.push({ code, name });
    }
  }
  return result.sort((x, y) => x.name.localeCompare(y.name, locale));
}

/** Company profile form: display name, logo (§ 3.1.1), time zone (§ 3.1.6, § 22.1.4), main
 * currency (§ 22.1.7) and, in the settings, the optional data of § 3.1.5. */
export function CompanyProfileForm({
  onSaved,
  withRequisites = false,
}: {
  onSaved?: () => void;
  withRequisites?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const [extra, setExtra] = useState<Requisites>(requisitesOf(company));
  const [form, setForm] = useState({
    display_name: company.display_name,
    logo: company.logo,
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
    if (form.logo !== company.logo) changes.logo = form.logo ?? "";
    if (form.timezone !== company.timezone) changes.timezone = form.timezone;
    if (form.main_currency && form.main_currency !== company.main_currency)
      changes.main_currency = form.main_currency;
    if (withRequisites) {
      const before = requisitesOf(company);
      for (const key of Object.keys(extra) as (keyof Requisites)[]) {
        if (JSON.stringify(extra[key]) !== JSON.stringify(before[key])) changes[key] = extra[key];
      }
    }
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
        <Field label={t("companies.logo")}>
          <ImagePicker
            purpose="company_logo"
            value={form.logo}
            name={form.display_name}
            square
            disabled={!editable}
            onChange={(logo) => setForm({ ...form, logo })}
          />
        </Field>
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
            <TimeZoneSelect
              value={form.timezone}
              disabled={!editable}
              onChange={(timezone) => setForm({ ...form, timezone })}
            />
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
        {withRequisites ? (
          <>
            <h3>{t("company.requisites")}</h3>
            <div className="row">
              {REQUISITE_FIELDS.map((key) => (
                <Field key={key} label={t(`company.${key}`)}>
                  <input
                    value={extra[key]}
                    disabled={!editable}
                    onChange={(e) => setExtra({ ...extra, [key]: e.target.value })}
                  />
                </Field>
              ))}
              <Field label={t("company.country")}>
                <select
                  value={extra.country}
                  disabled={!editable}
                  onChange={(e) => setExtra({ ...extra, country: e.target.value })}
                >
                  <option value="">—</option>
                  {countries(i18n.language).map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="field">
              <span>{t("company.identifiers")}</span>
              {[...extra.identifiers, { type: "", value: "" }].map((item, index) => (
                <div className="row" key={index} style={{ marginBottom: 8 }}>
                  <input
                    aria-label={t("company.identifier_type")}
                    placeholder={t("company.identifier_type")}
                    value={item.type}
                    disabled={!editable}
                    onChange={(e) => {
                      const items = [...extra.identifiers];
                      items[index] = { ...item, type: e.target.value };
                      setExtra({ ...extra, identifiers: items });
                    }}
                  />
                  <input
                    aria-label={t("company.identifier_value")}
                    placeholder={t("company.identifier_value")}
                    value={item.value}
                    disabled={!editable}
                    onChange={(e) => {
                      const items = [...extra.identifiers];
                      items[index] = { ...item, value: e.target.value };
                      setExtra({ ...extra, identifiers: items });
                    }}
                  />
                </div>
              ))}
              <small className="muted">{t("company.identifiers_hint")}</small>
            </div>
            <Field label={t("company.description")}>
              <textarea
                rows={3}
                value={extra.description}
                disabled={!editable}
                onChange={(e) => setExtra({ ...extra, description: e.target.value })}
              />
            </Field>
          </>
        ) : null}
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
