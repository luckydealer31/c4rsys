import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";

import { useApi } from "../../api/queries";
import type { CashAccount, Client } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { currencies, formatMoment } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { useOrders } from "../orders/OrdersPage";
import { ClientFinder } from "../rentals/IssuePage";
import { Articles, useArticleName, useArticles } from "./Articles";
import { CashReceipt, usePayments } from "./CashReceipt";
import { AccountRecords, MoneyRecordForm } from "./MoneyRecords";

export function useCashAccounts() {
  const company = useCompany();
  return useApi<{ cash_accounts: CashAccount[] }>(
    ["cash-accounts", company.public_id],
    `/c/${company.public_id}/cash-accounts`,
  );
}

type Tab = "money" | "accounts" | "articles";
const TABS: Tab[] = ["money", "accounts", "articles"];

/** Finance (ТЗ гл. 17): money records — client payments and manual receipts and payouts; cash
 * desks and accounts with their balances (§ 17.2.1, § 17.2.5, § 22.1.7); income and expense
 * articles (§ 17.2.8). */
export function FinancePage() {
  const { t } = useTranslation();
  const can = useCan();
  const accounts = useCashAccounts();
  const [params, setParams] = useSearchParams();
  const tab: Tab = TABS.find((key) => key === params.get("tab")) ?? "money";
  return (
    <div className="page">
      <h1>{t("nav.finance")}</h1>
      <div className="tabs" role="tablist">
        {TABS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "active" : ""}
            onClick={() => setParams(key === "money" ? {} : { tab: key })}
          >
            {t(`money.tab_${key}`)}
          </button>
        ))}
      </div>
      {tab === "money" ? (
        <>
          {can("finance.payment.receive") ? <Receipt /> : null}
          <MoneyRecordForm accounts={accounts.data?.cash_accounts ?? []} />
          <PaymentsList />
        </>
      ) : null}
      {tab === "accounts" ? <CashAccountsEditor /> : null}
      {tab === "articles" ? <Articles /> : null}
    </div>
  );
}

/** Receipt outside an order card: the client, then one of the client's open orders or none. */
function Receipt() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const company = useCompany();
  const orders = useOrders();
  const [client, setClient] = useState<Client | null>(null);
  const [orderId, setOrderId] = useState("");
  const open = (orders.data?.orders ?? []).filter(
    (o) => o.client_id === client?.id && (o.state === "open" || o.state === "ready_to_close"),
  );
  const order = open.find((o) => o.id === orderId) ?? null;

  async function refresh() {
    for (const key of [["payments", company.public_id], ["orders", company.public_id]]) {
      await queryClient.invalidateQueries({ queryKey: key });
    }
  }

  return (
    <>
      <Card title={t("payments.receive_title")}>
        {client ? (
          <>
            <p>
              <strong>{clientName(client)}</strong>{" "}
              <button type="button" className="secondary" onClick={() => setClient(null)}>
                {t("rentals.change_client")}
              </button>
            </p>
            <Field label={t("payments.order")}>
              <select value={orderId} onChange={(e) => setOrderId(e.target.value)}>
                <option value="">{t("payments.without_order")}</option>
                {open.map((o) => (
                  <option key={o.id} value={o.id}>
                    № {o.number}
                  </option>
                ))}
              </select>
            </Field>
          </>
        ) : (
          <ClientFinder onPick={setClient} />
        )}
      </Card>
      {client ? <CashReceipt key={`${client.id}-${orderId}`} clientId={client.id} order={order} onDone={refresh} /> : null}
    </>
  );
}

function PaymentsList() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const payments = usePayments();
  const accounts = useCashAccounts();
  const orders = useOrders();
  const accountName = new Map((accounts.data?.cash_accounts ?? []).map((a) => [a.id, a.name]));
  const orderNumber = new Map((orders.data?.orders ?? []).map((o) => [o.id, o.number]));
  const articles = useArticles();
  const nameOf = useArticleName();
  const articleName = new Map((articles.data?.articles ?? []).map((a) => [a.id, nameOf(a)]));
  const items = payments.data?.payments ?? [];
  return (
    <Card title={t("payments.title")}>
      {payments.isLoading ? <Loading /> : null}
      {!payments.isLoading && items.length === 0 ? <p className="muted">{t("payments.none")}</p> : null}
      {items.length ? (
        <table>
          <thead>
            <tr>
              <th>{t("payments.number")}</th>
              <th>{t("payments.when")}</th>
              <th>{t("payments.account")}</th>
              <th>{t("payments.amount", { currency: "" })}</th>
              <th>{t("money.purpose")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.id}>
                <td>№ {p.number}</td>
                <td>{formatMoment(p.posted_at, company.timezone ?? "UTC", i18n.language)}</td>
                <td>{accountName.get(p.cash_account_id) ?? "—"}</td>
                <td>
                  {p.direction === "out" ? "−" : ""}
                  {p.amount} {p.currency}
                </td>
                <td>
                  {p.kind !== "payment"
                    ? [t(`money.kind_${p.kind}`), p.article_id ? articleName.get(p.article_id) : null, p.note]
                        .filter(Boolean)
                        .join(" · ")
                    : p.allocations.length
                    ? p.allocations.map((a) => (
                        <div key={a.order_id}>
                          <Link to={companyPath(company, `/orders/${a.order_id}`)}>
                            № {orderNumber.get(a.order_id) ?? "…"}
                          </Link>{" "}
                          · {a.amount}
                        </div>
                      ))
                    : t("payments.unallocated")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}

export function CashAccountsEditor() {
  const { t } = useTranslation();
  const can = useCan();
  const accounts = useCashAccounts();
  const [opened, setOpened] = useState<string | null>(null);
  const list = accounts.data?.cash_accounts ?? [];
  const withBalance = list.some((a) => a.balance !== undefined);
  const openedAccount = list.find((a) => a.id === opened);
  return (
    <>
      <Card title={t("cash.title")}>
        {accounts.isLoading ? <Loading /> : null}
        {accounts.data?.cash_accounts.length === 0 ? (
          <p className="muted">{t("cash.none")}</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>{t("cash.name")}</th>
                <th>{t("cash.type")}</th>
                <th>{t("cash.currency")}</th>
                {withBalance ? <th>{t("money.balance")}</th> : null}
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((account) => (
                <AccountRow
                  key={account.id}
                  account={account}
                  editable={can("finance.cash_account.manage")}
                  withBalance={withBalance}
                  onOpen={account.balance !== undefined ? () => setOpened(account.id) : undefined}
                />
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {openedAccount ? (
        <AccountRecords
          key={openedAccount.id}
          account={openedAccount}
          onClose={() => setOpened(null)}
        />
      ) : null}
      {can("finance.cash_account.manage") ? <CreateAccount /> : null}
    </>
  );
}

function useRefresh() {
  const client = useQueryClient();
  const company = useCompany();
  return async () => {
    await client.invalidateQueries({ queryKey: ["cash-accounts", company.public_id] });
    // The setup wizard shows readiness from the same data (§ 22.1.12).
    await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
  };
}

function AccountRow({
  account,
  editable,
  withBalance,
  onOpen,
}: {
  account: CashAccount;
  editable: boolean;
  withBalance: boolean;
  onOpen?: () => void;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [name, setName] = useState(account.name);
  const rename = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/cash-accounts/${account.id}`,
    "PATCH",
  );
  const archive = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/cash-accounts/${account.id}/archive`,
  );

  return (
    <tr>
      <td>
        {editable && account.state === "active" ? (
          <input value={name} onChange={(e) => setName(e.target.value)} />
        ) : (
          account.name
        )}
        <ErrorMessage error={rename.error ?? archive.error} />
      </td>
      <td>{t(`cash.type_${account.type}`)}</td>
      <td>{account.currency}</td>
      {withBalance ? <td>{account.balance ?? "—"}</td> : null}
      <td>
        {onOpen ? (
          <button type="button" className="secondary" onClick={onOpen}>
            {t("money.open_records")}
          </button>
        ) : null}
        {account.state === "archived" ? (
          <span className="badge">{t("cash.archived")}</span>
        ) : editable ? (
          <div className="actions" style={{ marginTop: 0 }}>
            <button
              type="button"
              className="secondary"
              disabled={name === account.name || !name.trim()}
              onClick={async () => {
                if ((await rename.run({ name, expected_version: account.version })) !== null)
                  await refresh();
              }}
            >
              {t("common.save")}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={async () => {
                if ((await archive.run({ expected_version: account.version })) !== null)
                  await refresh();
              }}
            >
              {t("cash.archive")}
            </button>
          </div>
        ) : null}
      </td>
    </tr>
  );
}

function CreateAccount() {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [form, setForm] = useState({
    name: "",
    type: "cash",
    currency: company.main_currency ?? "",
  });
  const create = useCommand<Record<string, string>, unknown>(`/c/${company.public_id}/cash-accounts`);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, string> = { name: form.name, type: form.type, currency: form.currency };
    if ((await create.run(body)) !== null) {
      setForm({ ...form, name: "" });
      await refresh();
    }
  }

  return (
    <Card title={t("cash.create_title")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("cash.name")}>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label={t("cash.type")}>
            <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
              <option value="cash">{t("cash.type_cash")}</option>
              <option value="bank">{t("cash.type_bank")}</option>
            </select>
          </Field>
          <Field label={t("cash.currency")} hint={t("cash.currency_hint")}>
            <select
              value={form.currency}
              onChange={(e) => setForm({ ...form, currency: e.target.value })}
            >
              <option value="">—</option>
              {currencies().map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={!form.name.trim() || create.pending}>
            {t("cash.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}
