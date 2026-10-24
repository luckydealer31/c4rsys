import { useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { useApi } from "../../api/queries";
import type { PriceRange, Tariff, Vehicle, VehicleTerms } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { currencies } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { useVehicles } from "../vehicles/VehiclesPage";

export function useTariffs() {
  const company = useCompany();
  return useApi<{ tariffs: Tariff[] }>(["tariffs", company.public_id], `/c/${company.public_id}/tariffs`);
}

/** Tariff catalog: a daily tariff «24 hours from issue» with price ranges (§ 13.1–13.2). */
export function TariffsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const tariffs = useTariffs();
  return (
    <div className="page">
      <h1>{t("settings.tariffs")}</h1>
      <Card>
        {tariffs.isLoading ? <Loading /> : null}
        {tariffs.data?.tariffs.length === 0 ? <p className="muted">{t("tariffs.none")}</p> : null}
        {(tariffs.data?.tariffs ?? []).map((tariff) => (
          <Link key={tariff.id} className="list-link" to={companyPath(company, `/rentals/tariffs/${tariff.id}`)}>
            <strong>{tariff.name}</strong>{" "}
            {tariff.state === "archived" ? <span className="badge">{t("tariffs.archived")}</span> : null}
            <div className="muted small">
              {t("tariffs.daily_from_issue")} · {tariff.currency} · {rangesText(tariff.ranges, t)}
            </div>
          </Link>
        ))}
      </Card>
      {can("tariff.create") ? <TariffForm /> : null}
    </div>
  );
}

export function rangesText(ranges: PriceRange[], t: TFunction) {
  return ranges
    .map((r) =>
      r.to === null
        ? t("tariffs.range_open", { from: r.from, rate: Number(r.rate) })
        : t("tariffs.range_closed", { from: r.from, to: r.to, rate: Number(r.rate) }),
    )
    .join("; ");
}

type RangeRow = { from: string; to: string; rate: string };

function toRows(ranges: PriceRange[]): RangeRow[] {
  return ranges.map((r) => ({ from: String(r.from), to: r.to === null ? "" : String(r.to), rate: r.rate }));
}

/** Range rows; the next «from» follows the previous «to» (§ 13.2.8). The server checks the
 * structure. */
function RangesEditor({ rows, onChange }: { rows: RangeRow[]; onChange: (rows: RangeRow[]) => void }) {
  const { t } = useTranslation();
  function set(index: number, key: keyof RangeRow, value: string) {
    const next = rows.map((row, i) => (i === index ? { ...row, [key]: value } : row));
    const following = next[index + 1];
    if (key === "to" && following && /^\d+$/.test(value)) {
      next[index + 1] = { ...following, from: String(Number(value) + 1) };
    }
    onChange(next);
  }
  return (
    <div className="stack">
      <table>
        <thead>
          <tr>
            <th>{t("tariffs.from")}</th>
            <th>{t("tariffs.to")}</th>
            <th>{t("tariffs.rate")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              <td>
                <input value={row.from} inputMode="numeric" onChange={(e) => set(index, "from", e.target.value)} />
              </td>
              <td>
                <input
                  value={row.to}
                  inputMode="numeric"
                  placeholder={index === rows.length - 1 ? t("tariffs.no_limit") : ""}
                  onChange={(e) => set(index, "to", e.target.value)}
                />
              </td>
              <td>
                <input value={row.rate} inputMode="decimal" onChange={(e) => set(index, "rate", e.target.value)} />
              </td>
              <td>
                {rows.length > 1 ? (
                  <button type="button" className="secondary" onClick={() => onChange(rows.filter((_, i) => i !== index))}>
                    ×
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">{t("tariffs.ranges_hint")}</p>
      <div className="actions" style={{ marginTop: 0 }}>
        <button
          type="button"
          className="secondary"
          onClick={() => {
            const last = rows[rows.length - 1];
            onChange([...rows, { from: last?.to ? String(Number(last.to) + 1) : "", to: "", rate: "" }]);
          }}
        >
          {t("tariffs.add_range")}
        </button>
      </div>
    </div>
  );
}

function fromRows(rows: RangeRow[]) {
  return rows.map((row) => ({
    from: Number(row.from),
    to: row.to.trim() === "" ? null : Number(row.to),
    rate: row.rate.trim().replace(",", "."),
  }));
}

function TariffForm({ tariff }: { tariff?: Tariff }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const locations = useLocations();
  const [name, setName] = useState(tariff?.name ?? "");
  const [currency, setCurrency] = useState(tariff?.currency ?? company.main_currency ?? "");
  const [rows, setRows] = useState<RangeRow[]>(tariff ? toRows(tariff.ranges) : [{ from: "1", to: "", rate: "" }]);
  const [scope, setScope] = useState<string[] | "all">(
    tariff && !tariff.all_locations ? tariff.locations : "all",
  );
  const [saved, setSaved] = useState(false);
  const save = useCommand<Record<string, unknown>, { tariff: Tariff }>(
    tariff ? `/c/${company.public_id}/tariffs/${tariff.id}` : `/c/${company.public_id}/tariffs`,
    tariff ? "PATCH" : "POST",
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    const body: Record<string, unknown> = { name, ranges: fromRows(rows), locations: scope };
    if (tariff) body.expected_version = tariff.version;
    else if (currency) body.currency = currency;
    const result = await save.run(body);
    if (result) {
      setSaved(true);
      if (!tariff) {
        setName("");
        setRows([{ from: "1", to: "", rate: "" }]);
      }
      await client.invalidateQueries({ queryKey: ["tariffs", company.public_id] });
      await client.invalidateQueries({ queryKey: ["tariff", company.public_id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
    }
  }

  return (
    <Card title={tariff ? t("tariffs.edit_title") : t("tariffs.create_title")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("tariffs.name")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("cash.currency")} hint={tariff ? t("tariffs.currency_fixed") : t("cash.currency_hint")}>
            <select value={currency} disabled={Boolean(tariff)} onChange={(e) => setCurrency(e.target.value)}>
              <option value="">—</option>
              {currencies().map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("tariffs.unit")}>
            <input value={t("tariffs.daily_from_issue")} disabled />
          </Field>
        </div>
        <h3>{t("tariffs.ranges")}</h3>
        <RangesEditor rows={rows} onChange={setRows} />
        <div className="small">
          {t("tariffs.availability")}:{" "}
          <label>
            <input type="checkbox" checked={scope === "all"} onChange={() => setScope(scope === "all" ? [] : "all")} />{" "}
            {t("rights.whole_company")}
          </label>
          {scope !== "all"
            ? (locations.data?.locations ?? []).map((location) => (
                <label key={location.id} style={{ marginLeft: 12 }}>
                  <input
                    type="checkbox"
                    checked={scope.includes(location.id)}
                    onChange={() =>
                      setScope(
                        scope.includes(location.id) ? scope.filter((x) => x !== location.id) : [...scope, location.id],
                      )
                    }
                  />{" "}
                  {location.name}
                </label>
              ))
            : null}
        </div>
        <ErrorMessage error={save.error} />
        {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
        <div className="actions">
          <button type="submit" disabled={!name.trim() || save.pending}>
            {tariff ? t("common.save") : t("tariffs.create")}
          </button>
        </div>
      </form>
    </Card>
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
  const tariff = useApi<{ tariff: Tariff }>(["tariff", company.public_id, id], `/c/${company.public_id}/tariffs/${id}`);
  const archive = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/tariffs/${id}/archive`);
  const restore = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/tariffs/${id}/restore`);
  if (tariff.isLoading) return <div className="page"><Loading /></div>;
  const data = tariff.data?.tariff;
  if (!data) return <div className="page"><ErrorMessage error={null} /></div>;

  const current = data;
  async function toggle() {
    const command = current.state === "active" ? archive : restore;
    if ((await command.run({ expected_version: current.version })) !== null) {
      await client.invalidateQueries({ queryKey: ["tariff", company.public_id] });
      await client.invalidateQueries({ queryKey: ["tariffs", company.public_id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
    }
  }

  return (
    <div className="page">
      <h1>
        {data.name}{" "}
        <span className={`badge ${data.state === "active" ? "ok" : ""}`}>{t(`tariffs.state_${data.state}`)}</span>
      </h1>
      {can("tariff.edit") ? <TariffForm key={data.version} tariff={data} /> : null}
      {can("tariff.archive") ? (
        <div className="actions" style={{ marginBottom: 16 }}>
          <button type="button" className="secondary" onClick={() => void toggle()}>
            {data.state === "active" ? t("tariffs.archive") : t("tariffs.restore")}
          </button>
          <ErrorMessage error={archive.error ?? restore.error} />
        </div>
      ) : null}
      <TariffVehicles tariff={data} />
    </div>
  );
}

function termsText(terms: VehicleTerms, t: TFunction) {
  const mileage =
    terms.mileage.mode === "unlimited"
      ? t("terms.mileage_unlimited")
      : t("terms.mileage_included", {
          km: Number(terms.mileage.included_per_unit),
          unit: terms.mileage_unit,
          price: Number(terms.mileage.overage_price),
          currency: terms.currency,
        });
  const deposit = terms.deposit.required
    ? t("terms.deposit_required", {
        amount: Number(terms.deposit.amount),
        currency: terms.currency,
        days: terms.deposit.hold_days ?? "—",
      })
    : t("terms.deposit_none");
  return `${mileage}; ${deposit}`;
}

function TariffVehicles({ tariff }: { tariff: Tariff }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const terms = useApi<{ vehicles: VehicleTerms[] }>(
    ["tariff-vehicles", company.public_id, tariff.id],
    `/c/${company.public_id}/tariffs/${tariff.id}/vehicles`,
  );
  const vehicles = useVehicles();
  const names = new Map((vehicles.data?.vehicles ?? []).map((v) => [v.id, v]));
  return (
    <Card title={t("tariffs.vehicles")}>
      {(terms.data?.vehicles ?? []).length === 0 ? <p className="muted">{t("tariffs.no_vehicles")}</p> : null}
      <table>
        <tbody>
          {(terms.data?.vehicles ?? []).map((item) => (
            <tr key={item.vehicle_card_id}>
              <td>
                <Link to={companyPath(company, `/vehicles/${item.vehicle_card_id}`)}>
                  {names.get(item.vehicle_card_id)?.name ?? item.vehicle_card_id}
                </Link>
              </td>
              <td className="small">{termsText(item, t)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {can("tariff.edit") ? <TermsForm tariff={tariff} vehicles={vehicles.data?.vehicles ?? []} /> : null}
    </Card>
  );
}

/** Both choices are explicit: unlimited or included mileage; no deposit or a deposit with an
 * amount and an optional hold term in days (§ 22.1.8, decisions 22.10.2026). */
function TermsForm({ tariff, vehicles }: { tariff: Tariff; vehicles: Vehicle[] }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const [vehicleId, setVehicleId] = useState("");
  const [mileage, setMileage] = useState<"" | "unlimited" | "included">("");
  const [included, setIncluded] = useState("");
  const [overage, setOverage] = useState("");
  const [deposit, setDeposit] = useState<"" | "none" | "required">("");
  const [amount, setAmount] = useState("");
  const [holdDays, setHoldDays] = useState("");
  const save = useCommand<Record<string, unknown>, unknown>(
    () => `/c/${company.public_id}/tariffs/${tariff.id}/vehicles/${vehicleId}`,
    "PUT",
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = {
      mileage:
        mileage === "included"
          ? { mode: "included", included_per_unit: included, overage_price: overage.replace(",", ".") }
          : { mode: mileage },
      deposit:
        deposit === "required"
          ? { required: true, amount: amount.replace(",", "."), hold_days: holdDays ? Number(holdDays) : null }
          : { required: false },
    };
    if ((await save.run(body)) !== null) {
      setVehicleId("");
      await client.invalidateQueries({ queryKey: ["tariff-vehicles", company.public_id, tariff.id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
    }
  }

  return (
    <form className="stack" onSubmit={submit} style={{ marginTop: 16 }}>
      <h3>{t("terms.assign_title")}</h3>
      <div className="row">
        <Field label={t("terms.vehicle")}>
          <select value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
            <option value="">—</option>
            {vehicles.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name} · {v.vehicle_id}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("terms.mileage")}>
          <select value={mileage} onChange={(e) => setMileage(e.target.value as typeof mileage)}>
            <option value="">{t("terms.choose")}</option>
            <option value="unlimited">{t("terms.mileage_unlimited")}</option>
            <option value="included">{t("terms.mileage_limit")}</option>
          </select>
        </Field>
        <Field label={t("terms.deposit")}>
          <select value={deposit} onChange={(e) => setDeposit(e.target.value as typeof deposit)}>
            <option value="">{t("terms.choose")}</option>
            <option value="none">{t("terms.deposit_none")}</option>
            <option value="required">{t("terms.deposit_yes")}</option>
          </select>
        </Field>
      </div>
      {mileage === "included" ? (
        <div className="row">
          <Field label={t("terms.included_per_day")}>
            <input value={included} inputMode="decimal" onChange={(e) => setIncluded(e.target.value)} />
          </Field>
          <Field label={t("terms.overage_price")}>
            <input value={overage} inputMode="decimal" onChange={(e) => setOverage(e.target.value)} />
          </Field>
        </div>
      ) : null}
      {deposit === "required" ? (
        <div className="row">
          <Field label={t("terms.deposit_amount")}>
            <input value={amount} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Field label={t("terms.hold_days")}>
            <input value={holdDays} inputMode="numeric" onChange={(e) => setHoldDays(e.target.value)} />
          </Field>
        </div>
      ) : null}
      <ErrorMessage error={save.error} />
      <div className="actions">
        <button type="submit" disabled={!vehicleId || !mileage || !deposit || save.pending}>
          {t("terms.assign")}
        </button>
      </div>
    </form>
  );
}
