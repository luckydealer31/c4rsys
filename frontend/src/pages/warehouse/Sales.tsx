import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { useApi } from "../../api/queries";
import type { Client, ItemBrief, Order, Sources, Storage, Vehicle, WarehouseItem } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { clientName } from "../clients/ClientsPage";
import { useVehicles } from "../vehicles/VehiclesPage";
import { ItemPicker } from "./Receipts";
import { useMoney, useQuantity } from "./Stock";

export type SaleLine = {
  id: string;
  state: "reserved" | "sold" | "removed" | "cancelled";
  context: { id: string; location_name: string };
  item: ItemBrief | null;
  vehicle: { id: string; name: string } | null;
  asset: string | null;
  lot: string | null;
  packaging_quantity: string | null;
  opened: boolean;
  quantity: string;
  unit_price: string;
  amount: string;
  currency: string;
  created_at: string;
  sold_at: string | null;
  cancel_reason: string;
};

/** A sale from the warehouse starts with a new order for a client or an open order of a client
 * (decision of 25.10.2026); goods are then added on the order's page. */
export function SaleStartPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const clients = useApi<{ clients: Client[] }>(
    ["clients", company.public_id, query],
    `/c/${company.public_id}/clients?q=${encodeURIComponent(query)}`,
  );
  const openOrders = useApi<{ orders: { id: string; number: number; client: string }[] }>(
    ["warehouse-sale-orders", company.public_id],
    `/c/${company.public_id}/warehouse/sales/open-orders`,
  );
  const start = useCommand<{ client_id: string }, { order: { id: string } }>(`/c/${company.public_id}/warehouse/sales/start`);
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/warehouse")}>← {t("nav.warehouse")}</Link>
      </p>
      <h1>{t("sales.start_title")}</h1>
      <Card title={t("sales.new_order")}>
        <Field label={t("clients.search")}>
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
        </Field>
        {clients.isLoading ? <Loading /> : null}
        <ul>
          {(clients.data?.clients ?? []).slice(0, 20).map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="link"
                disabled={start.pending}
                onClick={async () => {
                  const result = await start.run({ client_id: c.id });
                  if (result) navigate(companyPath(company, `/orders/${result.order.id}`));
                }}
              >
                {clientName(c)}
              </button>{" "}
              <span className="small muted">{c.contacts.map((x) => x.value).join(", ")}</span>
            </li>
          ))}
        </ul>
        <p className="muted small">{t("sales.new_order_hint")}</p>
        <ErrorMessage error={start.error} />
      </Card>
      <Card title={t("sales.open_orders")}>
        {openOrders.data?.orders.length === 0 ? <p className="muted">{t("sales.no_open_orders")}</p> : null}
        <ul>
          {(openOrders.data?.orders ?? []).map((o) => (
            <li key={o.id}>
              <Link to={companyPath(company, `/orders/${o.id}`)}>
                {t("orders.order")} № {o.number}
              </Link>{" "}
              · {o.client}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

/** Goods of an order (§ 11.9): reserved while the order is open, sold at closing. A reserved
 * line is removed without consumption; a sold one is cancelled after the order is reopened. */
export function SaleGoods({ order, onChange }: { order: Order; onChange: () => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const quantity = useQuantity();
  const money = useMoney();
  const base = `/c/${company.public_id}/warehouse/orders/${order.id}/sale-lines`;
  const lines = useApi<{ lines: SaleLine[] }>(["sale-lines", company.public_id, order.id], base);
  const remove = useCommand<{ id: string }, unknown>((b) => `${base}/${b.id}`, "DELETE");
  const cancel = useCommand<{ id: string; reason: string }, unknown>((b) => `${base}/${b.id}/cancel`);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const open = order.state === "open" || order.state === "ready_to_close";
  const all = lines.data?.lines ?? [];
  if (!all.length && !(open && can("warehouse.sell") && order.client_id)) return null;

  async function refresh(result: unknown) {
    if (result === null) return;
    await client.invalidateQueries({ queryKey: ["sale-lines", company.public_id, order.id] });
    await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
    await onChange();
  }

  return (
    <Card title={t("sales.goods")}>
      {all.length ? (
        <table>
          <thead>
            <tr>
              <th>{t("warehouse.name")}</th>
              <th>{t("operations.what")}</th>
              <th>{t("operations.quantity")}</th>
              <th>{t("sales.price")}</th>
              <th>{t("receipts.total")}</th>
              <th>{t("receipts.state")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {all.map((line) => (
              <tr key={line.id} className={line.state === "removed" || line.state === "cancelled" ? "muted" : ""}>
                <td>{line.vehicle?.name ?? line.item?.name}</td>
                <td className="small">
                  {line.context.location_name}
                  {line.asset ? ` · ${line.asset}` : ""}
                  {line.lot ? ` · ${line.lot}` : ""}
                  {line.packaging_quantity && line.item ? ` · ${t("operations.pack_of", { size: quantity(line.packaging_quantity, line.item.unit) })}` : ""}
                  {line.opened ? ` · ${t("operations.opened")}` : ""}
                </td>
                <td>{line.item ? quantity(line.quantity, line.item.unit) : "1"}</td>
                <td>{money(line.unit_price, line.currency)}</td>
                <td>{money(line.amount, line.currency)}</td>
                <td>
                  {t(`sales.state_${line.state}`)}
                  {line.cancel_reason ? <div className="small muted">{line.cancel_reason}</div> : null}
                </td>
                <td>
                  {line.state === "reserved" && open && can("warehouse.sell") ? (
                    <button type="button" className="link" onClick={async () => void refresh(await remove.run({ id: line.id }))}>
                      {t("receipts.remove_line")}
                    </button>
                  ) : null}
                  {line.state === "sold" && open && can("warehouse.sale.correct") && !line.vehicle ? (
                    <button type="button" className="link" onClick={() => setCancelling(line.id)}>
                      {t("sales.cancel")}
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {cancelling ? (
        <div className="line-card">
          <Field label={t("sales.cancel_reason")} hint={t("sales.cancel_hint")}>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <div className="actions">
            <button
              type="button"
              disabled={!reason.trim() || cancel.pending}
              onClick={async () => {
                const result = await cancel.run({ id: cancelling, reason });
                if (result !== null) {
                  setCancelling(null);
                  setReason("");
                }
                await refresh(result);
              }}
            >
              {t("sales.cancel")}
            </button>
            <button type="button" className="secondary" onClick={() => setCancelling(null)}>
              {t("common.cancel")}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorMessage error={remove.error ?? cancel.error} />
      {open && can("warehouse.sell") && order.client_id ? <SaleAdder orderId={order.id} onAdded={refresh} /> : null}
      <p className="muted small">{t("sales.hint")}</p>
    </Card>
  );
}

function SaleAdder({ orderId, onAdded }: { orderId: string; onAdded: (result: unknown) => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  return (
    <StockAdder
      path={`/c/${company.public_id}/warehouse/orders/${orderId}/sale-lines`}
      title={t("sales.add")}
      button={t("sales.add_button")}
      withVehicles
      onAdded={onAdded}
    />
  );
}

/** Picks what to take from a warehouse — units of an item or a quantity of a lot from one form —
 * and posts it with `context_id` to `path` (a sale line, a material of a service work). */
export function StockAdder({
  path,
  title,
  button,
  extra,
  withVehicles = false,
  onAdded,
}: {
  path: string;
  title: string;
  button: string;
  extra?: Record<string, unknown>;
  withVehicles?: boolean;
  onAdded: (result: unknown) => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const quantity = useQuantity();
  const storages = useApi<{ storages: Storage[] }>(
    ["warehouse-storages", company.public_id],
    `/c/${company.public_id}/warehouse/storages`,
  );
  const warehouses = (storages.data?.storages ?? []).filter((s) => s.state === "enabled" && !s.location_deactivated);
  const vehicles = useVehicles();
  const [contextId, setContextId] = useState("");
  const [item, setItem] = useState<ItemBrief | null>(null);
  const [form, setForm] = useState("");
  const [lotId, setLotId] = useState("");
  const [amount, setAmount] = useState("");
  const [assets, setAssets] = useState<string[]>([]);
  const [confirmExpired, setConfirmExpired] = useState(false);
  const add = useCommand<Record<string, unknown>, unknown>(path);
  const context = contextId || (warehouses.length === 1 ? (warehouses[0]?.context_id ?? "") : "");
  const sources = useApi<Sources>(
    ["warehouse-sources", company.public_id, item?.id ?? "", context],
    `/c/${company.public_id}/warehouse/items/${item?.id ?? ""}/sources?context_id=${context}`,
    Boolean(item && context),
  );
  const stored = (vehicles.data?.vehicles ?? []).filter((v: Vehicle) => v.storage?.context_id === context);
  const lots = sources.data?.lots ?? [];
  const lot = lots.find((l) => l.id === lotId) ?? lots[0];
  const options = lot
    ? [
        ...lot.forms.map((f) => ({
          value: f.packaging_id ? `pack:${f.packaging_id}` : "loose",
          label: f.packaging_id
            ? t("operations.form_packs", { packs: Number(f.quantity) / Number(f.packaging_quantity), size: quantity(f.packaging_quantity ?? 0, item?.unit ?? "liter") })
            : t("sales.form_loose", { quantity: quantity(f.quantity, item?.unit ?? "liter") }),
        })),
        ...lot.opened_packages.map((o, i) => ({
          value: `opened:${o.id}`,
          label: t("operations.form_opened", { n: i + 1, remaining: quantity(o.remaining, item?.unit ?? "liter") }),
        })),
      ]
    : [];
  const chosen = options.some((o) => o.value === form) ? form : (options[0]?.value ?? "");

  function reset() {
    setItem(null);
    setForm("");
    setLotId("");
    setAmount("");
    setAssets([]);
    setConfirmExpired(false);
  }

  async function submit(body: Record<string, unknown>) {
    const result = await add.run({ context_id: context, ...extra, ...body });
    if (result !== null) reset();
    await onAdded(result);
  }

  return (
    <div className="line-card">
      <h3>{title}</h3>
      <Field label={t("receipts.warehouse")}>
        <select value={context} onChange={(e) => { setContextId(e.target.value); reset(); }}>
          <option value="">—</option>
          {warehouses.map((w) => (
            <option key={w.context_id} value={w.context_id}>
              {w.location_name}
            </option>
          ))}
        </select>
      </Field>
      {withVehicles && context && stored.length ? (
        <p className="small">
          {t("sales.vehicles")}:{" "}
          {stored.map((v) => (
            <button key={v.id} type="button" className="link" style={{ marginRight: 8 }} onClick={() => void submit({ vehicle_id: v.id })}>
              {v.name}
            </button>
          ))}
        </p>
      ) : null}
      {context && !item ? <ItemPicker onPick={(picked: WarehouseItem) => setItem(picked)} /> : null}
      {item ? (
        <>
          <p>
            <strong>{item.name}</strong>{" "}
            <button type="button" className="link" onClick={reset}>
              {t("receipts.remove_line")}
            </button>
          </p>
          {item.accounting === "individual" ? (
            (sources.data?.assets ?? []).length ? (
              <div className="stack">
                {(sources.data?.assets ?? []).map((a) => (
                  <label key={a.id}>
                    <input
                      type="checkbox"
                      checked={assets.includes(a.id)}
                      onChange={(e) => setAssets(e.target.checked ? [...assets, a.id] : assets.filter((x) => x !== a.id))}
                    />{" "}
                    {a.public_id} {a.serial_number}
                  </label>
                ))}
                <button type="button" disabled={!assets.length || add.pending} onClick={() => void submit({ item_id: item.id, asset_ids: assets })}>
                  {button}
                </button>
              </div>
            ) : (
              <p className="muted">{t("operations.nothing_available")}</p>
            )
          ) : lots.length ? (
            <>
              <div className="row">
                <Field label={t("receipts.lot")}>
                  <select value={lot?.id ?? ""} onChange={(e) => { setLotId(e.target.value); setForm(""); }}>
                    {lots.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.public_id}
                        {l.expiry_date ? ` · ${l.expiry_date}` : ""}
                        {l.expired ? ` · ${t("stock.expired")}` : ""}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label={t("operations.form")} hint={t("sales.form_hint")}>
                  <select value={chosen} onChange={(e) => setForm(e.target.value)}>
                    {options.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label={t("operations.quantity_unit", { unit: t(`warehouse.unit_short_${item.unit}`) })}>
                  <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
                </Field>
              </div>
              {lot?.expired ? (
                <label className="alert warn" style={{ display: "block" }}>
                  <input type="checkbox" checked={confirmExpired} onChange={(e) => setConfirmExpired(e.target.checked)} />{" "}
                  {t("operations.confirm_expired", { lot: lot.public_id })}
                </label>
              ) : null}
              <button
                type="button"
                disabled={!amount || add.pending}
                onClick={() => {
                  const [kind, formId] = chosen.split(":");
                  void submit({
                    item_id: item.id,
                    lot_id: lot?.id,
                    packaging_id: kind === "pack" ? formId : null,
                    opened_package_id: kind === "opened" ? formId : null,
                    quantity: amount,
                    confirm_expired: confirmExpired,
                  });
                }}
              >
                {button}
              </button>
            </>
          ) : (
            <p className="muted">{t("operations.nothing_available")}</p>
          )}
        </>
      ) : null}
      <ErrorMessage error={add.error} />
    </div>
  );
}

/** Cancelling the closing of an order with the separate right and a reason (§ 12.2.3). */
export function ReopenOrder({ order, onDone }: { order: Order; onDone: () => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [reason, setReason] = useState("");
  const reopen = useCommand<{ expected_version: number; reason: string }, unknown>(`/c/${company.public_id}/orders/${order.id}/reopen`);
  if (order.state !== "closed" || !can("order.reopen")) return null;
  return (
    <Card title={t("sales.reopen_title")}>
      <Field label={t("sales.reopen_reason")} hint={t("sales.reopen_hint")}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <ErrorMessage error={reopen.error} />
      <div className="actions">
        <button
          type="button"
          className="secondary"
          disabled={!reason.trim() || reopen.pending}
          onClick={async () => {
            if ((await reopen.run({ expected_version: order.version, reason })) !== null) {
              setReason("");
              await onDone();
            }
          }}
        >
          {t("sales.reopen")}
        </button>
      </div>
    </Card>
  );
}
