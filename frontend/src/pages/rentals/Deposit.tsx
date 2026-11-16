import { DepositButton } from "../finance/CashReceipt";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { CashAccount, DepositInfo, Rental } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Field } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useCashAccounts } from "../finance/FinancePage";
import { RateAmount, effectiveRate, useRateSuggestion } from "../finance/Rates";
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
      {rental.state !== "cancelled" ? (
        <DepositButton rentalId={rental.id} clientId={rental.client_id} onDone={onDone} />
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
  // Which charges of an order in several currencies the deposit settles (decision of 16.11.2026).
  const [target, setTarget] = useState("");
  const path = { to_balance: "to-balance", hold: "hold", release: "release", offset: "offset", refund: "refund", waive: "waive" }[action];
  const run = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/rentals/${rental.id}/deposit/${path}`);
  // The account a refund is paid from is chosen by hand, none stands there by itself: a payout
  // must not leave from an account nobody looked at (customer's decision of 16.11.2026).
  const account = accounts.find((a) => a.id === accountId);
  const exchange = action === "refund" && Boolean(account && account.currency !== deposit.currency);
  const suggestion = useRateSuggestion(exchange ? deposit.currency : undefined, account?.currency);
  // Paid back in the currency the deposit came in: what came, no rate (decision of 16.11.2026).
  const came = exchange ? deposit.received_in.find((r) => r.currency === account?.currency) : undefined;
  const asReceived = Boolean(came && Number(amount) <= Number(came.amount));
  const payout =
    came && asReceived && Number(amount) > 0
      ? Number(amount) === Number(came.amount)
        ? Number(came.money).toFixed(2)
        : ((Number(amount) * Number(came.money)) / Number(came.amount)).toFixed(2)
      : "";
  const applied = exchange && !asReceived ? effectiveRate(suggestion.data, rate) : "1";
  const debts = (orders.data?.orders ?? []).filter(
    (o) =>
      o.client_id === rental.client_id &&
      o.state !== "cancelled" &&
      Object.values(o.amounts).some((a) => Number(a.to_pay) > 0),
  );
  const order = debts.find((o) => o.id === orderId);
  // As a payment: the currency chosen; else the deposit's own, when the order owes in it; else
  // the one with the most left to pay.
  const owing = order ? Object.keys(order.amounts).filter((code) => Number(order.amounts[code]?.to_pay) > 0) : [];
  const mostOwed = [...owing].sort(
    (a, b) => Number(order?.amounts[b]?.to_pay ?? 0) - Number(order?.amounts[a]?.to_pay ?? 0),
  )[0];
  const orderCurrency = order
    ? owing.includes(target)
      ? target
      : owing.includes(deposit.currency)
        ? deposit.currency
        : mostOwed
    : undefined;
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
      if (orderCurrency) body.currency = orderCurrency;
    }
    if (action === "refund") {
      body.cash_account_id = account?.id;
      body.rate = exchange && !asReceived && rate ? rate : null;
      body.vehicle_with_client = withClient;
      body.note = reason;
    }
    if ((await run.run(body)) !== null) await onDone();
  }

  const amountField = (
    <Field label={t("payments.amount", { currency: deposit.currency })}>
      <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(",", "."))} />
    </Field>
  );
  const reasonField = (
    <Field label={needsReason ? t("deposit.reason") : t("money.note")}>
      <input value={reason} onChange={(e) => setReason(e.target.value)} />
    </Field>
  );
  return (
    <div className="stack">
      <p className="muted">{t(`deposit.hint_${action}`)}</p>
      {action === "offset" ? (
        <>
          <div className="row">
            <Field label={t("deposit.order")}>
              <select
                value={orderId}
                onChange={(e) => {
                  setOrderId(e.target.value);
                  setTarget("");
                  setRate("");
                }}
              >
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
            {order && owing.length > 1 ? (
              <Field label={t("payments.settle_currency")}>
                <select
                  value={orderCurrency ?? ""}
                  onChange={(e) => {
                    setTarget(e.target.value);
                    setRate("");
                  }}
                >
                  {owing.map((code) => (
                    <option key={code} value={code}>
                      {t("payments.settle_option", { currency: code, amount: order.amounts[code]?.to_pay ?? "0" })}
                    </option>
                  ))}
                </select>
              </Field>
            ) : null}
          </div>
          {offsetExchange && orderCurrency ? (
            <RateAmount
              lead={amountField}
              from={deposit.currency}
              to={orderCurrency}
              amount={amount}
              manual={rate}
              onManual={setRate}
            />
          ) : (
            <div className="row">{amountField}</div>
          )}
          <div className="row">{reasonField}</div>
        </>
      ) : action === "refund" ? (
        <>
          <div className="row">
            <Field label={t("money.account")}>
              <select
                value={account?.id ?? ""}
                onChange={(e) => {
                  setAccountId(e.target.value);
                  setRate("");
                }}
              >
                <option value="">—</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} · {a.currency}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {exchange && account && asReceived ? (
            <div className="row">
              {amountField}
              <Field label={t("rates.equivalent", { currency: account.currency })}>
                <input value={payout} disabled readOnly />
              </Field>
            </div>
          ) : exchange && account ? (
            <RateAmount
              lead={amountField}
              from={deposit.currency}
              to={account.currency}
              amount={amount}
              manual={rate}
              onManual={setRate}
            />
          ) : (
            <div className="row">{amountField}</div>
          )}
          <div className="row">{reasonField}</div>
        </>
      ) : (
        <div className="row">
          {action !== "waive" ? amountField : null}
          {reasonField}
        </div>
      )}
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
