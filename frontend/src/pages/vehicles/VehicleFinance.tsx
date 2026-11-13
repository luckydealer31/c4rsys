/** Money of one vehicle (customer's decision of 13.11.2026): the cost of the acquisition, what
 * the company spent on the vehicle, what the vehicle brought and the result by currency. The
 * numbers are those of the report «Payback of own vehicles». */
import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { Vehicle } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, IconButton, Modal } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { currencies, formatMoment } from "../../time";

type Line = {
  id: string;
  number: number;
  at: string | null;
  amount: string;
  currency: string;
  article: string;
  article_code: string;
  note: string;
  order_id: string | null;
  order_number: number | null;
};
type Finance = {
  available: boolean;
  acquisition: { cost: string; currency: string } | null;
  expenses?: Line[];
  incomes?: Line[];
  totals?: { currency: string; income: string; expense: string; result: string }[];
};

function useAmount() {
  const { i18n } = useTranslation();
  return (value: string, currency: string) =>
    `${Number(value).toLocaleString(i18n.language, { minimumFractionDigits: 0, maximumFractionDigits: 2 })} ${currency}`;
}

function AcquisitionForm({ vehicle, onDone }: { vehicle: Vehicle; onDone: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const [cost, setCost] = useState(vehicle.acquisition?.cost ? String(Number(vehicle.acquisition.cost)) : "");
  const [currency, setCurrency] = useState(vehicle.acquisition?.currency ?? company.main_currency ?? currencies()[0] ?? "");
  const save = useCommand<Record<string, unknown>, { vehicle: Vehicle }>(
    `/c/${company.public_id}/vehicles/${vehicle.id}/acquisition`,
    "PUT",
  );
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await save.run({ cost: cost || null, currency: cost ? currency : null, expected_version: vehicle.version });
    if (result === null) return;
    await client.invalidateQueries({ queryKey: ["vehicle", company.public_id, vehicle.id] });
    onDone();
  }
  return (
    <form className="stack" onSubmit={submit}>
      <div className="row">
        <Field label={t("vehicles.acquisition_cost")}>
          <input autoFocus value={cost} inputMode="decimal" onChange={(e) => setCost(e.target.value.replace(/[^\d.]/g, ""))} />
        </Field>
        <Field label={t("vehicles.acquisition_currency")}>
          <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
            {currencies().map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <ErrorMessage error={save.error} />
      <div className="actions">
        <button type="submit" disabled={save.pending || Boolean(cost && !currency)}>
          {t("common.save")}
        </button>
        <button type="button" className="secondary" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

function Lines({ items, income }: { items: Line[]; income: boolean }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const amount = useAmount();
  if (items.length === 0) return <p className="muted">{t(income ? "vehicles.finance_incomes_none" : "vehicles.finance_expenses_none")}</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>{t("vehicles.finance_when")}</th>
          <th>{t("vehicles.finance_what")}</th>
          <th className="num">{t("vehicles.finance_amount")}</th>
        </tr>
      </thead>
      <tbody>
        {items.map((line, index) => {
          const article = line.article || (line.article_code ? t(`finance_articles.code_${line.article_code}`, { defaultValue: "" }) : "");
          return (
            <tr key={`${line.id}-${line.order_id ?? ""}-${index}`}>
              <td className="small">{line.at ? formatMoment(line.at, company.timezone ?? "UTC", i18n.language) : "—"}</td>
              <td>
                {income && line.order_id ? (
                  <Link to={companyPath(company, `/orders/${line.order_id}`)}>
                    {t("vehicles.finance_order", { number: line.order_number })}
                  </Link>
                ) : (
                  [article, line.note].filter(Boolean).join(" · ") || t("vehicles.finance_payout", { number: line.number })
                )}
              </td>
              <td className="num">{amount(line.amount, line.currency)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function VehicleFinance({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const amount = useAmount();
  const [editing, setEditing] = useState(false);
  const finance = useApi<Finance>(
    ["vehicle", company.public_id, vehicle.id, "finance"],
    `/c/${company.public_id}/vehicles/${vehicle.id}/finance`,
  );
  const data = finance.data;
  const own = vehicle.relation === "own";
  const editable = own && can("vehicle.edit") && vehicle.state === "active";
  return (
    <>
      {own ? (
        <Card title={t("vehicles.acquisition")}>
          <p>
            {t("vehicles.acquisition_cost")}:{" "}
            <strong>
              {vehicle.acquisition?.cost && vehicle.acquisition.currency
                ? amount(vehicle.acquisition.cost, vehicle.acquisition.currency)
                : t("vehicles.acquisition_not_set")}
            </strong>{" "}
            {editable ? <IconButton icon="edit" label={`${t("common.edit")}: ${t("vehicles.acquisition_cost")}`} onClick={() => setEditing(true)} /> : null}
          </p>
          <p className="muted small">{t("vehicles.acquisition_hint")}</p>
        </Card>
      ) : null}
      {finance.isLoading ? <p className="muted">{t("common.loading")}</p> : null}
      {data && !data.available ? (
        <Card title={t("vehicles.finance_result")}>
          <p className="muted">{t("vehicles.finance_hidden")}</p>
        </Card>
      ) : null}
      {data?.available ? (
        <>
          <Card title={t("vehicles.finance_result")}>
            {(data.totals ?? []).length === 0 ? <p className="muted">{t("vehicles.finance_empty")}</p> : null}
            {(data.totals ?? []).length ? (
              <table>
                <thead>
                  <tr>
                    <th>{t("vehicles.acquisition_currency")}</th>
                    <th className="num">{t("vehicles.finance_incomes")}</th>
                    <th className="num">{t("vehicles.finance_expenses")}</th>
                    <th className="num">{t("vehicles.finance_total")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.totals ?? []).map((row) => (
                    <tr key={row.currency}>
                      <td>{row.currency}</td>
                      <td className="num">{amount(row.income, row.currency)}</td>
                      <td className="num">{amount(row.expense, row.currency)}</td>
                      <td className="num">
                        <strong className={Number(row.result) < 0 ? "error-text" : undefined}>
                          {Number(row.result) > 0 ? "+" : ""}
                          {amount(row.result, row.currency)}
                        </strong>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </Card>
          <Card title={t("vehicles.finance_expenses")}>
            <Lines items={data.expenses ?? []} income={false} />
          </Card>
          <Card title={t("vehicles.finance_incomes")}>
            <Lines items={data.incomes ?? []} income />
          </Card>
        </>
      ) : null}
      {editing ? (
        <Modal title={t("vehicles.acquisition_cost")} onClose={() => setEditing(false)}>
          <AcquisitionForm vehicle={vehicle} onDone={() => setEditing(false)} />
        </Modal>
      ) : null}
    </>
  );
}
