import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { useApi } from "../../api/queries";
import type { ServiceOperation, WarehouseItem } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Info, Loading, Modal } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { ClientForm } from "../clients/ClientsPage";
import { ClientFinder } from "../rentals/IssuePage";
import { useServiceOperations } from "../service/hooks";

/** The units marked in a stock card: of this item, in this warehouse. */
export type Marked = { item: WarehouseItem; contextId: string; ids: string[] };

type OpenOrder = { id: string; number: number; client: string; phone: string; created_at: string; vehicles: string[] };

/** Goods leave the warehouse into an order (customer's decision of 22.11.2026): the marked units
 * go into a new order of a client or into an open one. In the order they are reserved; they are
 * sold when the order is closed and are simply taken out of it before that. */
export function ToOrderModal({ marked, onClose }: { marked: Marked; onClose: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [query, setQuery] = useState("");
  const text = query.trim();
  const orders = useApi<{ orders: OpenOrder[] }>(
    ["warehouse-sale-orders", company.public_id, text],
    `/c/${company.public_id}/warehouse/sales/open-orders?q=${encodeURIComponent(text)}`,
  );
  // «New order» is a button of its own (customer's remark of 22.11.2026): the search above the
  // list looks only among the open orders; a new order takes a client of the base or a new one.
  const [mode, setMode] = useState<"orders" | "new" | "client">("orders");
  const start = useCommand<{ client_id: string }, { order: { id: string } }>(`/c/${company.public_id}/warehouse/sales/start`);
  const add = useCommand<{ order_id: string } & Record<string, unknown>, unknown>(
    (body) => `/c/${company.public_id}/warehouse/orders/${body.order_id}/sale-lines`,
  );
  const busy = start.pending || add.pending;

  async function into(orderId: string) {
    const done = await add.run({
      order_id: orderId,
      context_id: marked.contextId,
      item_id: marked.item.id,
      asset_ids: marked.ids,
    });
    if (done === null) return;
    await client.invalidateQueries({ queryKey: ["warehouse-item-stock", company.public_id, marked.item.id] });
    await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
    navigate(companyPath(company, `/orders/${orderId}`));
  }

  async function fresh(clientId: string) {
    const result = await start.run({ client_id: clientId });
    if (result) await into(result.order.id);
  }

  return (
    <Modal title={mode === "orders" ? t("send.to_order") : t("send.new_order")} onClose={onClose} roomy>
      <p className="small muted">
        {t("stock.marked", { count: marked.ids.length })} · {marked.item.name} <Info text={t("send.order_hint")} />
      </p>
      {mode === "orders" ? (
        <>
          {can("order.create") ? (
            <p>
              <button type="button" className="secondary" onClick={() => setMode("new")}>
                {t("send.new_order")}
              </button>
            </p>
          ) : null}
          <h3>{t("send.open_orders")}</h3>
          <input
            aria-label={t("send.order_search")}
            placeholder={t("send.order_search")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {orders.isLoading ? <Loading /> : null}
          {orders.data?.orders.length === 0 ? (
            <p className="muted">{text === "" ? t("sales.no_open_orders") : t("send.nothing_found")}</p>
          ) : null}
          <ul className="pick-list">
            {(orders.data?.orders ?? []).map((o) => (
              <li key={o.id}>
                <button type="button" className="link" disabled={busy} onClick={() => void into(o.id)}>
                  № {o.number} · {o.client}
                </button>
                <span className="small muted">
                  {[o.phone, new Date(o.created_at).toLocaleDateString(), ...o.vehicles].filter(Boolean).join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <>
          <p>
            <button type="button" className="link" onClick={() => setMode(mode === "client" ? "new" : "orders")}>
              ← {mode === "client" ? t("send.new_order") : t("send.open_orders")}
            </button>
          </p>
          {mode === "new" ? (
            // Room for the list of the found clients, which drops under the field.
            <div className="finder-room">
              <ClientFinder onPick={(c) => void fresh(c.id)} onCreate={() => setMode("client")} />
            </div>
          ) : (
            <ClientForm onCreated={(c) => void fresh(c.id)} />
          )}
        </>
      )}
      <ErrorMessage error={start.error ?? add.error} />
    </Modal>
  );
}

/** Goods leave the warehouse into a service that already exists (customer's decision of
 * 22.11.2026). Who may change the works and issue from the warehouse puts the units to a work at
 * once; otherwise they go into the order of the service, and a work is chosen later there. */
export function ToServiceModal({ marked, locationId, onClose }: { marked: Marked; locationId: string; onClose: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const client = useQueryClient();
  const operations = useServiceOperations(`active=1&location_id=${locationId}`);
  const [chosen, setChosen] = useState<ServiceOperation | null>(null);
  const toWork = can("service.create") && can("warehouse.issue");
  const toOrder = can("warehouse.sell");
  const material = useCommand<{ job_id: string } & Record<string, unknown>, unknown>(
    (body) => `/c/${company.public_id}/service/jobs/${body.job_id}/materials`,
  );
  const line = useCommand<{ order_id: string } & Record<string, unknown>, unknown>(
    (body) => `/c/${company.public_id}/warehouse/orders/${body.order_id}/sale-lines`,
  );
  const busy = material.pending || line.pending;
  const take = { context_id: marked.contextId, item_id: marked.item.id, asset_ids: marked.ids };

  async function done(result: unknown, operation: ServiceOperation) {
    if (result === null) return;
    await client.invalidateQueries({ queryKey: ["warehouse-item-stock", company.public_id, marked.item.id] });
    await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
    await client.invalidateQueries({ queryKey: ["service-operation", company.public_id, operation.id] });
    navigate(companyPath(company, `/service/operations/${operation.id}`));
  }

  const jobs = (chosen?.jobs ?? []).filter((j) => j.state === "planned" || j.state === "in_progress");
  return (
    <Modal title={t("send.to_service")} onClose={onClose} roomy>
      <p className="small muted">
        {t("stock.marked", { count: marked.ids.length })} · {marked.item.name} <Info text={t("send.service_hint")} />
      </p>
      {chosen === null ? (
        <>
          {operations.isLoading ? <Loading /> : null}
          {operations.data?.operations.length === 0 ? <p className="muted">{t("send.no_services")}</p> : null}
          <ul className="pick-list">
            {(operations.data?.operations ?? []).map((o) => (
              <li key={o.id}>
                <button type="button" className="link" onClick={() => setChosen(o)}>
                  № {o.number} · {o.vehicle_name ?? ""}
                </button>
                <span className="small muted">
                  {[t("send.order_number", { number: o.order_number }), o.reason].filter(Boolean).join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <>
          <p>
            <strong>
              № {chosen.number} · {chosen.vehicle_name ?? ""}
            </strong>{" "}
            <button type="button" className="link" onClick={() => setChosen(null)}>
              {t("send.other_service")}
            </button>
          </p>
          {toWork ? (
            <>
              <h3>{t("send.to_work")}</h3>
              {jobs.length === 0 ? <p className="muted">{t("send.no_works")}</p> : null}
              <ul className="pick-list">
                {jobs.map((j) => (
                  <li key={j.id}>
                    <button
                      type="button"
                      className="link"
                      disabled={busy}
                      onClick={async () =>
                        done(await material.run({ job_id: j.id, ...take, expected_version: j.version }), chosen)
                      }
                    >
                      {j.name}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {toOrder ? (
            chosen.is_internal ? (
              <p className="small muted">{t("send.internal_later")}</p>
            ) : (
              <p>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={async () => done(await line.run({ order_id: chosen.order_id, ...take }), chosen)}
                >
                  {t("send.without_work")}
                </button>{" "}
                <Info text={t("send.without_work_hint")} />
              </p>
            )
          ) : null}
        </>
      )}
      <ErrorMessage error={material.error ?? line.error} />
    </Modal>
  );
}
