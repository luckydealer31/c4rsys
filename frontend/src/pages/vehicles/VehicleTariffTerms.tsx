/** The tariffs of a vehicle in the settings of its card (customer's remark of 24.11.2026): every
 * tariff the vehicle is in, with what the tariff holds for this vehicle only — its price in
 * every range, the mileage included and the price over it, the deposit and the days it is held.
 * They are changed here, tariff by tariff; the tariff itself — its name, unit, ranges and other
 * vehicles — is changed on its own page. */
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { Tariff, VehicleTerms } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, IconButton } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { rangesText } from "../tariffs/TariffsPage";
import { rowBody, rowOf, rowProblem, type VehicleRow } from "../tariffs/VehiclePrices";

export function VehicleTariffTerms({ vehicleId }: { vehicleId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const tariffs = useApi<{ tariffs: Tariff[] }>(
    ["tariffs", company.public_id],
    `/c/${company.public_id}/tariffs`,
    can("tariff.view"),
  );
  if (!can("tariff.view")) return null;
  return (
    <>
      {(tariffs.data?.tariffs ?? []).map((tariff) => (
        <TermsOfTariff key={tariff.id} tariff={tariff} vehicleId={vehicleId} />
      ))}
      <p className="muted small">
        {t("vehicles.tariffs_hint")}{" "}
        <Link to={companyPath(company, "/rentals/tariffs")}>{t("settings.tariffs")}</Link>
      </p>
    </>
  );
}

function TermsOfTariff({ tariff, vehicleId }: { tariff: Tariff; vehicleId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const terms = useApi<{ vehicles: VehicleTerms[] }>(
    ["tariff-vehicles", company.public_id, tariff.id],
    `/c/${company.public_id}/tariffs/${tariff.id}/vehicles`,
  );
  const save = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/tariffs/${tariff.id}/vehicles/${vehicleId}`,
    "PUT",
  );
  const [row, setRow] = useState<VehicleRow | null>(null);
  const mine = terms.data?.vehicles.find((v) => v.vehicle_card_id === vehicleId);
  // Only the tariffs of this vehicle are shown.
  if (!mine) return null;
  const ranges = tariff.ranges.map((r) => ({ from: r.from, to: r.to }));
  const name = (index: number) => {
    const range = ranges[index];
    return range ? rangesText([{ ...range, rate: null }], t, tariff.unit) : "";
  };
  const shown = rowOf(mine, ranges);
  const editable = can("tariff.edit") && tariff.state === "active";
  const problem = row ? rowProblem(row, ranges) : null;
  const change = (changes: Partial<VehicleRow>) => setRow({ ...(row ?? shown), ...changes });
  const money = (value: string) => (value ? `${value} ${mine.currency}` : "—");
  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>
          <Link to={companyPath(company, `/rentals/tariffs/${tariff.id}`)}>{tariff.name}</Link>
        </h2>
        {tariff.state === "archived" ? <span className="badge">{t("tariffs.archived")}</span> : null}
        {editable && row === null ? (
          <IconButton icon="edit" label={`${t("common.edit")}: ${tariff.name}`} onClick={() => setRow(shown)} />
        ) : null}
      </div>
      {row === null ? (
        <dl className="facts">
          {ranges.map((_, index) => (
            <div key={index} style={{ display: "contents" }}>
              <dt>{name(index)}</dt>
              <dd>{money(shown.rates[index] ?? "")}</dd>
            </div>
          ))}
          <dt>{t("terms.included_short")}</dt>
          <dd>{shown.included ? `${shown.included} ${mine.mileage_unit}` : t("terms.mileage_unlimited")}</dd>
          {shown.included ? (
            <>
              <dt>{t("terms.overage_short")}</dt>
              <dd>{money(shown.overage)}</dd>
            </>
          ) : null}
          <dt>{t("terms.deposit")}</dt>
          <dd>{shown.deposit ? money(shown.deposit) : t("terms.deposit_none")}</dd>
          {shown.hold ? (
            <>
              <dt>{t("terms.hold_short")}</dt>
              <dd>{shown.hold}</dd>
            </>
          ) : null}
        </dl>
      ) : (
        <>
          <div className="row">
            {ranges.map((_, index) => (
              <Field key={index} label={`${name(index)}, ${mine.currency}`}>
                <input
                  inputMode="decimal"
                  value={row.rates[index] ?? ""}
                  onChange={(e) => change({ rates: { ...row.rates, [index]: e.target.value } })}
                />
              </Field>
            ))}
          </div>
          <div className="row">
            <Field label={`${t("terms.included_short")}, ${mine.mileage_unit}`}>
              <input inputMode="decimal" value={row.included} onChange={(e) => change({ included: e.target.value })} />
            </Field>
            <Field label={`${t("terms.overage_short")}, ${mine.currency}`}>
              <input inputMode="decimal" value={row.overage} onChange={(e) => change({ overage: e.target.value })} />
            </Field>
            <Field label={`${t("terms.deposit")}, ${mine.currency}`}>
              <input inputMode="decimal" value={row.deposit} onChange={(e) => change({ deposit: e.target.value })} />
            </Field>
            <Field label={t("terms.hold_short")}>
              <input inputMode="numeric" value={row.hold} onChange={(e) => change({ hold: e.target.value })} />
            </Field>
          </div>
          {problem ? <p className="error-text small">{t(problem)}</p> : null}
          <ErrorMessage error={save.error} />
          <div className="actions">
            <button
              type="button"
              disabled={Boolean(problem) || save.pending}
              onClick={async () => {
                if ((await save.run(rowBody(row, ranges))) === null) return;
                await client.invalidateQueries({ queryKey: ["tariff-vehicles", company.public_id, tariff.id] });
                setRow(null);
              }}
            >
              {t("common.save")}
            </button>
            <button type="button" className="secondary" onClick={() => setRow(null)}>
              {t("common.cancel")}
            </button>
          </div>
        </>
      )}
    </Card>
  );
}
