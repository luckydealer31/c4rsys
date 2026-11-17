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
 * or a missing rate is said aloud. Changing either needs the right to set a rate (§ 23.7.6).
 *
 * The rate is shown the way it is said: «1 USD = 37 THB», not «1 THB = 0.027 USD» (decision of
 * 16.11.2026) — turned over when it is below one. `manual` stays the rate from `from` to `to`.
 *
 * The line is drawn the same in every state — before the rate is loaded, without an exchange
 * (`to` not given), with the equivalent known beforehand (`exact`) — so the field of the amount
 * is never made anew: what is being typed into it while the rate comes is not lost, and the
 * focus stays (found on 17.11.2026 by a test on a loaded machine). */
export function RateAmount({
  from,
  to,
  amount,
  manual,
  onManual,
  lead,
  exact,
}: {
  from?: string;
  /** Not given — the amount is not exchanged: only the line with its field is drawn. */
  to?: string;
  /** In the currency `from`. */
  amount: string;
  manual: string;
  onManual: (value: string) => void;
  /** The field of the amount itself: it stands first in the same line (decision of 16.11.2026). */
  lead?: ReactNode;
  /** The equivalent known without a rate — what came in is paid back: shown, not changed. */
  exact?: string;
}) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const suggestion = useRateSuggestion(from, to);
  const data = suggestion.data;
  // The equivalent and the rate while they are being typed; else they follow each other.
  const [typed, setTyped] = useState<string | null>(null);
  const [typedRate, setTypedRate] = useState<string | null>(null);
  // Without a loaded rate the way to show it is known only from a typed equivalent.
  const [turned, setTurned] = useState<boolean | null>(null);
  useEffect(() => {
    setTyped(null);
    setTypedRate(null);
  }, [amount, from, to]);
  useEffect(() => setTurned(null), [from, to]);
  // The rate is asked for and shown only when the amount is exchanged by a rate.
  const rated = to !== undefined && exact === undefined && data ? data : null;
  if (!lead && !rated && exact === undefined) return null;
  const zone = company.timezone ?? "UTC";
  const rate = rated ? effectiveRate(rated, manual) : null;
  const source = Number(amount) > 0 ? Number(amount) : 0;
  const derived = rate && source ? (source * Number(rate)).toFixed(2) : "";
  const inverse = rated?.rate ? Number(rated.rate) < 1 : (turned ?? false);
  const over = (value: string | null) => (value && Number(value) > 0 ? String(Number((1 / Number(value)).toFixed(6))) : "");
  const shown = inverse ? over(rate) : (rate ?? "");
  const [one, other] = inverse ? [to, from] : [from, to];
  const direction = t("rates.manual", { from: one, to: other });
  const origin = rated?.rate
    ? `${t("rates.suggested", { from: one, to: other, rate: inverse ? over(rated.rate) : rated.rate })} ${t("rates.source", { time: formatMoment(rated.fetched_at, zone, i18n.language) })}`
    : direction;
  // The fields of the rate and of the equivalent, when the amount is exchanged by a rate.
  const fields = rated ? (
  <>
    <Field label={t("rates.rate")}>
      <input
        inputMode="decimal"
        title={origin}
        value={typedRate ?? shown}
        disabled={!rated.may_override}
        onChange={(e) => {
          const value = e.target.value.replace(",", ".");
          setTyped(null);
          setTypedRate(value);
          // 12 places as the server keeps.
          onManual(inverse ? (Number(value) > 0 ? String(Number((1 / Number(value)).toFixed(12))) : "") : value);
        }}
      />
    </Field>
    <Field label={t("rates.equivalent", { currency: to })}>
      <input
        inputMode="decimal"
        value={typed ?? derived}
        disabled={!rated.may_override || !source}
        onChange={(e) => {
          const value = e.target.value.replace(",", ".");
          setTyped(value);
          setTypedRate(null);
          const wanted = Number(value);
          if (!rated.rate && wanted > 0 && source) setTurned(wanted < source);
          // The rate that turns the amount into this equivalent.
          onManual(wanted > 0 && source ? String(Number((wanted / source).toFixed(12))) : "");
        }}
      />
    </Field>
  </>
  ) : null;
  // The three places below are always there, empty or not: the line with the field of the
  // amount keeps its place and is not made anew.
  return (
    <>
      {rated?.rate && rated.stale ? <p className="alert error">{t("rates.stale")}</p> : null}
      {/* Until a rate is typed in: then there is nothing to warn about. */}
      {!rated || rated.rate || manual ? null : (
        <p className="alert error">
          {rated.latest_fetch_at
            ? t("rates.none_since", { time: formatMoment(rated.latest_fetch_at, zone, i18n.language) })
            : t("rates.none")}{" "}
          {rated.may_override ? t("rates.enter_manual") : t("rates.no_right")}
        </p>
      )}
      <div className="row">
        {lead}
        {exact !== undefined && to ? (
          <Field label={t("rates.equivalent", { currency: to })}>
            <input value={exact} disabled readOnly />
          </Field>
        ) : null}
        {fields}
      </div>
    </>
  );
}
