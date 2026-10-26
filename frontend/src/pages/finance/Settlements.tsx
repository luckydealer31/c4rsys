import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { CashAccount, Client, Counterparty, Payment } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { currencies, formatMoment } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { useOrders } from "../orders/OrdersPage";
import { ClientFinder } from "../rentals/IssuePage";
import { useArticleName, useArticles } from "./Articles";
import { useCashAccounts } from "./FinancePage";
import { RateField, effectiveRate, useRateSuggestion } from "./Rates";

type Money = Record<string, string>;

type Lists = {
  clients: { id: string; name: string; balance: Money; available: Money; unpaid: Money }[];
  counterparties: { id: string; name: string; payable: Money; incomplete: boolean }[];
};

type Entry = {
  id: string;
  kind: "opening" | "return_credit" | "cancellation";
  debtor: "party" | "company";
  amount: string;
  currency: string;
  as_of: string;
  return_result: string | null;
  cancelled: boolean;
  left: string | null;
  note: string;
};

type OffsetRow = {
  id: string;
  order_id: string | null;
  opening_id: string | null;
  amount: string;
  currency: string;
  settled_amount: string | null;
  settled_currency: string | null;
  state: "active" | "cancelled";
  replaces_id: string | null;
  comment: string;
  created_at: string;
};

type ClientSettlement = {
  balance: Money;
  available: Money;
  unpaid: Money;
  openings: Entry[];
  offsets: OffsetRow[];
};

type SupplierSettlement = {
  payable: Money;
  incomplete: boolean;
  entries: Entry[];
  payments: Payment[];
  receipts: {
    id: string;
    number: number;
    purchase_id: string | null;
    currency: string;
    amount: string;
    incomplete: boolean;
    to_pay: string;
  }[];
  returns: {
    id: string;
    number: number;
    receipt_id: string;
    currency: string;
    amount: string;
    incomplete: boolean;
    settled: boolean;
  }[];
};

const moneyText = (money: Money) =>
  Object.entries(money)
    .map(([currency, amount]) => `${amount} ${currency}`)
    .join(" · ") || "0";

function useRefreshSettlements() {
  const client = useQueryClient();
  const company = useCompany();
  return async () => {
    for (const key of ["settlements", "client-settlement", "supplier-settlement", "cash-accounts", "account-records", "payments", "orders"]) {
      await client.invalidateQueries({ queryKey: [key, company.public_id] });
    }
  };
}

/** Settlements with parties (§ 17.2.3): who owes the company and whom the company owes, with
 * the client's and the supplier's cards. */
export function Settlements() {
  const { t } = useTranslation();
  const can = useCan();
  const company = useCompany();
  const lists = useApi<Lists>(["settlements", company.public_id], `/c/${company.public_id}/settlements`);
  const [client, setClient] = useState<{ id: string; name: string } | null>(null);
  const [supplier, setSupplier] = useState<{ id: string; name: string } | null>(null);

  return (
    <>
      <Card title={t("settlements.clients_title")}>
        {lists.isLoading ? <Loading /> : null}
        {lists.data && lists.data.clients.length === 0 ? <p className="muted">{t("settlements.none")}</p> : null}
        {lists.data?.clients.length ? (
          <table>
            <thead>
              <tr>
                <th>{t("settlements.client")}</th>
                <th>{t("settlements.balance")}</th>
                <th>{t("settlements.available")}</th>
                <th>{t("settlements.unpaid")}</th>
              </tr>
            </thead>
            <tbody>
              {lists.data.clients.map((row) => (
                <tr key={row.id}>
                  <td>
                    <button type="button" className="link" onClick={() => setClient(row)}>
                      {row.name}
                    </button>
                  </td>
                  <td>{moneyText(row.balance)}</td>
                  <td>{moneyText(row.available)}</td>
                  <td>{moneyText(row.unpaid)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        <p className="muted">{t("settlements.find_client")}</p>
        <ClientFinder onPick={(c: Client) => setClient({ id: c.id, name: clientName(c) })} />
      </Card>
      {client ? <ClientCard key={client.id} clientId={client.id} name={client.name} onClose={() => setClient(null)} /> : null}

      <Card title={t("settlements.suppliers_title")}>
        {lists.data && lists.data.counterparties.length === 0 ? <p className="muted">{t("settlements.none")}</p> : null}
        {lists.data?.counterparties.length ? (
          <table>
            <thead>
              <tr>
                <th>{t("settlements.counterparty")}</th>
                <th>{t("settlements.payable")}</th>
              </tr>
            </thead>
            <tbody>
              {lists.data.counterparties.map((row) => (
                <tr key={row.id}>
                  <td>
                    <button type="button" className="link" onClick={() => setSupplier(row)}>
                      {row.name}
                    </button>
                  </td>
                  <td>
                    {moneyText(row.payable)}
                    {row.incomplete ? ` · ${t("settlements.incomplete")}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        <SupplierPicker onPick={setSupplier} />
      </Card>
      {supplier ? <SupplierCard key={supplier.id} counterpartyId={supplier.id} name={supplier.name} onClose={() => setSupplier(null)} /> : null}

      {can("finance.settlement.manage") ? <OpeningForm /> : null}
    </>
  );
}

function useCounterparties() {
  const company = useCompany();
  return useApi<{ counterparties: Counterparty[] }>(
    ["counterparties", company.public_id, "", false],
    `/c/${company.public_id}/counterparties?q=&state=active`,
  );
}

function SupplierPicker({ onPick }: { onPick: (value: { id: string; name: string }) => void }) {
  const { t } = useTranslation();
  const list = useCounterparties();
  return (
    <Field label={t("settlements.open_counterparty")}>
      <select
        value=""
        onChange={(e) => {
          const found = list.data?.counterparties.find((c) => c.id === e.target.value);
          if (found) onPick({ id: found.id, name: found.name });
        }}
      >
        <option value="">—</option>
        {(list.data?.counterparties ?? []).map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
    </Field>
  );
}

// --- Client ----------------------------------------------------------------------------------

function ClientCard({ clientId, name, onClose }: { clientId: string; name: string; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const can = useCan();
  const company = useCompany();
  const data = useApi<ClientSettlement>(
    ["client-settlement", company.public_id, clientId],
    `/c/${company.public_id}/clients/${clientId}/settlement`,
  );
  const orders = useOrders();
  const orderNumber = new Map((orders.data?.orders ?? []).map((o) => [o.id, o.number]));
  const zone = company.timezone ?? "UTC";
  const info = data.data;

  return (
    <Card title={name}>
      <div className="actions" style={{ marginTop: 0 }}>
        <button type="button" className="secondary" onClick={onClose}>
          {t("money.close")}
        </button>
      </div>
      {data.isLoading ? <Loading /> : null}
      {info ? (
        <>
          <dl className="facts">
            <dt>{t("settlements.available")}</dt>
            <dd>{moneyText(info.available)}</dd>
            <dt>{t("settlements.unpaid")}</dt>
            <dd>{moneyText(info.unpaid)}</dd>
            <dt>{t("settlements.balance")}</dt>
            <dd>
              <strong>{moneyText(info.balance)}</strong>
            </dd>
          </dl>
          <p className="muted">{t("settlements.balance_hint")}</p>
          {info.openings.length ? (
            <>
              <h3>{t("settlements.openings")}</h3>
              <EntriesTable entries={info.openings} />
            </>
          ) : null}
          {info.offsets.length ? (
            <>
              <h3>{t("settlements.offsets")}</h3>
              <table>
                <tbody>
                  {info.offsets.map((o) => (
                    <OffsetLine
                      key={o.id}
                      offset={o}
                      target={
                        o.order_id
                          ? `${t("payments.order")} № ${orderNumber.get(o.order_id) ?? "…"}`
                          : t("settlements.opening_debt")
                      }
                      when={formatMoment(o.created_at, zone, i18n.language)}
                    />
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
          {can("finance.offset") ? <OffsetForm clientId={clientId} info={info} /> : null}
          {can("finance.payout") ? <RefundForm clientId={clientId} info={info} /> : null}
        </>
      ) : null}
    </Card>
  );
}

function EntriesTable({ entries }: { entries: Entry[] }) {
  const { t } = useTranslation();
  const can = useCan();
  return (
    <table>
      <tbody>
        {entries.map((e) => (
          <EntryLine key={e.id} entry={e} cancellable={can("finance.settlement.manage") && !e.cancelled} label={t(`settlements.entry_${e.kind}_${e.debtor}`)} />
        ))}
      </tbody>
    </table>
  );
}

function EntryLine({ entry, cancellable, label }: { entry: Entry; cancellable: boolean; label: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefreshSettlements();
  const [note, setNote] = useState("");
  const [asking, setAsking] = useState(false);
  const cancel = useCommand<Record<string, string>, unknown>(`/c/${company.public_id}/settlement-entries/${entry.id}/cancel`);
  return (
    <tr>
      <td>
        {label}
        {entry.return_result ? ` · ${t(`settlements.result_${entry.return_result}`)}` : ""}
        {entry.note ? ` · ${entry.note}` : ""}
      </td>
      <td>
        {entry.amount} {entry.currency}
        {entry.left !== null && entry.debtor === "party" && !entry.cancelled ? ` · ${t("settlements.left", { amount: entry.left })}` : ""}
      </td>
      <td>{entry.as_of}</td>
      <td>
        {entry.cancelled ? <span className="badge">{t("settlements.cancelled")}</span> : null}
        {cancellable && !asking ? (
          <button type="button" className="secondary" onClick={() => setAsking(true)}>
            {t("settlements.cancel")}
          </button>
        ) : null}
        {asking ? (
          <div className="row">
            <input placeholder={t("settlements.reason")} value={note} onChange={(e) => setNote(e.target.value)} />
            <button
              type="button"
              disabled={!note.trim() || cancel.pending}
              onClick={async () => {
                if ((await cancel.run({ note })) !== null) {
                  setAsking(false);
                  await refresh();
                }
              }}
            >
              {t("settlements.confirm_cancel")}
            </button>
          </div>
        ) : null}
        <ErrorMessage error={cancel.error} />
      </td>
    </tr>
  );
}

/** A set-off; its cancellation that leaves a closed order with a debt shows the debt and asks
 * for an explicit confirmation (§ 17.2.16). */
function OffsetLine({ offset, target, when }: { offset: OffsetRow; target: string; when: string }) {
  const { t } = useTranslation();
  const can = useCan();
  const company = useCompany();
  const refresh = useRefreshSettlements();
  const cancel = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/offsets/${offset.id}/cancel`);
  const debt = cancel.error?.code === "DEBT_WILL_APPEAR" ? (cancel.error.params as { debt: string; currency: string }) : null;

  async function run(confirmed?: string) {
    if ((await cancel.run({ confirmed_debt: confirmed ?? null })) !== null) await refresh();
  }

  return (
    <tr>
      <td>{when}</td>
      <td>{target}</td>
      <td>
        {offset.amount} {offset.currency}
        {offset.settled_amount ? ` → ${offset.settled_amount} ${offset.settled_currency}` : ""}
        {offset.replaces_id ? ` · ${t("settlements.corrected")}` : ""}
      </td>
      <td>
        {offset.state === "cancelled" ? (
          <span className="badge">{t("settlements.cancelled")}</span>
        ) : can("finance.offset") ? (
          debt ? (
            <div className="stack">
              <p className="alert error">{t("settlements.debt_will_appear", debt)}</p>
              <button type="button" onClick={() => run(debt.debt)}>
                {t("settlements.confirm_debt")}
              </button>
            </div>
          ) : (
            <button type="button" className="secondary" onClick={() => run()}>
              {t("settlements.cancel")}
            </button>
          )
        ) : null}
        {cancel.error && !debt ? <ErrorMessage error={cancel.error} /> : null}
      </td>
    </tr>
  );
}

function OffsetForm({ clientId, info }: { clientId: string; info: ClientSettlement }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefreshSettlements();
  const orders = useOrders();
  const funds = Object.entries(info.available).filter(([, v]) => Number(v) > 0);
  const debts = (orders.data?.orders ?? []).filter(
    (o) => o.client_id === clientId && o.state !== "cancelled" && Object.values(o.amounts).some((a) => Number(a.to_pay) > 0),
  );
  const openings = info.openings.filter((e) => e.debtor === "party" && !e.cancelled && Number(e.left) > 0);
  const [target, setTarget] = useState("");
  const [currency, setCurrency] = useState("");
  const [amount, setAmount] = useState("");
  const [rate, setRate] = useState("");
  const create = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/offsets`);
  const fundsCurrency = currency || funds[0]?.[0] || "";
  const order = debts.find((o) => `order:${o.id}` === target);
  const opening = openings.find((e) => `opening:${e.id}` === target);
  const targetCurrency = order ? Object.keys(order.amounts)[0] : opening?.currency;
  const exchange = Boolean(targetCurrency && fundsCurrency && targetCurrency !== fundsCurrency);
  const suggestion = useRateSuggestion(exchange ? fundsCurrency : undefined, targetCurrency);
  const applied = exchange ? effectiveRate(suggestion.data, rate) : "1";
  if (!funds.length || (!debts.length && !openings.length)) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = {
      client_id: clientId,
      amount,
      currency: fundsCurrency,
      order_id: order?.id ?? null,
      opening_id: opening?.id ?? null,
      rate: exchange && rate ? rate : null,
    };
    if ((await create.run(body)) !== null) {
      setAmount("");
      setRate("");
      await refresh();
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <h3>{t("settlements.offset_title")}</h3>
      <p className="muted">{t("settlements.offset_hint")}</p>
      <div className="row">
        <Field label={t("settlements.offset_target")}>
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">—</option>
            {debts.map((o) => (
              <option key={o.id} value={`order:${o.id}`}>
                № {o.number} ·{" "}
                {Object.entries(o.amounts)
                  .map(([c, a]) => `${a.to_pay} ${c}`)
                  .join(", ")}
              </option>
            ))}
            {openings.map((e) => (
              <option key={e.id} value={`opening:${e.id}`}>
                {t("settlements.opening_debt")} · {e.left} {e.currency}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("settlements.from_funds")}>
          <select value={fundsCurrency} onChange={(e) => setCurrency(e.target.value)}>
            {funds.map(([c, v]) => (
              <option key={c} value={c}>
                {v} {c}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("payments.amount", { currency: fundsCurrency })}>
          <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(",", "."))} />
        </Field>
      </div>
      {exchange && targetCurrency ? <RateField from={fundsCurrency} to={targetCurrency} manual={rate} onManual={setRate} /> : null}
      <ErrorMessage error={create.error} />
      <div className="actions">
        <button type="submit" disabled={!target || !amount || !applied || create.pending}>
          {t("settlements.offset_submit")}
        </button>
      </div>
    </form>
  );
}

/** Available funds paid back to the client, possibly from an account in another currency
 * (§ 17.2.17): before posting the payout, the amount taken from the funds and the rest are shown. */
function RefundForm({ clientId, info }: { clientId: string; info: ClientSettlement }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefreshSettlements();
  const accounts = (useCashAccounts().data?.cash_accounts ?? []).filter((a) => a.state === "active");
  const funds = Object.entries(info.available).filter(([, v]) => Number(v) > 0);
  const [accountId, setAccountId] = useState("");
  const [currency, setCurrency] = useState("");
  const [amount, setAmount] = useState("");
  const [rate, setRate] = useState("");
  const refund = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/refunds`);
  const fundsCurrency = currency || funds[0]?.[0] || "";
  const account = accounts.find((a) => a.id === accountId) ?? accounts.find((a) => a.currency === fundsCurrency) ?? accounts[0];
  const exchange = Boolean(account && fundsCurrency && account.currency !== fundsCurrency);
  const suggestion = useRateSuggestion(exchange ? fundsCurrency : undefined, account?.currency);
  const applied = exchange ? effectiveRate(suggestion.data, rate) : "1";
  if (!funds.length || !accounts.length) return null;
  const available = Number(info.available[fundsCurrency] ?? 0);
  const payout = applied && amount ? (Number(amount) * Number(applied)).toFixed(2) : "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!account) return;
    const body = {
      cash_account_id: account.id,
      client_id: clientId,
      funds_amount: amount,
      funds_currency: fundsCurrency,
      rate: exchange && rate ? rate : null,
    };
    if ((await refund.run(body)) !== null) {
      setAmount("");
      setRate("");
      await refresh();
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <h3>{t("settlements.refund_title")}</h3>
      <div className="row">
        <Field label={t("settlements.from_funds")}>
          <select value={fundsCurrency} onChange={(e) => setCurrency(e.target.value)}>
            {funds.map(([c, v]) => (
              <option key={c} value={c}>
                {v} {c}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("payments.amount", { currency: fundsCurrency })}>
          <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(",", "."))} />
        </Field>
        <Field label={t("money.account")}>
          <select value={account?.id ?? ""} onChange={(e) => setAccountId(e.target.value)}>
            {accounts.map((a: CashAccount) => (
              <option key={a.id} value={a.id}>
                {a.name} · {a.currency}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {exchange && account ? <RateField from={fundsCurrency} to={account.currency} manual={rate} onManual={setRate} /> : null}
      {amount && payout ? (
        <p>
          {t("settlements.refund_preview", {
            payout,
            account_currency: account?.currency ?? "",
            taken: amount,
            funds_currency: fundsCurrency,
            rest: (available - Number(amount)).toFixed(2),
          })}
        </p>
      ) : null}
      <ErrorMessage error={refund.error} />
      <div className="actions">
        <button type="submit" disabled={!amount || !applied || refund.pending}>
          {t("settlements.refund_submit")}
        </button>
      </div>
    </form>
  );
}

// --- Supplier ----------------------------------------------------------------------------------

function SupplierCard({ counterpartyId, name, onClose }: { counterpartyId: string; name: string; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const can = useCan();
  const company = useCompany();
  const data = useApi<SupplierSettlement>(
    ["supplier-settlement", company.public_id, counterpartyId],
    `/c/${company.public_id}/counterparties/${counterpartyId}/settlement`,
  );
  const info = data.data;
  const zone = company.timezone ?? "UTC";
  return (
    <Card title={name}>
      <div className="actions" style={{ marginTop: 0 }}>
        <button type="button" className="secondary" onClick={onClose}>
          {t("money.close")}
        </button>
      </div>
      {data.isLoading ? <Loading /> : null}
      {info ? (
        <>
          <p>
            {t("settlements.payable")}: <strong>{moneyText(info.payable)}</strong>
            {info.incomplete ? ` · ${t("settlements.incomplete")}` : ""}
          </p>
          <p className="muted">{t("settlements.payable_hint")}</p>
          {info.receipts.length ? (
            <>
              <h3>{t("settlements.receipts")}</h3>
              <table>
                <tbody>
                  {info.receipts.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <Link to={companyPath(company, `/warehouse?tab=receipts`)}>№ {r.number}</Link>
                      </td>
                      <td>
                        {r.amount} {r.currency}
                        {r.incomplete ? ` · ${t("settlements.incomplete")}` : ""}
                      </td>
                      <td>{t("settlements.to_pay", { amount: r.to_pay, currency: r.currency })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
          {info.returns.length ? (
            <>
              <h3>{t("settlements.returns")}</h3>
              <table>
                <tbody>
                  {info.returns.map((r) => (
                    <ReturnLine key={r.id} line={r} />
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
          {info.entries.length ? (
            <>
              <h3>{t("settlements.openings")}</h3>
              <EntriesTable entries={info.entries} />
            </>
          ) : null}
          {info.payments.length ? (
            <>
              <h3>{t("settlements.supplier_payments")}</h3>
              <table>
                <tbody>
                  {info.payments.map((p) => (
                    <tr key={p.id}>
                      <td>№ {p.number}</td>
                      <td>{formatMoment(p.posted_at, zone, i18n.language)}</td>
                      <td>
                        {p.direction === "out" ? "−" : "+"}
                        {p.amount} {p.currency}
                      </td>
                      <td>{p.kind === "reversal" ? t("money.kind_reversal") : p.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
          {can("finance.payout") || can("finance.income.record") ? (
            <SupplierPaymentForm counterpartyId={counterpartyId} receipts={info.receipts} />
          ) : null}
        </>
      ) : null}
    </Card>
  );
}

/** The financial result of a return to the supplier (§ 11.4.2). */
function ReturnLine({ line }: { line: SupplierSettlement["returns"][number] }) {
  const { t } = useTranslation();
  const can = useCan();
  const company = useCompany();
  const refresh = useRefreshSettlements();
  const [result, setResult] = useState("debt_reduced");
  const [amount, setAmount] = useState("");
  const settle = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/supplier-returns/${line.id}/settle`);
  return (
    <tr>
      <td>№ {line.number}</td>
      <td>
        {line.amount} {line.currency}
        {line.incomplete ? ` · ${t("settlements.incomplete")}` : ""}
      </td>
      <td>
        {line.settled ? (
          <span className="badge">{t("settlements.return_settled")}</span>
        ) : can("finance.settlement.manage") ? (
          <div className="row">
            <select value={result} onChange={(e) => setResult(e.target.value)}>
              {["debt_reduced", "money_back", "replacement"].map((r) => (
                <option key={r} value={r}>
                  {t(`settlements.result_${r}`)}
                </option>
              ))}
            </select>
            {line.incomplete ? (
              <input
                inputMode="decimal"
                placeholder={t("payments.amount", { currency: line.currency })}
                value={amount}
                onChange={(e) => setAmount(e.target.value.replace(",", "."))}
              />
            ) : null}
            <button
              type="button"
              disabled={settle.pending || (line.incomplete && !amount)}
              onClick={async () => {
                if ((await settle.run({ result, amount: amount || null })) !== null) await refresh();
              }}
            >
              {t("settlements.settle_return")}
            </button>
          </div>
        ) : null}
        <ErrorMessage error={settle.error} />
      </td>
    </tr>
  );
}

function SupplierPaymentForm({
  counterpartyId,
  receipts,
}: {
  counterpartyId: string;
  receipts: SupplierSettlement["receipts"];
}) {
  const { t } = useTranslation();
  const can = useCan();
  const company = useCompany();
  const refresh = useRefreshSettlements();
  const accounts = (useCashAccounts().data?.cash_accounts ?? []).filter((a) => a.state === "active");
  const articles = (useArticles().data?.articles ?? []).filter((a) => a.kind === "expense" && a.state === "active");
  const nameOf = useArticleName();
  const directions = (["out", "in"] as const).filter((d) => can(d === "out" ? "finance.payout" : "finance.income.record"));
  const [form, setForm] = useState({ direction: "", account: "", amount: "", article: "", receipt: "", note: "" });
  const pay = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/supplier-payments`);
  const direction = form.direction || directions[0];
  const account = accounts.find((a) => a.id === form.account) ?? accounts[0];
  const goods = articles.find((a) => a.code === "goods_materials");
  const article = form.article || goods?.id || "";
  const linkable = receipts.filter((r) => r.currency === account?.currency);
  if (!accounts.length || !direction) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!account) return;
    const body = {
      cash_account_id: account.id,
      counterparty_id: counterpartyId,
      direction,
      amount: form.amount,
      currency: account.currency,
      article_id: article,
      links: form.receipt ? [{ receipt_id: form.receipt, amount: form.amount }] : [],
      note: form.note,
    };
    if ((await pay.run(body)) !== null) {
      setForm({ ...form, amount: "", receipt: "", note: "" });
      await refresh();
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <h3>{t("settlements.supplier_payment_title")}</h3>
      <div className="row">
        <Field label={t("money.direction")}>
          <select value={direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}>
            {directions.map((d) => (
              <option key={d} value={d}>
                {t(`settlements.supplier_${d}`)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("money.account")}>
          <select value={account?.id ?? ""} onChange={(e) => setForm({ ...form, account: e.target.value, receipt: "" })}>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} · {a.currency}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("payments.amount", { currency: account?.currency ?? "" })}>
          <input inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value.replace(",", ".") })} />
        </Field>
      </div>
      <div className="row">
        <Field label={t("money.article")}>
          <select value={article} onChange={(e) => setForm({ ...form, article: e.target.value })}>
            {articles.map((a) => (
              <option key={a.id} value={a.id}>
                {nameOf(a)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("settlements.link_receipt")} hint={t("settlements.link_hint")}>
          <select value={form.receipt} onChange={(e) => setForm({ ...form, receipt: e.target.value })}>
            <option value="">{t("settlements.on_account")}</option>
            {linkable.map((r) => (
              <option key={r.id} value={r.id}>
                № {r.number} · {t("settlements.to_pay", { amount: r.to_pay, currency: r.currency })}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("money.note")}>
          <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
        </Field>
      </div>
      <ErrorMessage error={pay.error} />
      <div className="actions">
        <button type="submit" disabled={!form.amount || !article || pay.pending}>
          {t(direction === "out" ? "settlements.supplier_pay" : "settlements.supplier_money_back")}
        </button>
      </div>
    </form>
  );
}

// --- Initial obligations (§ 17.2.14) --------------------------------------------------------------

function OpeningForm() {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefreshSettlements();
  const counterparties = useCounterparties();
  const [party, setParty] = useState<"client" | "counterparty">("client");
  const [client, setClient] = useState<Client | null>(null);
  const [form, setForm] = useState({
    counterparty: "",
    debtor: "party",
    amount: "",
    currency: company.main_currency ?? "",
    as_of: new Date().toISOString().slice(0, 10),
    note: "",
  });
  const create = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/settlement-entries`);
  const partyId = party === "client" ? client?.id : form.counterparty;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = {
      client_id: party === "client" ? partyId : null,
      counterparty_id: party === "counterparty" ? partyId : null,
      debtor: form.debtor,
      amount: form.amount,
      currency: form.currency,
      as_of: form.as_of,
      note: form.note,
    };
    if ((await create.run(body)) !== null) {
      setForm({ ...form, amount: "", note: "" });
      await refresh();
    }
  }

  return (
    <Card title={t("settlements.opening_title")}>
      <p className="muted">{t("settlements.opening_hint")}</p>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("settlements.party")}>
            <select value={party} onChange={(e) => setParty(e.target.value as "client" | "counterparty")}>
              <option value="client">{t("settlements.client")}</option>
              <option value="counterparty">{t("settlements.counterparty")}</option>
            </select>
          </Field>
          {party === "counterparty" ? (
            <Field label={t("settlements.counterparty")}>
              <select value={form.counterparty} onChange={(e) => setForm({ ...form, counterparty: e.target.value })}>
                <option value="">—</option>
                {(counterparties.data?.counterparties ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          <Field label={t("settlements.who_owes")}>
            <select value={form.debtor} onChange={(e) => setForm({ ...form, debtor: e.target.value })}>
              <option value="party">{t(`settlements.debtor_party_${party}`)}</option>
              <option value="company">{t(`settlements.debtor_company_${party}`)}</option>
            </select>
          </Field>
        </div>
        {party === "client" ? (
          client ? (
            <p>
              <strong>{clientName(client)}</strong>{" "}
              <button type="button" className="secondary" onClick={() => setClient(null)}>
                {t("rentals.change_client")}
              </button>
            </p>
          ) : (
            <ClientFinder onPick={setClient} />
          )
        ) : null}
        <div className="row">
          <Field label={t("payments.amount", { currency: "" })}>
            <input inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value.replace(",", ".") })} />
          </Field>
          <Field label={t("cash.currency")}>
            <select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>
              {currencies().map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("settlements.as_of")}>
            <input type="date" value={form.as_of} onChange={(e) => setForm({ ...form, as_of: e.target.value })} />
          </Field>
          <Field label={t("money.note")}>
            <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </Field>
        </div>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={!partyId || !form.amount || !form.currency || create.pending}>
            {t("settlements.opening_submit")}
          </button>
        </div>
      </form>
    </Card>
  );
}
