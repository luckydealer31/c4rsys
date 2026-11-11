import { useTranslation } from "react-i18next";

import type { Vehicle, VehicleTerms } from "../../api/types";

/** What is entered for one vehicle of a tariff (customer's decision of 11.11.2026): its price of
 * a unit in every range; the mileage included in a unit and the price over it — empty means no
 * limit of mileage; the deposit and the days it is held — empty means no deposit. */
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

export function rowOf(terms: VehicleTerms): VehicleRow {
  return {
    checked: true,
    rates: Object.fromEntries(terms.rates.map((r) => [r.from, String(Number(r.rate))])),
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
  if (ranges.some((r) => !(Number(number(row.rates[r.from] ?? "")) > 0))) return "terms.problem_rates";
  if (Boolean(row.included.trim()) !== Boolean(row.overage.trim())) return "terms.problem_mileage";
  if (row.hold.trim() && !row.deposit.trim()) return "terms.problem_hold";
  return null;
}

export function rowBody(row: VehicleRow, ranges: Bounds[]): Record<string, unknown> {
  const included = Boolean(row.included.trim());
  const deposit = Boolean(row.deposit.trim());
  return {
    rates: ranges.map((r) => ({ from: r.from, rate: number(row.rates[r.from] ?? "") })),
    mileage: included
      ? { mode: "included", included_per_unit: number(row.included), overage_price: number(row.overage) }
      : { mode: "unlimited" },
    deposit: deposit
      ? { required: true, amount: number(row.deposit), hold_days: row.hold.trim() ? Number(row.hold) : null }
      : { required: false },
  };
}

/** The vehicles of the company with a mark and, for the marked ones, their prices and terms. */
export function VehiclePricesTable({
  vehicles,
  ranges,
  rows,
  onChange,
  unit,
  disabled = false,
}: {
  vehicles: Vehicle[];
  ranges: Bounds[];
  rows: Record<string, VehicleRow>;
  onChange: (rows: Record<string, VehicleRow>) => void;
  unit: string;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
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
  if (vehicles.length === 0) return <p className="muted">{t("terms.no_vehicles")}</p>;
  return (
    <div className="table-scroll">
      <table className="prices-table">
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
            </th>
            {ranges.map((r) => (
              <th key={r.from}>
                {r.to === null ? t("terms.range_from", { from: r.from, unit }) : t("terms.range", { from: r.from, to: r.to, unit })}
              </th>
            ))}
            <th>{t("terms.included_short")}</th>
            <th>{t("terms.overage_short")}</th>
            <th>{t("terms.deposit")}</th>
            <th>{t("terms.hold_short")}</th>
          </tr>
        </thead>
        <tbody>
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
                    {ranges.map((r) => (
                      <td key={r.from}>
                        <input
                          inputMode="decimal"
                          disabled={disabled}
                          aria-label={`${v.name}: ${t("terms.price")} ${r.from}`}
                          value={current.rates[r.from] ?? ""}
                          onChange={(e) => set(v.id, { rates: { ...current.rates, [r.from]: e.target.value } })}
                        />
                      </td>
                    ))}
                    <td>{input(v.id, "included", `${v.name}: ${t("terms.included_short")}`)}</td>
                    <td>{input(v.id, "overage", `${v.name}: ${t("terms.overage_short")}`)}</td>
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
      <p className="muted small">{t("terms.table_hint")}</p>
    </div>
  );
}
