import { useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useParams } from "react-router";

import { useApi } from "../../api/queries";
import type { Tariff, TariffRange, VehicleTerms } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Info, Loading, UnconfiguredConfirm } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { useLocations } from "../settings/LocationsPage";
import { useVehicles } from "../vehicles/VehiclesPage";
import {
  EMPTY_ROW,
  rowBody,
  rowOf,
  rowProblem,
  VehiclePricesTable,
  type Bounds,
  type VehicleRow,
} from "./VehiclePrices";

export function useTariffs() {
  const company = useCompany();
  return useApi<{ tariffs: Tariff[] }>(["tariffs", company.public_id], `/c/${company.public_id}/tariffs`);
}

/** Unit of a tariff as one choice: minute, hour, a day «24 hours from issue», settlement days with
 * a daily boundary, calendar month (§ 13.1.4, § 13.2). */
export function unitText(tariff: { unit: string; day_mode: string | null; day_boundary?: string | null }, t: TFunction) {
  if (tariff.unit === "day" && tariff.day_mode === "fixed_boundary") {
    return t("tariffs.kind_fixed_boundary", { time: tariff.day_boundary ?? "" });
  }
  return t(`tariffs.kind_${tariff.unit === "day" ? "from_issue" : tariff.unit}`);
}

/** Short name of the billable unit: «min», «h», «days», «months». */
export function unitShort(unit: string | null | undefined, t: TFunction) {
  return t(`tariffs.short_${unit ?? "day"}`);
}

/** Tariff catalog: tariffs of any unit with price ranges (§ 13.1–13.2). */
/** The list of tariffs lives in the rental section now (decision of 11.11.2026). */
export function TariffsPage() {
  const company = useCompany();
  return <Navigate to={companyPath(company, "/rentals?tab=catalog")} replace />;
}

/** The ranges as text; with a price where one is given (the prices of a vehicle), else only
 * the bounds — the prices of a tariff belong to its vehicles. */
export function rangesText(ranges: TariffRange[], t: TFunction, unit = "day") {
  const short = unitShort(unit, t);
  return ranges
    .map((r) => {
      const bounds = r.to === null ? t("terms.range_from", { from: r.from, unit: short }) : t("terms.range", { from: r.from, to: r.to, unit: short });
      return r.rate === null ? bounds : `${bounds}: ${Number(r.rate)}`;
    })
    .join("; ");
}

export const KINDS = ["minute", "hour", "from_issue", "fixed_boundary", "month"] as const;
export type Kind = (typeof KINDS)[number];

export function kindBody(kind: Kind, boundary: string): Record<string, unknown> {
  if (kind === "from_issue") return { unit: "day", day_mode: "from_issue" };
  if (kind === "fixed_boundary") return { unit: "day", day_mode: "fixed_boundary", day_boundary: boundary };
  return { unit: kind };
}

export type RangeRow = { from: string; to: string; rate: string };

/** Each range begins right after the one before it: the first with 1, the next with the «to» of
 * the previous plus one (§ 13.2.8). */
function chained(rows: RangeRow[]): RangeRow[] {
  return rows.map((row, i) => {
    const before = rows[i - 1]?.to ?? "";
    return { ...row, from: i === 0 ? "1" : /^\d+$/.test(before) ? String(Number(before) + 1) : "" };
  });
}

/** Whether the ranges may be saved: every range but the last ends, and not before it begins; the
 * last one has no upper bound. */
export function rangesValid(rows: RangeRow[]): boolean {
  return chained(rows).every((row, i) =>
    i === rows.length - 1 ? row.to.trim() === "" : /^\d+$/.test(row.to) && Number(row.to) >= Number(row.from),
  );
}

/** The price ranges (§ 13.2.8; customer's decision of 18.11.2026). Only «to» is typed: «from»
 * follows by itself, and a range that got its «to» is followed by the next one at once — from
 * that value plus one, without an upper bound. Emptied, the last «to» takes the range added
 * after it away again. There is no button to add a range. The server checks the structure. */
export function RangesEditor({
  rows,
  onChange,
  unit,
}: {
  rows: RangeRow[];
  onChange: (rows: RangeRow[]) => void;
  unit: string;
}) {
  const { t } = useTranslation();
  const shown = chained(rows);
  function setTo(index: number, value: string) {
    const to = value.replace(/\D/g, "");
    let next = rows.map((row, i) => (i === index ? { ...row, to } : row));
    const last = next.length - 1;
    // The last range got its end: the next one begins after it, without an upper bound.
    if (index === last && to !== "") next = [...next, { from: "", to: "", rate: "" }];
    // The end is taken back: the untouched range added after it goes with it.
    if (index === last - 1 && to === "" && next[last]?.to === "") next = next.slice(0, -1);
    onChange(chained(next));
  }
  return (
    <div className="stack">
      <table className="ranges-table">
        <thead>
          <tr>
            <th>{t("tariffs.from", { unit: unitShort(unit, t) })}</th>
            <th>
              {t("tariffs.to", { unit: unitShort(unit, t) })}
              <Info text={t("tariffs.ranges_hint")} />
            </th>
            <th />
          </tr>
        </thead>
        <tbody>
          {shown.map((row, index) => {
            const last = index === shown.length - 1;
            const wrong = !last && row.to !== "" && Number(row.to) < Number(row.from);
            return (
              <tr key={index}>
                <td>
                  <input value={row.from} readOnly disabled aria-label={t("tariffs.from", { unit: unitShort(unit, t) })} />
                </td>
                <td>
                  <input
                    value={row.to}
                    inputMode="numeric"
                    aria-label={t("tariffs.to", { unit: unitShort(unit, t) })}
                    aria-invalid={wrong || undefined}
                    placeholder={last ? t("tariffs.no_limit") : ""}
                    onChange={(e) => setTo(index, e.target.value)}
                  />
                </td>
                <td>
                  {/* The last range goes when the «to» before it is emptied. */}
                  {!last && shown.length > 2 ? (
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={t("tariffs.remove_range")}
                      title={t("tariffs.remove_range")}
                      onClick={() => onChange(chained(rows.filter((_, i) => i !== index)))}
                    >
                      ×
                    </button>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function fromRows(rows: RangeRow[]) {
  // Bounds only: the price of a range is set for each vehicle (decision of 11.11.2026).
  return rows.map((row) => ({ from: Number(row.from), to: row.to.trim() === "" ? null : Number(row.to) }));
}

/** One tariff (customer's decision of 19.11.2026): its name and availability, and the table of
 * its vehicles whose columns are the price ranges — the end of a range is edited in the header of
 * its column, × takes a column away, the end typed for the last column adds a new one with the
 * same prices. Everything is saved with one button, in one command. An archived tariff is only
 * shown. */
function TariffEditor({ tariff, onSaved, aside }: { tariff: Tariff; onSaved: () => void; aside?: ReactNode }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const locations = useLocations();
  const terms = useApi<{ vehicles: VehicleTerms[] }>(
    ["tariff-vehicles", company.public_id, tariff.id],
    `/c/${company.public_id}/tariffs/${tariff.id}/vehicles`,
  );
  const vehicles = useVehicles();
  const editable = can("tariff.edit") && tariff.state === "active";
  const [name, setName] = useState(tariff.name);
  const [scope, setScope] = useState<string[] | "all">(tariff.all_locations ? "all" : tariff.locations);
  const [tos, setTos] = useState<string[]>(tariff.ranges.map((r) => (r.to === null ? "" : String(r.to))));
  const [edited, setEdited] = useState<Record<string, VehicleRow> | null>(null);
  const [dirty, setDirty] = useState(false);
  const save = useCommand<Record<string, unknown>, { tariff: Tariff }>(
    `/c/${company.public_id}/tariffs/${tariff.id}/setup`,
    "PUT",
  );

  const assigned = new Map((terms.data?.vehicles ?? []).map((item) => [item.vehicle_card_id, item]));
  // The own and managed vehicles of the rental, and every vehicle that has the tariff.
  const list = (vehicles.data?.vehicles ?? []).filter(
    (v) =>
      assigned.has(v.id) ||
      ((v.relation === "own" || v.relation === "managed") && v.storage === null && v.state !== "archived"),
  );
  const rows = edited ?? Object.fromEntries([...assigned].map(([id, item]) => [id, rowOf(item, tariff.ranges)]));
  const last = tos.length - 1;
  const bounds: Bounds[] = tos.map((to, i) => ({
    from: i === 0 ? 1 : /^\d+$/.test(tos[i - 1] ?? "") ? Number(tos[i - 1]) + 1 : NaN,
    to: i === last ? null : /^\d+$/.test(to) ? Number(to) : NaN,
  }));
  const rangesOk = bounds.every((b, i) => (i === last ? tos[i] === "" : Number(b.to) >= b.from));
  const problem = list.some((v) => rowProblem(rows[v.id] ?? EMPTY_ROW, bounds));

  function change(next: Record<string, VehicleRow>) {
    setEdited(next);
    setDirty(true);
  }
  /** Every row with its prices moved: `price(rates)` gives the new prices of a row. */
  const moved = (price: (rates: Record<number, string>) => Record<number, string>) =>
    Object.fromEntries(Object.entries(rows).map(([id, row]) => [id, { ...row, rates: price(row.rates) }]));
  function onTo(index: number, value: string) {
    setDirty(true);
    if (index === last && value !== "") {
      // The last range got its end: a new one follows it, with the same prices.
      setTos([...tos.slice(0, -1), value, ""]);
      change(moved((rates) => ({ ...rates, [index + 1]: rates[index] ?? "" })));
      return;
    }
    setTos(tos.map((to, i) => (i === index ? value : to)));
  }
  function onRemove(index: number) {
    const next = tos.filter((_, i) => i !== index);
    next[next.length - 1] = "";
    setTos(next);
    // The prices of the columns after the removed one move one column to the left.
    change(
      moved((rates) =>
        Object.fromEntries(
          Object.entries(rates)
            .filter(([key]) => Number(key) !== index)
            .map(([key, rate]) => [Number(key) > index ? Number(key) - 1 : Number(key), rate]),
        ),
      ),
    );
  }

  async function submit() {
    const marked = list.filter((v) => rows[v.id]?.checked);
    const result = await save.run({
      expected_version: tariff.version,
      name,
      locations: scope,
      ranges: bounds.map((b) => ({ from: b.from, to: b.to })),
      vehicles: marked.map((v) => ({ vehicle_card_id: v.id, ...rowBody(rows[v.id] ?? EMPTY_ROW, bounds) })),
      removed: list.filter((v) => assigned.has(v.id) && !rows[v.id]?.checked).map((v) => v.id),
    });
    if (result) {
      await Promise.all(
        ["tariff", "tariffs", "tariff-vehicles", "readiness"].map((key) =>
          client.invalidateQueries({ queryKey: [key, company.public_id] }),
        ),
      );
      onSaved();
    }
  }

  return (
    <>
      <Card title={t("tariffs.edit_title")}>
        <div className="stack">
          <div className="row">
            <Field label={t("tariffs.name")}>
              <input
                value={name}
                disabled={!editable}
                onChange={(e) => {
                  setName(e.target.value);
                  setDirty(true);
                }}
              />
            </Field>
            <Field label={t("cash.currency")} info={t("tariffs.currency_fixed")}>
              <input value={tariff.currency} disabled />
            </Field>
            <Field label={t("tariffs.billing")} info={t("tariffs.unit_fixed")}>
              <input value={unitText(tariff, t)} disabled />
            </Field>
          </div>
          <div className="small">
            {t("tariffs.availability")}:{" "}
            <label>
              <input
                type="checkbox"
                disabled={!editable}
                checked={scope === "all"}
                onChange={() => {
                  setScope(scope === "all" ? [] : "all");
                  setDirty(true);
                }}
              />{" "}
              {t("rights.whole_company")}
            </label>
            {scope !== "all"
              ? (locations.data?.locations ?? []).map((location) => (
                  <label key={location.id} style={{ marginLeft: 12 }}>
                    <input
                      type="checkbox"
                      disabled={!editable}
                      checked={scope.includes(location.id)}
                      onChange={() => {
                        setScope(
                          scope.includes(location.id) ? scope.filter((x) => x !== location.id) : [...scope, location.id],
                        );
                        setDirty(true);
                      }}
                    />{" "}
                    {location.name}
                  </label>
                ))
              : null}
          </div>
        </div>
      </Card>
      <Card title={t("terms.vehicles_title", { currency: tariff.currency })}>
        {terms.isLoading || vehicles.isLoading ? <Loading /> : null}
        <VehiclePricesTable
          vehicles={list}
          ranges={bounds}
          rows={rows}
          onChange={change}
          unit={unitShort(tariff.unit, t)}
          currency={tariff.currency}
          disabled={!editable}
          edit={editable ? { tos, onTo, onRemove } : undefined}
        />
        <ErrorMessage error={save.error} />
        {/* «Save» at the left, the archive at the right, in one line (customer's remark of
            19.11.2026). */}
        <div className="actions apart">
          {editable ? (
            <button
              type="button"
              disabled={!dirty || !name.trim() || !rangesOk || problem || save.pending}
              onClick={() => void submit()}
            >
              {t("common.save")}
            </button>
          ) : (
            <span />
          )}
          {aside}
        </div>
      </Card>
    </>
  );
}

/** One tariff: change, archive or restore, and its vehicles with mileage and deposit terms
 * (§ 13.1.6, § 13.4.1, § 22.1.8). */
export function TariffPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const [saved, setSaved] = useState(false);
  const tariff = useApi<{ tariff: Tariff }>(["tariff", company.public_id, id], `/c/${company.public_id}/tariffs/${id}`);
  const archive = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/tariffs/${id}/archive`);
  const restore = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/tariffs/${id}/restore`);
  if (tariff.isLoading) return <div className="page"><Loading /></div>;
  const data = tariff.data?.tariff;
  if (!data) return <div className="page"><ErrorMessage error={null} /></div>;

  const current = data;
  async function toggle(confirmed = false) {
    const command = current.state === "active" ? archive : restore;
    const body = { expected_version: current.version, ...(confirmed ? { confirm_unconfigured: true } : {}) };
    if ((await command.run(body)) !== null) {
      await client.invalidateQueries({ queryKey: ["tariff", company.public_id] });
      await client.invalidateQueries({ queryKey: ["tariffs", company.public_id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
    }
  }

  // The whole width of the screen: more ranges are seen at once (customer's remark of
  // 19.11.2026); the way back leads to the list of tariffs and services.
  return (
    <div className="page full">
      <p>
        <Link to={companyPath(company, "/rentals?tab=catalog")}>← {t("rentals.tab_catalog")}</Link>
      </p>
      <h1>
        {data.name}{" "}
        <span className={`badge ${data.state === "active" ? "ok" : ""}`}>{t(`tariffs.state_${data.state}`)}</span>
      </h1>
      {/* A new version of the tariff starts the editor afresh; the mark of saving stays. */}
      <TariffEditor
        key={`${data.version}-${data.state}`}
        tariff={data}
        onSaved={() => setSaved(true)}
        aside={
          can("tariff.archive") ? (
            <button type="button" className="secondary" onClick={() => void toggle()}>
              {data.state === "active" ? t("tariffs.archive") : t("tariffs.restore")}
            </button>
          ) : null
        }
      />
      {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
      <UnconfiguredConfirm error={archive.error} pending={archive.pending} onConfirm={() => void toggle(true)} />
      <ErrorMessage error={archive.error ?? restore.error} />
    </div>
  );
}
