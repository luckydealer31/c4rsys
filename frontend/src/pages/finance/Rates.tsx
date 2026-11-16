import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import { Field } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";

export type RateSuggestion = {
  from: string;
  to: string;
  rate: string | null;
  source: string | null;
  fetched_at: string | null;
  stale: boolean | null;
  latest_fetch_at: string | null;
  may_override: boolean;
};

export function useRateSuggestion(from: string | undefined, to: string | undefined) {
  const company = useCompany();
  const needed = Boolean(from && to && from !== to);
  return useApi<RateSuggestion>(
    ["rate-suggestion", company.public_id, from ?? "", to ?? ""],
    `/c/${company.public_id}/exchange-rates/suggestion?from=${from}&to=${to}`,
    needed,
  );
}

/** The rate actually used: a manual one when entered, otherwise the suggested one. */
export function effectiveRate(suggestion: RateSuggestion | undefined, manual: string): string | null {
  return manual.trim() ? manual.trim() : (suggestion?.rate ?? null);
}

/** Exchange rate of an operation (§ 17.1.3, § 17.1.8): the last system rate with its time and
 * source, a warning when it is outdated; another rate only with the right (§ 23.7.6). Without a
 * rate a manual one is required, no guess is made. */
export function RateField({
  from,
  to,
  manual,
  onManual,
}: {
  from: string;
  to: string;
  manual: string;
  onManual: (value: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const suggestion = useRateSuggestion(from, to);
  const data = suggestion.data;
  const zone = company.timezone ?? "UTC";
  if (!data) return null;
  return (
    <div className="stack">
      {data.rate ? (
        <p className={data.stale ? "alert error" : "muted"}>
          {t("rates.suggested", { from, to, rate: data.rate })}{" "}
          {t("rates.source", { time: formatMoment(data.fetched_at, zone, i18n.language) })}
          {data.stale ? ` ${t("rates.stale")}` : ""}
        </p>
      ) : (
        <p className="alert error">
          {data.latest_fetch_at
            ? t("rates.none_since", { time: formatMoment(data.latest_fetch_at, zone, i18n.language) })
            : t("rates.none")}{" "}
          {data.may_override ? t("rates.enter_manual") : t("rates.no_right")}
        </p>
      )}
      {data.may_override ? (
        <Field label={t("rates.manual", { from, to })} hint={t("rates.manual_hint")}>
          <input
            inputMode="decimal"
            value={manual}
            placeholder={data.rate ?? ""}
            onChange={(e) => onManual(e.target.value.replace(",", "."))}
          />
        </Field>
      ) : null}
    </div>
  );
}

/** The rate and the equivalent of an amount, two fields in one line (decision of 16.11.2026):
 * the last loaded rate stands in the field at once; either of the two may be changed and the
 * other follows — a new rate gives the equivalent, a new equivalent gives the rate. Nothing is
 * printed around them: where the rate came from is shown under the pointer; only an outdated
 * or a missing rate is said aloud. Changing either needs the right to set a rate (§ 23.7.6). */
export function RateAmount({
  from,
  to,
  amount,
  manual,
  onManual,
  lead,
}: {
  from: string;
  to: string;
  /** In the currency `from`. */
  amount: string;
  manual: string;
  onManual: (value: string) => void;
  /** The field of the amount itself: it stands first in the same line (decision of 16.11.2026). */
  lead?: ReactNode;
}) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const suggestion = useRateSuggestion(from, to);
  const data = suggestion.data;
  // The equivalent while it is being typed; else it follows the amount and the rate.
  const [typed, setTyped] = useState<string | null>(null);
  useEffect(() => setTyped(null), [amount, from, to]);
  if (!data) return lead ? <div className="row">{lead}</div> : null;
  const zone = company.timezone ?? "UTC";
  const rate = effectiveRate(data, manual);
  const source = Number(amount) > 0 ? Number(amount) : 0;
  const derived = rate && source ? (source * Number(rate)).toFixed(2) : "";
  const origin = data.rate
    ? `${t("rates.suggested", { from, to, rate: data.rate })} ${t("rates.source", { time: formatMoment(data.fetched_at, zone, i18n.language) })}`
    : undefined;
  return (
    <>
      {data.rate && data.stale ? <p className="alert error">{t("rates.stale")}</p> : null}
      {/* Until a rate is typed in: then there is nothing to warn about. */}
      {data.rate || manual ? null : (
        <p className="alert error">
          {data.latest_fetch_at
            ? t("rates.none_since", { time: formatMoment(data.latest_fetch_at, zone, i18n.language) })
            : t("rates.none")}{" "}
          {data.may_override ? t("rates.enter_manual") : t("rates.no_right")}
        </p>
      )}
      <div className="row">
        {lead}
        <Field label={t("rates.rate")}>
          <input
            inputMode="decimal"
            title={origin}
            value={manual || data.rate || ""}
            disabled={!data.may_override}
            onChange={(e) => {
              setTyped(null);
              onManual(e.target.value.replace(",", "."));
            }}
          />
        </Field>
        <Field label={t("rates.equivalent", { currency: to })}>
          <input
            inputMode="decimal"
            value={typed ?? derived}
            disabled={!data.may_override || !source}
            onChange={(e) => {
              const value = e.target.value.replace(",", ".");
              setTyped(value);
              const wanted = Number(value);
              // The rate that turns the amount into this equivalent; 12 places as the server keeps.
              onManual(wanted > 0 && source ? String(Number((wanted / source).toFixed(12))) : "");
            }}
          />
        </Field>
      </div>
    </>
  );
}
