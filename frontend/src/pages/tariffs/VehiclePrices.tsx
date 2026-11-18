import { useTranslation } from "react-i18next";

import { Info } from "../../components/ui";

import type { Vehicle, VehicleTerms } from "../../api/types";

/** What is entered for one vehicle of a tariff (customer's decision of 11.11.2026): its price of
 * a unit in every range; the mileage included in a unit and the price over it — empty means no
 * limit of mileage; the deposit and the days it is held — empty means no deposit. The prices are
 * kept by the column of the range, so they stay in their column when a bound moves. */
export type VehicleRow = {
  checked: boolean;
  rates: Record<number, string>;
  included: string;
  overage: string;
  deposit: string;
  hold: string;
};

export type Bounds = { from: number; to: number | null };

export const EMPTY_ROW: VehicleRow = { checked: false, rates: {}, included: "", overage: "", deposit: "", hold: "" };

const number = (value: string) => value.trim().replace(",", ".");

export function rowOf(terms: VehicleTerms, ranges: Bounds[]): VehicleRow {
  return {
    checked: true,
    rates: Object.fromEntries(
      terms.rates
        .map((r) => [ranges.findIndex((b) => b.from === r.from), String(Number(r.rate))] as const)
        .filter(([index]) => index >= 0),
    ),
    included: terms.mileage.included_per_unit ? String(Number(terms.mileage.included_per_unit)) : "",
    overage: terms.mileage.overage_price ? String(Number(terms.mileage.overage_price)) : "",
    deposit: terms.deposit.amount ? String(Number(terms.deposit.amount)) : "",
    hold: terms.deposit.hold_days ? String(terms.deposit.hold_days) : "",
  };
}

/** What the row lacks, as a translation key, or nothing. A marked vehicle needs a price in
 * every range; the mileage included and the price over it go together; the days of holding
 * need a deposit. */
export function rowProblem(row: VehicleRow, ranges: Bounds[]): string | null {
  if (!row.checked) return null;
  if (ranges.some((_, i) => !(Number(number(row.rates[i] ?? "")) > 0))) return "terms.problem_rates";
  if (Boolean(row.included.trim()) !== Boolean(row.overage.trim())) return "terms.problem_mileage";
  if (row.hold.trim() && !row.deposit.trim()) return "terms.problem_hold";
  return null;
}

export function rowBody(row: VehicleRow, ranges: Bounds[]): Record<string, unknown> {
  const included = Boolean(row.included.trim());
  const deposit = Boolean(row.deposit.trim());
  return {
    rates: ranges.map((r, i) => ({ from: r.from, rate: number(row.rates[i] ?? "") })),
    mileage: included
      ? { mode: "included", included_per_unit: number(row.included), overage_price: number(row.overage) }
      : { mode: "unlimited" },
    deposit: deposit
      ? { required: true, amount: number(row.deposit), hold_days: row.hold.trim() ? Number(row.hold) : null }
      : { required: false },
  };
}

/** The ranges edited in the header of the table (customer's decision of 19.11.2026): the end
 * of each range is typed in the header of its column; the end typed for the last column adds a
 * new one after it; × takes a column away. `tos` — the ends as typed, the last one empty. */
export type RangesEdit = {
  tos: string[];
  onTo: (index: number, value: string) => void;
  onRemove: (index: number) => void;
};

/** The vehicles of the company with a mark and, for the marked ones, their prices and terms. */
export function VehiclePricesTable({
  vehicles,
  ranges,
  rows,
  onChange,
  unit,
  currency = "",
  disabled = false,
  edit,
}: {
  vehicles: Vehicle[];
  ranges: Bounds[];
  rows: Record<string, VehicleRow>;
  onChange: (rows: Record<string, VehicleRow>) => void;
  unit: string;
  currency?: string;
  disabled?: boolean;
  edit?: RangesEdit;
}) {
  const { t, i18n } = useTranslation();
  const row = (id: string) => rows[id] ?? EMPTY_ROW;
  const set = (id: string, changes: Partial<VehicleRow>) => onChange({ ...rows, [id]: { ...row(id), ...changes } });
  const all = vehicles.length > 0 && vehicles.every((v) => row(v.id).checked);
  const input = (id: string, key: "included" | "overage" | "deposit" | "hold", label: string) => (
    <input
      inputMode="decimal"
      aria-label={label}
      disabled={disabled}
      value={row(id)[key]}
      onChange={(e) => set(id, { [key]: e.target.value })}
    />
  );
  /** A field with what its number is measured in written inside its box, after the number. */
  const measured = (id: string, key: "included" | "overage", label: string, suffix: string) => (
    <label className="unit-field">
      {input(id, key, label)}
      <span className="unit-suffix" aria-hidden="true">
        {suffix}
      </span>
    </label>
  );
  /** «км», or the mile in the form the number asks for: 1 миля, 2 мили, 5 миль. */
  const distance = (odometer: string, value: string) => {
    if (odometer !== "mi") return t("terms.km");
    const count = Number(number(value)) || 0;
    return t(`terms.mi_${new Intl.PluralRules(i18n.language).select(count)}`);
  };
  if (vehicles.length === 0 && !edit) return <p className="muted">{t("terms.no_vehicles")}</p>;
  const begin = (r: Bounds) => (Number.isFinite(r.from) ? r.from : "…");
  /** The header of a range: its beginning, the field of its end with the unit inside, ×. */
  const head = (r: Bounds, i: number) => {
    if (!edit) {
      return r.to === null ? t("terms.range_from", { from: r.from, unit }) : t("terms.range", { from: r.from, to: r.to, unit });
    }
    const last = i === ranges.length - 1;
    const to = edit.tos[i] ?? "";
    const wrong = !last && !(/^\d+$/.test(to) && Number.isFinite(r.from) && Number(to) >= r.from);
    return (
      <div className="range-edit">
        <span className="range-begin">{t("terms.range_begin", { from: begin(r) })}</span>
          <label className={`unit-field${wrong ? " invalid" : ""}`}>
            <input
              inputMode="numeric"
              value={to}
              disabled={disabled}
              placeholder={last ? t("terms.open_end") : ""}
              aria-label={t("terms.range_to", { from: begin(r) })}
              aria-invalid={wrong || undefined}
              onChange={(e) => edit.onTo(i, e.target.value.replace(/\D/g, ""))}
            />
            <span className="unit-suffix" aria-hidden="true">
              {unit}
            </span>
          </label>
          {ranges.length > 1 ? (
            <button
              type="button"
              className="icon-button"
              disabled={disabled}
              aria-label={t("terms.range_remove", { from: begin(r) })}
              title={t("tariffs.remove_range")}
              onClick={() => edit.onRemove(i)}
            >
              ×
            </button>
          ) : null}
      </div>
    );
  };
  return (
    <div className="table-scroll">
      <table className={`prices-table${edit ? " editing" : ""}`}>
        <thead>
          <tr>
            <th>
              <label>
                <input
                  type="checkbox"
                  disabled={disabled}
                  checked={all}
                  onChange={() =>
                    onChange(Object.fromEntries(vehicles.map((v) => [v.id, { ...row(v.id), checked: !all }])))
                  }
                />{" "}
                {t("terms.select_all")}
              </label>
              <Info text={t(edit ? "terms.select_hint_edit" : "terms.select_hint")} />
            </th>
            {ranges.map((r, i) => (
              <th key={i} className={edit ? "range-head" : undefined}>
                {head(r, i)}
              </th>
            ))}
            {/* That the mileage is counted for one unit of the tariff is said under «i»; in what it
                is measured — inside the field of each vehicle, by its odometer (customer's
                remarks of 18.11.2026). */}
            <th>
              {t("terms.included_short")}
              <Info text={t("terms.included_hint", { unit })} />
            </th>
            <th>
              {t("terms.overage_short")}
              <Info text={t("terms.overage_hint")} />
            </th>
            <th>
              {t("terms.deposit")}
              <Info text={t("terms.deposit_hint")} />
            </th>
            <th>{t("terms.hold_short")}</th>
          </tr>
        </thead>
        <tbody>
          {vehicles.length === 0 ? (
            <tr>
              <td colSpan={ranges.length + 5} className="muted">
                {t("terms.no_vehicles")}
              </td>
            </tr>
          ) : null}
          {vehicles.map((v) => {
            const current = row(v.id);
            const problem = rowProblem(current, ranges);
            return (
              <tr key={v.id}>
                <td>
                  <label>
                    <input
                      type="checkbox"
                      disabled={disabled}
                      checked={current.checked}
                      onChange={() => set(v.id, { checked: !current.checked })}
                    />{" "}
                    {v.name}
                  </label>
                  {problem ? <div className="small error-text">{t(problem)}</div> : null}
                </td>
                {current.checked ? (
                  <>
                    {ranges.map((r, i) => (
                      <td key={i}>
                        <input
                          inputMode="decimal"
                          disabled={disabled}
                          aria-label={`${v.name}: ${t("terms.price")} ${begin(r)}`}
                          value={current.rates[i] ?? ""}
                          onChange={(e) => set(v.id, { rates: { ...current.rates, [i]: e.target.value } })}
                        />
                      </td>
                    ))}
                    <td>
                      {measured(
                        v.id,
                        "included",
                        `${v.name}: ${t("terms.included_short")}`,
                        distance(v.odometer_unit, current.included),
                      )}
                    </td>
                    <td>
                      {measured(
                        v.id,
                        "overage",
                        `${v.name}: ${t("terms.overage_short")}`,
                        t(`terms.per_${v.odometer_unit}`, { currency }).trim(),
                      )}
                    </td>
                    <td>{input(v.id, "deposit", `${v.name}: ${t("terms.deposit")}`)}</td>
                    <td>{input(v.id, "hold", `${v.name}: ${t("terms.hold_short")}`)}</td>
                  </>
                ) : (
                  <td colSpan={ranges.length + 4} className="muted small">
                    {t("terms.not_marked")}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
