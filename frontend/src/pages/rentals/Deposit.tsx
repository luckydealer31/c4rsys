import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { CashAccount, DepositInfo, Rental } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Field } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useCashAccounts } from "../finance/FinancePage";
import { RateField, effectiveRate, useRateSuggestion } from "../finance/Rates";
import { useOrders } from "../orders/OrdersPage";

type Action = "" | "to_balance" | "hold" | "release" | "offset" | "refund" | "waive";

/** The deposit of a rental (§ 14.7): required, received, held and where it went; a missing
 * deposit apart from the debt (§ 14.3.3). Actions need «Manage deposits»; the receipt is part of
 * the cash receipt of the order. */
export function DepositCard({ rental, onDone }: { rental: Rental; onDone: () => Promise<void> }) {
  const { t, i18n } = useTranslation();
  const can = useCan();
  const deposit = rental.deposit;
  const [action, setAction] = useState<Action>("");
  if (!deposit) return null;
  if (deposit.required === "0.00" && deposit.received === "0.00") return <p className="muted">{t("deposit.not_required")}</p>;
  const c = deposit.currency;
  const manage = can("finance.deposit.manage");
  const actions: Action[] = [];
  if (manage && Number(deposit.held) > 0) actions.push("to_balance", "offset", "refund");
  if (manage && Number(deposit.held) > Number(deposit.claimed)) actions.push("hold");
  if (manage && Number(deposit.claimed) > 0) actions.push("release");
  if (manage && deposit.conflict) actions.push("waive");

  return (
    <div className="stack">
      {deposit.conflict ? (
        <div className="alert warn">{t("deposit.conflict", { amount: deposit.missing, currency: c })}</div>
      ) : null}
      <dl className="facts">
        <dt>{t("deposit.required")}</dt>
        <dd>
          {deposit.required} {c}
          {deposit.waived ? ` · ${t("deposit.waived")}` : ""}
        </dd>
        <dt>{t("deposit.received")}</dt>
        <dd>
          {deposit.received} {c}
        </dd>
        <dt>{t("deposit.held")}</dt>
        <dd>
          <strong>
            {deposit.held} {c}
          </strong>
          {Number(deposit.claimed) > 0 ? ` · ${t("deposit.claimed", { amount: deposit.claimed, currency: c })}` : ""}
        </dd>
        <dt>{t("deposit.used")}</dt>
        <dd>{t("deposit.used_value", { offset: deposit.offset, refunded: deposit.refunded, to_balance: deposit.to_balance, currency: c })}</dd>
        <dt>{t("deposit.term")}</dt>
        <dd>
          {deposit.hold_days === null
            ? t("deposit.term_manual")
            : deposit.release_at
              ? t("deposit.term_until", { time: formatMoment(deposit.release_at, rental.calc_timezone, i18n.language) })
              : t("deposit.term_days", { days: deposit.hold_days })}
        </dd>
      </dl>
      {deposit.entries.length ? (
        <table>
          <tbody>
            {deposit.entries.map((e) => (
              <tr key={e.id}>
                <td>{formatMoment(e.created_at, rental.calc_timezone, i18n.language)}</td>
                <td>
                  {t(`deposit.kind_${e.kind}`)}
                  {e.automatic ? ` · ${t("deposit.automatic")}` : ""}
                </td>
                <td>
                  {e.amount} {c}
                  {e.money_amount ? ` (${e.money_amount} ${e.money_currency})` : ""}
                </td>
                <td>{e.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {actions.length ? (
        <div className="actions" style={{ marginTop: 0 }}>
          {actions.map((a) => (
            <button key={a} type="button" className={action === a ? "" : "secondary"} onClick={() => setAction(action === a ? "" : a)}>
              {t(`deposit.action_${a}`)}
            </button>
          ))}
        </div>
      ) : null}
      {action ? <DepositAction key={action} action={action} rental={rental} deposit={deposit} onDone={async () => { setAction(""); await onDone(); }} /> : null}
    </div>
  );
}

function DepositAction({
  action,
  rental,
  deposit,
  onDone,
}: {
  action: Exclude<Action, "">;
  rental: Rental;
  deposit: DepositInfo;
  onDone: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const orders = useOrders();
  const accounts = (useCashAccounts().data?.cash_accounts ?? []).filter((a: CashAccount) => a.state === "active");
  const limit = action === "hold" ? Number(deposit.held) - Number(deposit.claimed) : action === "release" ? Number(deposit.claimed) : Number(deposit.held);
  const [amount, setAmount] = useState(action === "waive" ? "" : limit.toFixed(2));
  const [reason, setReason] = useState("");
  const [orderId, setOrderId] = useState(rental.order_id);
  const [accountId, setAccountId] = useState("");
  const [rate, setRate] = useState("");
  const [withClient, setWithClient] = useState(false);
  const path = { to_balance: "to-balance", hold: "hold", release: "release", offset: "offset", refund: "refund", waive: "waive" }[action];
  const run = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/rentals/${rental.id}/deposit/${path}`);
  const account = accounts.find((a) => a.id === accountId) ?? accounts.find((a) => a.currency === deposit.currency) ?? accounts[0];
  const exchange = action === "refund" && Boolean(account && account.currency !== deposit.currency);
  const suggestion = useRateSuggestion(exchange ? deposit.currency : undefined, account?.currency);
  const applied = exchange ? effectiveRate(suggestion.data, rate) : "1";
  const debts = (orders.data?.orders ?? []).filter(
    (o) =>
      o.client_id === rental.client_id &&
      o.state !== "cancelled" &&
      Object.values(o.amounts).some((a) => Number(a.to_pay) > 0),
  );
  const order = debts.find((o) => o.id === orderId);
  const orderCurrency = order ? Object.keys(order.amounts)[0] : undefined;
  const offsetExchange = action === "offset" && Boolean(orderCurrency && orderCurrency !== deposit.currency);
  const offsetSuggestion = useRateSuggestion(offsetExchange ? deposit.currency : undefined, orderCurrency);
  const offsetRate = offsetExchange ? effectiveRate(offsetSuggestion.data, rate) : "1";
  const needsReason = action === "hold" || action === "release" || action === "waive";

  async function submit() {
    const body: Record<string, unknown> = { reason };
    if (action !== "waive") body.amount = amount;
    if (action === "offset") {
      body.order_id = orderId;
      body.rate = offsetExchange && rate ? rate : null;
    }
    if (action === "refund") {
      body.cash_account_id = account?.id;
      body.rate = exchange && rate ? rate : null;
      body.vehicle_with_client = withClient;
      body.note = reason;
    }
    if ((await run.run(body)) !== null) await onDone();
  }

  return (
    <div className="stack">
      <p className="muted">{t(`deposit.hint_${action}`)}</p>
      <div className="row">
        {action !== "waive" ? (
          <Field label={t("payments.amount", { currency: deposit.currency })}>
            <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(",", "."))} />
          </Field>
        ) : null}
        {action === "offset" ? (
          <Field label={t("deposit.order")}>
            <select value={orderId} onChange={(e) => setOrderId(e.target.value)}>
              {debts.map((o) => (
                <option key={o.id} value={o.id}>
                  № {o.number} ·{" "}
                  {Object.entries(o.amounts)
                    .map(([c, a]) => `${a.to_pay} ${c}`)
                    .join(", ")}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        {action === "refund" ? (
          <Field label={t("money.account")}>
            <select value={account?.id ?? ""} onChange={(e) => setAccountId(e.target.value)}>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} · {a.currency}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field label={needsReason ? t("deposit.reason") : t("money.note")}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
      {exchange && account ? <RateField from={deposit.currency} to={account.currency} manual={rate} onManual={setRate} /> : null}
      {offsetExchange && orderCurrency ? (
        <RateField from={deposit.currency} to={orderCurrency} manual={rate} onManual={setRate} />
      ) : null}
      {action === "refund" && exchange && applied && amount ? (
        <p>{t("deposit.refund_preview", { amount: (Number(amount) * Number(applied)).toFixed(2), currency: account?.currency ?? "" })}</p>
      ) : null}
      {action === "refund" && rental.state === "active" ? (
        <label>
          <input type="checkbox" checked={withClient} onChange={(e) => setWithClient(e.target.checked)} /> {t("deposit.vehicle_with_client")}
        </label>
      ) : null}
      <ErrorMessage error={run.error} />
      <div className="actions" style={{ marginTop: 0 }}>
        <button
          type="button"
          disabled={
            run.pending ||
            (action !== "waive" && !amount) ||
            (needsReason && !reason.trim()) ||
            (action === "offset" && (!debts.length || !offsetRate)) ||
            (action === "refund" && (!account || !applied || (rental.state === "active" && !withClient)))
          }
          onClick={submit}
        >
          {t(`deposit.action_${action}`)}
        </button>
      </div>
    </div>
  );
}
