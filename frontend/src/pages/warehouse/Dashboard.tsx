import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { Counterparty, Storage } from "../../api/types";
import { Card, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { useCategoryName, usePartCategories } from "./Categories";
import { useMoney, useQuantity } from "./Stock";

type Unit = { id?: string; name: string; unit: string };
type MoneyValue = { amount: string; incomplete: boolean };

type Board = {
  scope: { warehouses: string[]; partial: boolean };
  filters: { date_from: string; date_to: string; time_zone: string };
  currency: string;
  stock: { item: Unit; actual: string; reserved: string; available: string }[];
  value: { cost: MoneyValue; sale: MoneyValue };
  issued: { operation_id: string; number: number; item: Unit; vehicle: string; what: string; quantity: string }[];
  in_transit: { operation_id: string; number: number; state: string; from: string; to: string }[];
  below_minimum: { item: Unit; location_name: string; available: string; minimum: string }[];
  expiring: { lot: string; item: Unit; location_name: string; expiry_date: string; expired: boolean; quantity: string }[];
  expiry_horizon_days: number;
  period: Record<string, { count: number; cost?: MoneyValue }>;
  purchases: { purchase_id: string; number: number; item: Unit; ordered: string; received: string; expected: string }[];
};

const PERIOD_KEYS = ["receipts", "consume", "issue", "move", "write_off", "supplier_return", "sale"] as const;

/** The starter dashboard «Warehouse» (§ 21.6.1): only the warehouses the employee may see; with
 * partial access the scope is shown (§ 21.1.1). Filters: period, warehouse, category, item,
 * lot and counterparty. */
export function WarehouseDashboard() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const quantity = useQuantity();
  const money = useMoney();
  const [filters, setFilters] = useState({ from: "", to: "", context_id: "", part_category: "", item_id: "", lot_id: "", counterparty_id: "" });
  const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();
  const board = useApi<{ dashboard: Board }>(
    ["warehouse-dashboard", company.public_id, query],
    `/c/${company.public_id}/warehouse/dashboard${query ? `?${query}` : ""}`,
    can("warehouse.view"),
  );
  const storages = useApi<{ storages: Storage[] }>(["warehouse-storages", company.public_id], `/c/${company.public_id}/warehouse/storages`);
  const categories = usePartCategories();
  const categoryName = useCategoryName();
  const suppliers = useApi<{ counterparties: Counterparty[] }>(
    ["counterparties", company.public_id, "", false],
    `/c/${company.public_id}/counterparties?q=&state=active`,
    can("counterparty.view"),
  );
  if (!can("warehouse.view")) return null;
  const data = board.data?.dashboard;
  const set = (key: keyof typeof filters) => (e: { target: { value: string } }) => setFilters({ ...filters, [key]: e.target.value });
  const total = (value: MoneyValue) => `${money(value.amount, data?.currency ?? "")}${value.incomplete ? ` · ${t("dashboard.incomplete")}` : ""}`;

  return (
    <>
      <Card title={t("dashboard.title")}>
        <div className="row">
          <Field label={t("dashboard.from")}>
            <input type="date" value={filters.from || data?.filters.date_from || ""} onChange={set("from")} />
          </Field>
          <Field label={t("dashboard.to")}>
            <input type="date" value={filters.to || data?.filters.date_to || ""} onChange={set("to")} />
          </Field>
          <Field label={t("receipts.warehouse")}>
            <select value={filters.context_id} onChange={set("context_id")}>
              <option value="">{t("dashboard.all")}</option>
              {(storages.data?.storages ?? []).map((s) => (
                <option key={s.context_id} value={s.context_id}>
                  {s.location_name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("categories.category")}>
            <select value={filters.part_category} onChange={set("part_category")}>
              <option value="">{t("dashboard.all")}</option>
              {(categories.data?.categories ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {categoryName(c.names)}
                </option>
              ))}
            </select>
          </Field>
          {can("counterparty.view") ? (
            <Field label={t("receipts.supplier")}>
              <select value={filters.counterparty_id} onChange={set("counterparty_id")}>
                <option value="">{t("dashboard.all")}</option>
                {(suppliers.data?.counterparties ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
        </div>
        {data ? (
          <p className="small muted">
            {t("dashboard.scope", { warehouses: data.scope.warehouses.join(", ") || "—", zone: data.filters.time_zone })}
            {data.scope.partial ? ` · ${t("dashboard.partial")}` : ""}
          </p>
        ) : null}
      </Card>
      {board.isLoading || !data ? (
        <Loading />
      ) : (
        <>
          <Card title={t("dashboard.value")}>
            <p>
              {t("dashboard.cost")}: <strong>{total(data.value.cost)}</strong>
            </p>
            <p>
              {t("dashboard.sale")}: <strong>{total(data.value.sale)}</strong>
            </p>
          </Card>
          <Card title={t("dashboard.period")}>
            <table>
              <tbody>
                {PERIOD_KEYS.map((key) => (
                  <tr key={key}>
                    <td>{t(`dashboard.period_${key}`)}</td>
                    <td>{data.period[key]?.count ?? 0}</td>
                    <td>{data.period[key]?.cost ? total(data.period[key].cost) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card title={t("dashboard.stock")}>
            {data.stock.length === 0 ? <p className="muted">{t("stock.empty")}</p> : null}
            {data.stock.length ? (
              <table>
                <thead>
                  <tr>
                    <th>{t("warehouse.name")}</th>
                    <th>{t("dashboard.actual")}</th>
                    <th>{t("dashboard.reserved")}</th>
                    <th>{t("dashboard.available")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.stock.map((row) => (
                    <tr key={row.item.id}>
                      <td>
                        <Link to={companyPath(company, `/warehouse/items/${row.item.id}`)}>{row.item.name}</Link>
                      </td>
                      <td>{quantity(row.actual, row.item.unit)}</td>
                      <td>{quantity(row.reserved, row.item.unit)}</td>
                      <td>{quantity(row.available, row.item.unit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </Card>
          <Card title={t("dashboard.below_minimum")}>
            {data.below_minimum.length === 0 ? <p className="muted">{t("dashboard.none")}</p> : null}
            <ul>
              {data.below_minimum.map((row) => (
                <li key={`${row.item.id}-${row.location_name}`}>
                  {row.item.name} · {row.location_name}: {quantity(row.available, row.item.unit)} (
                  {t("prices.minimum_short", { value: quantity(row.minimum, row.item.unit) })})
                </li>
              ))}
            </ul>
          </Card>
          <Card title={t("dashboard.expiring", { days: data.expiry_horizon_days })}>
            {data.expiring.length === 0 ? <p className="muted">{t("dashboard.none")}</p> : null}
            <ul>
              {data.expiring.map((row) => (
                <li key={row.lot}>
                  {row.item.name} · {row.lot} · {row.location_name} · {row.expiry_date} · {quantity(row.quantity, row.item.unit)}{" "}
                  {row.expired ? <span className="badge warn">{t("stock.expired")}</span> : null}
                </li>
              ))}
            </ul>
          </Card>
          <Card title={t("dashboard.issued")}>
            {data.issued.length === 0 ? <p className="muted">{t("dashboard.none")}</p> : null}
            <ul>
              {data.issued.map((row) => (
                <li key={`${row.operation_id}-${row.what}`}>
                  <Link to={companyPath(company, `/warehouse/operations/${row.operation_id}`)}>№ {row.number}</Link> · {row.vehicle} ·{" "}
                  {row.item.name} · {row.what} · {quantity(row.quantity, row.item.unit)}
                </li>
              ))}
            </ul>
          </Card>
          <Card title={t("dashboard.in_transit")}>
            {data.in_transit.length === 0 ? <p className="muted">{t("dashboard.none")}</p> : null}
            <ul>
              {data.in_transit.map((row) => (
                <li key={row.operation_id}>
                  <Link to={companyPath(company, `/warehouse/operations/${row.operation_id}`)}>№ {row.number}</Link> · {row.from} → {row.to} ·{" "}
                  {t(`operations.state_${row.state}`)}
                </li>
              ))}
            </ul>
          </Card>
          <Card title={t("dashboard.purchases")}>
            {data.purchases.length === 0 ? <p className="muted">{t("dashboard.none")}</p> : null}
            {data.purchases.length ? (
              <table>
                <thead>
                  <tr>
                    <th>{t("receipts.number_short")}</th>
                    <th>{t("warehouse.name")}</th>
                    <th>{t("purchases.ordered_short")}</th>
                    <th>{t("purchases.received")}</th>
                    <th>{t("purchases.expected")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.purchases.map((row) => (
                    <tr key={`${row.purchase_id}-${row.item.id}`}>
                      <td>
                        <Link to={companyPath(company, `/warehouse/purchases/${row.purchase_id}`)}>№ {row.number}</Link>
                      </td>
                      <td>{row.item.name}</td>
                      <td>{quantity(row.ordered, row.item.unit)}</td>
                      <td>{quantity(row.received, row.item.unit)}</td>
                      <td>{quantity(row.expected, row.item.unit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </Card>
        </>
      )}
    </>
  );
}
