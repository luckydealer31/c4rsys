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
