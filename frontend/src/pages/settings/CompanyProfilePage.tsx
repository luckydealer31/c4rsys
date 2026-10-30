import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { Company, JournalEntry, PartyCard } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Avatar, ImagePicker } from "../../components/images";
import { TimeZoneSelect } from "../../components/TimeZoneSelect";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { currencies, deviceTimeZone, formatMoment } from "../../time";

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
  const company = useCompany();
  const can = useCan();
  return (
    <div className="page">
      <h1>{t("company.settings")}</h1>
      <CompanyProfileForm withRequisites />
      {company.employment?.is_top_authority || company.employment?.has_full_rights ? (
        <PublicFieldsCard key={company.version} />
      ) : null}
      <SearchCardPreview />
      {can("company.settings") ? <ProfileHistoryCard /> : null}
    </div>
  );
}

// § 3.1.8: additional profile fields that may be made public.
const PUBLIC_FIELDS = [
  "legal_name",
  "legal_form",
  "identifiers",
  "legal_address",
  "actual_address",
  "phone",
  "email",
  "website",
  "description",
] as const;

/** A switch of publicity for each additional field (§ 3.1.8). It does not reach documents,
 * owners or the internal history. */
function PublicFieldsCard() {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const [chosen, setChosen] = useState<string[]>(company.public_fields ?? []);
  const [saved, setSaved] = useState(false);
  const save = useCommand<{ fields: string[]; expected_version: number }, unknown>(
    `/c/${company.public_id}/public-fields`,
    "PUT",
  );
  return (
    <Card title={t("company.public_title")}>
      <p className="muted small">{t("company.public_hint")}</p>
      {PUBLIC_FIELDS.map((key) => (
        <label key={key} className="small" style={{ display: "block", margin: "6px 0" }}>
          <input
            type="checkbox"
            checked={chosen.includes(key)}
            onChange={() =>
              setChosen(chosen.includes(key) ? chosen.filter((x) => x !== key) : [...chosen, key])
            }
          />{" "}
          {t(`company.${key}`)}
        </label>
      ))}
      <ErrorMessage error={save.error} />
      {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
      <div className="actions">
        <button
          type="button"
          disabled={save.pending}
          onClick={async () => {
            setSaved(false);
            if ((await save.run({ fields: chosen, expected_version: company.version })) !== null) {
              setSaved(true);
              await client.invalidateQueries({ queryKey: ["company", company.public_id] });
              await client.invalidateQueries({ queryKey: ["company-card", company.public_id] });
            }
          }}
        >
          {t("common.save")}
        </button>
      </div>
    </Card>
  );
}

/** How others see the company when searching by its exact ID (§ 3.5.3). */
function SearchCardPreview() {
  const { t } = useTranslation();
  const company = useCompany();
  const card = useApi<{ company: PartyCard }>(
    ["company-card", company.public_id, company.version],
    `/companies/lookup?id=${encodeURIComponent(company.public_id)}`,
  );
  const data = card.data?.company;
  if (!data || data.type !== "company") return null;
  const show = (value: unknown) =>
    Array.isArray(value)
      ? value.map((x: { type: string; value: string }) => `${x.type}: ${x.value}`).join(", ")
      : String(value);
  return (
    <Card title={t("company.search_card")}>
      <p className="muted small">{t("company.search_card_hint")}</p>
      <p>
        <Avatar file={data.logo ?? null} name={data.display_name} size={32} square /> <strong>{data.display_name}</strong>
      </p>
      <p className="small">
        {data.public_id}
        {data.country ? ` · ${data.country}` : ""}
      </p>
      {Object.entries(data.public ?? {}).map(([key, value]) => (
        <p key={key} className="small">
          {t(`company.${key}`)}: {show(value)}
        </p>
      ))}
    </Card>
  );
}

/** § 3.5.2: initiator, time, changed fields with the old and new values. */
function ProfileHistoryCard() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const history = useApi<{ history: JournalEntry[] }>(
    ["profile-history", company.public_id, company.version],
    `/c/${company.public_id}/profile-history`,
  );
  const zone = company.timezone ?? deviceTimeZone();
  const value = (v: unknown, field: string) => {
    if (field === "public_fields" && Array.isArray(v))
      return v.length ? v.map((x) => t(`company.${String(x)}`)).join(", ") : "—";
    return v === null || v === "" ? "—" : typeof v === "string" ? v : JSON.stringify(v);
  };
  return (
    <Card title={t("company.history")}>
      {history.isLoading ? <Loading /> : null}
      <table>
        <thead>
          <tr>
            <th>{t("history.when")}</th>
            <th>{t("history.author")}</th>
            <th>{t("company.history_changes")}</th>
          </tr>
        </thead>
        <tbody>
          {(history.data?.history ?? []).map((entry) => (
            <tr key={entry.id}>
              <td className="small">{formatMoment(entry.occurred_at, zone, i18n.language)}</td>
              <td className="small">{entry.actor.name || entry.actor.user_id || "—"}</td>
              <td className="small">
                {entry.action === "company.created" ? t("company.history_created") : null}
                {entry.action !== "company.created"
                  ? Object.keys(entry.after ?? {}).map((key) => (
                      <div key={key}>
                        {t(`company.${key}`, { defaultValue: key })}: {value(entry.before?.[key], key)} →{" "}
                        {value(entry.after?.[key], key)}
                      </div>
                    ))
                  : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
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
