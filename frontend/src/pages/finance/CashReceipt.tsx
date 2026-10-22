import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { Order, Payment } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { useCashAccounts } from "./FinancePage";

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
  const accounts = useCashAccounts();
  const usable = (accounts.data?.cash_accounts ?? []).filter((a) => a.state === "active");
  const orderCurrencies = order ? Object.keys(order.amounts) : [];
  const preferred = usable.find((a) => orderCurrencies.includes(a.currency)) ?? usable[0];
  const [accountId, setAccountId] = useState("");
  const account = usable.find((a) => a.id === (accountId || preferred?.id));
  const remainder = order && account ? (order.amounts[account.currency]?.to_pay ?? "") : "";
  const [amount, setAmount] = useState<string | null>(null);
  const [allocation, setAllocation] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [done, setDone] = useState<Payment | null>(null);
  const receive = useCommand<Record<string, unknown>, { payment: Payment }>(`/c/${company.public_id}/payments/cash`);
  const amountValue = amount ?? (Number(remainder) > 0 ? remainder : "");
  const allocationValue = allocation ?? amountValue;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!account) return;
    const result = await receive.run({
      cash_account_id: account.id,
      client_id: clientId,
      amount: amountValue,
      currency: account.currency,
      allocations: order && allocationValue ? [{ order_id: order.id, amount: allocationValue }] : [],
      note,
    });
    if (result) {
      setDone(result.payment);
      setAmount(null);
      setAllocation(null);
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
        </div>
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
          <button type="submit" disabled={receive.pending || !account || !amountValue}>
            {t("payments.receive")}
          </button>
        </div>
      </form>
    </Card>
  );
}
