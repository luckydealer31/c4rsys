import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { Client, JournalEntry, Order } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { CashReceipt, usePayments } from "../finance/CashReceipt";
import { useCashAccounts } from "../finance/FinancePage";
import { employeeName, JournalTable, useEmployees } from "../settings/EmployeesPage";

export function useOrders() {
  const company = useCompany();
  const can = useCan();
  return useApi<{ orders: Order[] }>(["orders", company.public_id], `/c/${company.public_id}/orders`, can("order.view"));
}

function useClientNames(enabled: boolean) {
  const company = useCompany();
  const clients = useApi<{ clients: Client[] }>(
    ["clients", company.public_id, ""],
    `/c/${company.public_id}/clients?q=`,
    enabled,
  );
  return new Map((clients.data?.clients ?? []).map((c) => [c.id, clientName(c)]));
}

/** «Charged / paid / to pay or overpaid» per currency (§ 12.2.4). */
export function OrderAmountsText({ order }: { order: Order }) {
  const { t } = useTranslation();
  const entries = Object.entries(order.amounts);
  if (!entries.length) return <>—</>;
  return (
    <>
      {entries.map(([currency, a]) => (
        <div key={currency}>
          {t("orders.charged")} {a.charged} · {t("orders.paid")} {a.paid} ·{" "}
          {Number(a.overpaid) > 0 ? `${t("orders.overpaid")} ${a.overpaid}` : `${t("orders.to_pay")} ${a.to_pay}`}{" "}
          {currency}
        </div>
      ))}
    </>
  );
}

/** Orders of the company (§ 12.2.3). */
export function OrdersPage() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const orders = useOrders();
  const names = useClientNames(can("client.list"));
  const zone = company.timezone ?? "UTC";
  return (
    <div className="page">
      <h1>{t("nav.orders")}</h1>
      <Card>
        {orders.isLoading ? <Loading /> : null}
        <ErrorMessage error={orders.error as ApiError} />
        {orders.data?.orders.length === 0 ? <p className="muted">{t("orders.none")}</p> : null}
        {orders.data?.orders.length ? (
          <table>
            <thead>
              <tr>
                <th>{t("orders.number")}</th>
                <th>{t("orders.client")}</th>
                <th>{t("orders.created")}</th>
                <th>{t("orders.amounts")}</th>
                <th>{t("orders.state")}</th>
              </tr>
            </thead>
            <tbody>
              {orders.data.orders.map((order) => (
                <tr key={order.id}>
                  <td>
                    <Link to={companyPath(company, `/orders/${order.id}`)}>№ {order.number}</Link>
                  </td>
                  <td>{names.get(order.client_id ?? "") ?? "—"}</td>
                  <td>{formatMoment(order.created_at, zone, i18n.language)}</td>
                  <td className="small">
                    <OrderAmountsText order={order} />
                  </td>
                  <td>{t(`orders.state_${order.state}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Card>
    </div>
  );
}

/** Order card (STAGE1_SCREENS): number, client, responsible, lines (a rental is its own line),
 * amounts, payments, state; «Close» with the list of unmet conditions; cash receipt; history. */
export function OrderPage() {
  const { t, i18n } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const key = ["order", company.public_id, id];
  const order = useApi<{ order: Order }>(key, `/c/${company.public_id}/orders/${id}`);
  const history = useApi<{ history: JournalEntry[] }>(
    ["order-history", company.public_id, id],
    `/c/${company.public_id}/orders/${id}/history`,
  );
  const payments = usePayments();
  const accounts = useCashAccounts();
  const employees = useEmployees();
  const data = order.data?.order;
  const client = useApi<{ client: Client }>(
    ["client", company.public_id, data?.client_id ?? ""],
    `/c/${company.public_id}/clients/${data?.client_id ?? ""}`,
    Boolean(data?.client_id) && (can("client.find") || can("client.list")),
  );
  const close = useCommand<Record<string, unknown>, { order: Order }>(`/c/${company.public_id}/orders/${id}/close`);
  const [showHistory, setShowHistory] = useState(false);
  if (order.isLoading) {
    return (
      <div className="page">
        <Loading />
      </div>
    );
  }
  if (!data) {
    return (
      <div className="page">
        <ErrorMessage error={order.error as ApiError} />
      </div>
    );
  }
  const current = data;
  const zone = company.timezone ?? "UTC";
  const when = (value: string | null) => formatMoment(value, zone, i18n.language);
  const accountName = new Map((accounts.data?.cash_accounts ?? []).map((a) => [a.id, a.name]));
  const responsible = employees.data?.employees.find((e) => e.id === current.responsible_employment_id);
  const own = (payments.data?.payments ?? []).filter((p) => p.allocations.some((a) => a.order_id === current.id));
  const open = current.state === "open" || current.state === "ready_to_close";

  async function refresh() {
    for (const k of [key, ["order-history", company.public_id, id], ["orders", company.public_id], ["payments", company.public_id]]) {
      await queryClient.invalidateQueries({ queryKey: k });
    }
  }

  return (
    <div className="page">
      <h1>
        {t("orders.order")} № {current.number} · {t(`orders.state_${current.state}`)}
      </h1>
      <Card>
        <dl className="facts">
          <dt>{t("orders.client")}</dt>
          <dd>
            {client.data ? (
              <Link to={companyPath(company, `/clients/${client.data.client.id}`)}>{clientName(client.data.client)}</Link>
            ) : (
              "—"
            )}
          </dd>
          <dt>{t("orders.responsible")}</dt>
          <dd>{responsible ? employeeName(responsible, t) : "—"}</dd>
          <dt>{t("orders.created")}</dt>
          <dd>{when(current.created_at)}</dd>
          {current.closed_at ? (
            <>
              <dt>{t("orders.closed")}</dt>
              <dd>{when(current.closed_at)}</dd>
            </>
          ) : null}
          <dt>{t("orders.amounts")}</dt>
          <dd>
            <OrderAmountsText order={current} />
          </dd>
        </dl>
      </Card>

      <Card title={t("orders.lines")}>
        <table>
          <tbody>
            {current.operations.map((op) => (
              <tr key={op.id}>
                <td>
                  {op.type === "rental" ? (
                    <Link to={companyPath(company, `/rentals/${op.id}`)}>{t("orders.line_rental")}</Link>
                  ) : (
                    op.type
                  )}
                </td>
                <td>{op.type === "rental" ? t(`rentals.state_${op.state}`) : op.state}</td>
                <td>{op.finished ? t("orders.finished") : t("orders.not_finished")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title={t("orders.payments")}>
        {own.length === 0 ? <p className="muted">{t("orders.no_payments")}</p> : null}
        {own.length ? (
          <table>
            <tbody>
              {own.map((p) => (
                <tr key={p.id}>
                  <td>№ {p.number}</td>
                  <td>{when(p.posted_at)}</td>
                  <td>{accountName.get(p.cash_account_id) ?? "—"}</td>
                  <td>
                    {p.allocations.find((a) => a.order_id === current.id)?.amount} {p.currency}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Card>

      {open && current.client_id && can("finance.payment.receive") ? (
        <CashReceipt
          clientId={current.client_id}
          order={current}
          onDone={refresh}
        />
      ) : null}

      {open && can("order.close") ? (
        <Card title={t("orders.closing")}>
          {current.closing_conditions.length ? (
            <ul>
              {current.closing_conditions.map((c) => (
                <li key={`${c.id}-${c.condition}`}>{t(`orders.condition_${c.condition}`, { operation: t(`orders.operation_${c.operation}`) })}</li>
              ))}
            </ul>
          ) : (
            <p>{t("orders.ready_to_close")}</p>
          )}
          <ErrorMessage error={close.error} />
          <div className="actions">
            <button
              type="button"
              disabled={close.pending || current.closing_conditions.length > 0}
              onClick={() => void close.run({ expected_version: current.version }).then((r) => (r ? refresh() : undefined))}
            >
              {t("orders.close")}
            </button>
          </div>
        </Card>
      ) : null}

      <div className="actions">
        <button type="button" className="secondary" onClick={() => setShowHistory(!showHistory)}>
          {t("rentals.history")}
        </button>
      </div>
      {showHistory ? (
        <Card title={t("rentals.history")}>
          {history.isLoading ? <Loading /> : null}
          <ErrorMessage error={history.error as ApiError} />
          <JournalTable entries={history.data?.history ?? []} timeZone={zone} />
        </Card>
      ) : null}
    </div>
  );
}
