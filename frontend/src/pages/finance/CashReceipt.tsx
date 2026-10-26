import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { DepositInfo, Order, Payment } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { useCashAccounts } from "./FinancePage";
import { RateField, effectiveRate, useRateSuggestion } from "./Rates";

export function usePayments() {
  const company = useCompany();
  return useApi<{ payments: Payment[] }>(["payments", company.public_id], `/c/${company.public_id}/payments`);
}

/** Cash receipt (§ 17.2.9–17.2.10, STAGE1_SCREENS): account, amount in the account's currency,
 * client, allocation to the order — the order's remainder by default. Money may be received
 * without allocation (decision on T15). A repeated click or network retry reuses the key and
 * does not create a second payment. */
export function CashReceipt({
  clientId,
  order,
  onDone,
}: {
  clientId: string;
  order: Order | null;
  onDone: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const accounts = useCashAccounts();
  const usable = (accounts.data?.cash_accounts ?? []).filter((a) => a.state === "active");
  const orderCurrencies = order ? Object.keys(order.amounts) : [];
  const preferred = usable.find((a) => orderCurrencies.includes(a.currency)) ?? usable[0];
  const [accountId, setAccountId] = useState("");
  const account = usable.find((a) => a.id === (accountId || preferred?.id));
  // An order charged in one currency may be paid in another: the receipt stays in the account's
  // currency and settles the order by the rate (§ 17.1.4).
  const orderCurrency = orderCurrencies.length === 1 ? orderCurrencies[0] : undefined;
  const exchange = Boolean(order && account && orderCurrency && orderCurrency !== account.currency);
  const [manualRate, setManualRate] = useState("");
  const suggestion = useRateSuggestion(exchange ? account?.currency : undefined, orderCurrency);
  const rate = exchange ? effectiveRate(suggestion.data, manualRate) : null;
  const remainder = order && account && !exchange ? (order.amounts[account.currency]?.to_pay ?? "") : "";
  const [amount, setAmount] = useState<string | null>(null);
  const [allocation, setAllocation] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [done, setDone] = useState<Payment | null>(null);
  const receive = useCommand<Record<string, unknown>, { payment: Payment }>(`/c/${company.public_id}/payments/cash`);
  // § 14.7.3: the deposit part of the receipt is named explicitly, apart from the payment.
  const rentalId = order?.operations.find((o) => o.type === "rental")?.id;
  const deposit = useApi<{ deposit: DepositInfo }>(
    ["rental-deposit", company.public_id, rentalId ?? ""],
    `/c/${company.public_id}/rentals/${rentalId ?? ""}/deposit`,
    Boolean(rentalId),
  ).data?.deposit;
  const missing = deposit && Number(deposit.missing) > 0 && !deposit.waived ? deposit.missing : "";
  const [depositPart, setDepositPart] = useState<string | null>(null);
  const depositValue = depositPart ?? (account && deposit && account.currency === deposit.currency ? missing : "");
  const due = (Number(remainder) > 0 ? Number(remainder) : 0) + (Number(depositValue) || 0);
  const amountValue = amount ?? (due > 0 ? due.toFixed(2) : "");
  // Without a set remainder the order gets what is left after the deposit part.
  const rest = amount !== null ? Math.max(Number(amount) - (Number(depositValue) || 0), 0) : 0;
  const allocationValue =
    allocation ?? (Number(remainder) > 0 ? remainder : rest > 0 ? rest.toFixed(2) : "");
  const settles = exchange && rate && allocationValue ? (Number(allocationValue) * Number(rate)).toFixed(2) : "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!account) return;
    const result = await receive.run({
      cash_account_id: account.id,
      client_id: clientId,
      amount: amountValue,
      currency: account.currency,
      allocations:
        order && allocationValue
          ? [{ order_id: order.id, amount: allocationValue, rate: exchange && manualRate ? manualRate : null }]
          : [],
      deposits:
        rentalId && depositValue
          ? [{ rental_id: rentalId, amount: depositValue, rate: exchange && manualRate ? manualRate : null }]
          : [],
      note,
    });
    if (result) {
      setDone(result.payment);
      setAmount(null);
      setAllocation(null);
      setDepositPart(null);
      await queryClient.invalidateQueries({ queryKey: ["rental-deposit", company.public_id] });
      await queryClient.invalidateQueries({ queryKey: ["rental", company.public_id] });
      setManualRate("");
      setNote("");
      await onDone();
    }
  }

  return (
    <Card title={t("payments.receive_title")}>
      {usable.length === 0 ? <p className="muted">{t("payments.no_accounts")}</p> : null}
      <form onSubmit={submit}>
        <div className="row">
          <Field label={t("payments.account")}>
            <select value={account?.id ?? ""} onChange={(e) => setAccountId(e.target.value)}>
              {usable.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} · {a.currency}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("payments.amount", { currency: account?.currency ?? "" })}>
            <input value={amountValue} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} />
          </Field>
          {order ? (
            <Field label={t("payments.allocation", { number: order.number })} hint={t("payments.allocation_hint")}>
              <input value={allocationValue} inputMode="decimal" onChange={(e) => setAllocation(e.target.value)} />
            </Field>
          ) : null}
          {rentalId && deposit && (Number(deposit.required) > 0 || depositPart) ? (
            <Field
              label={t("payments.deposit_part", { currency: account?.currency ?? "" })}
              hint={t("payments.deposit_hint", { missing: deposit.missing, currency: deposit.currency })}
            >
              <input value={depositValue} inputMode="decimal" onChange={(e) => setDepositPart(e.target.value)} />
            </Field>
          ) : null}
        </div>
        {exchange && order && account && orderCurrency ? (
          <>
            <p className="muted">
              {t("payments.order_currency", {
                currency: orderCurrency,
                amount: order.amounts[orderCurrency]?.to_pay ?? "0",
              })}
            </p>
            <RateField from={account.currency} to={orderCurrency} manual={manualRate} onManual={setManualRate} />
            {settles ? <p>{t("payments.settles", { amount: settles, currency: orderCurrency })}</p> : null}
          </>
        ) : null}
        <div className="row">
          <Field label={t("payments.note")}>
            <input value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
        </div>
        <ErrorMessage error={receive.error} />
        {done ? (
          <div className="alert ok">
            {t("payments.received", { number: done.number, amount: done.amount, currency: done.currency })}
          </div>
        ) : null}
        <div className="actions">
          <button type="submit" disabled={receive.pending || !account || !amountValue || (exchange && !rate)}>
            {t("payments.receive")}
          </button>
        </div>
      </form>
    </Card>
  );
}
