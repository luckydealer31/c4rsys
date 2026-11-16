import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { DepositInfo, Order, Payment } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Info, Modal } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { useCashAccounts } from "./FinancePage";
import { RateField, effectiveRate, useRateSuggestion } from "./Rates";

export function usePayments(q = "") {
  const company = useCompany();
  return useApi<{ payments: Payment[] }>(
    q ? ["payments", company.public_id, "q", q] : ["payments", company.public_id],
    `/c/${company.public_id}/payments${q ? `?q=${encodeURIComponent(q)}` : ""}`,
  );
}

/** Cash receipt (§ 17.2.9–17.2.10, STAGE1_SCREENS): account, amount in the account's currency,
 * client, allocation to the order — the order's remainder by default. Money may be received
 * without allocation (decision on T15). A repeated click or network retry reuses the key and
 * does not create a second payment. */
export function CashReceipt({
  clientId,
  order,
  onDone,
  bare = false,
}: {
  clientId: string;
  order: Order | null;
  onDone: () => Promise<void>;
  /** Inside a window that has the title: the form alone. */
  bare?: boolean;
}) {
  const { t } = useTranslation();
  const company = useCompany();
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
  // The deposit is not a part of a payment (decision of 15.11.2026): it is received by an
  // action of its own, «Receive the deposit», so a payment is never split by hand.
  const rentalId = order?.operations.find((o) => o.type === "rental")?.id;
  const due = Number(remainder) > 0 ? Number(remainder) : 0;
  const amountValue = amount ?? (due > 0 ? due.toFixed(2) : "");
  const allocationValue = allocation ?? (Number(remainder) > 0 ? remainder : amount !== null ? amount : "");
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
      deposits: [],
      note,
    });
    if (result) {
      setDone(result.payment);
      setAmount(null);
      setAllocation(null);
      setManualRate("");
      setNote("");
      await onDone();
    }
  }

  const Frame = bare ? Bare : Card;
  return (
    <Frame title={t("payments.receive_title")}>
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
      {rentalId && !bare ? <DepositButton rentalId={rentalId} clientId={clientId} onDone={onDone} /> : null}
    </Frame>
  );
}

function Bare({ children }: { title?: string; children: ReactNode }) {
  return <div>{children}</div>;
}

/** «Receive a payment» for the order of a rental or a booking (decision of 16.11.2026): a
 * button of its own beside the calculation, apart from «Receive the deposit»; it opens the
 * payment of the order in a window. Without a client there is nobody to receive from. */
export function PaymentButton({
  orderId,
  clientId,
  onDone,
}: {
  orderId: string;
  clientId: string | null;
  onDone: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const order = useApi<{ order: Order }>(
    ["order", company.public_id, orderId],
    `/c/${company.public_id}/orders/${orderId}`,
    open,
  );
  if (!can("finance.payment.receive")) return null;
  return (
    <div className="actions end">
      {clientId ? null : <Info text={t("payments.client_first")} />}
      <button type="button" disabled={!clientId} onClick={() => setOpen(true)}>
        {t("payments.pay")}
      </button>
      {open && clientId ? (
        <Modal title={t("payments.receive_title")} onClose={() => setOpen(false)}>
          {order.data ? (
            <CashReceipt
              bare
              clientId={clientId}
              order={order.data.order}
              onDone={async () => {
                setOpen(false);
                await queryClient.invalidateQueries({ queryKey: ["order", company.public_id, orderId] });
                await onDone();
              }}
            />
          ) : (
            <ErrorMessage error={order.error as ApiError | null} />
          )}
        </Modal>
      ) : null}
    </div>
  );
}

/** «Receive the deposit» of a rental (decision of 15.11.2026): shown while a part of the
 * deposit is missing; opens a window where the money is received as the deposit only. */
export function DepositButton({
  rentalId,
  clientId,
  onDone,
}: {
  rentalId: string;
  /** Without a client the button is shown but cannot be pressed: there is nobody to receive from. */
  clientId: string | null;
  onDone: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [open, setOpen] = useState(false);
  const deposit = useApi<{ deposit: DepositInfo }>(
    ["rental-deposit", company.public_id, rentalId],
    `/c/${company.public_id}/rentals/${rentalId}/deposit`,
  ).data?.deposit;
  if (!deposit || !(Number(deposit.missing) > 0) || deposit.waived || !can("finance.payment.receive")) return null;
  return (
    <div className="stack" style={{ marginTop: 16 }}>
      <p className="alert warn">{t("deposit.conflict", { amount: deposit.missing, currency: deposit.currency })}</p>
      <div className="actions end" style={{ marginTop: 0 }}>
        {clientId ? null : <Info text={t("payments.client_first")} />}
        <button type="button" disabled={!clientId} onClick={() => setOpen(true)}>
          {t("deposit.pay")}
        </button>
      </div>
      {open && clientId ? (
        <Modal title={t("deposit.pay")} onClose={() => setOpen(false)}>
          <DepositReceipt
            rentalId={rentalId}
            clientId={clientId}
            deposit={deposit}
            onDone={async () => {
              setOpen(false);
              await onDone();
            }}
          />
        </Modal>
      ) : null}
    </div>
  );
}

function DepositReceipt({
  rentalId,
  clientId,
  deposit,
  onDone,
}: {
  rentalId: string;
  clientId: string;
  deposit: DepositInfo;
  onDone: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const usable = (useCashAccounts().data?.cash_accounts ?? []).filter((a) => a.state === "active");
  const [accountId, setAccountId] = useState("");
  const account = usable.find((a) => a.id === accountId) ?? usable.find((a) => a.currency === deposit.currency) ?? usable[0];
  const exchange = Boolean(account && account.currency !== deposit.currency);
  const [manualRate, setManualRate] = useState("");
  const suggestion = useRateSuggestion(exchange ? account?.currency : undefined, deposit.currency);
  const rate = exchange ? effectiveRate(suggestion.data, manualRate) : null;
  const [amount, setAmount] = useState<string | null>(null);
  const amountValue = amount ?? (exchange ? "" : deposit.missing);
  const [note, setNote] = useState("");
  const receive = useCommand<Record<string, unknown>, { payment: Payment }>(`/c/${company.public_id}/payments/cash`);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!account) return;
    const result = await receive.run({
      cash_account_id: account.id,
      client_id: clientId,
      amount: amountValue,
      currency: account.currency,
      allocations: [],
      // The whole receipt is the deposit of this rental.
      deposits: [{ rental_id: rentalId, amount: amountValue, rate: exchange && manualRate ? manualRate : null }],
      note,
    });
    if (result) {
      await queryClient.invalidateQueries({ queryKey: ["rental-deposit", company.public_id] });
      await queryClient.invalidateQueries({ queryKey: ["rental", company.public_id] });
      await queryClient.invalidateQueries({ queryKey: ["payments", company.public_id] });
      await onDone();
    }
  }

  return (
    <form onSubmit={submit}>
      {usable.length === 0 ? <p className="muted">{t("payments.no_accounts")}</p> : null}
      <p className="muted small">{t("deposit.pay_hint")}</p>
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
        <Field label={t("deposit.pay_amount", { currency: account?.currency ?? "" })}>
          <input value={amountValue} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} />
        </Field>
      </div>
      {exchange && account ? (
        <RateField from={account.currency} to={deposit.currency} manual={manualRate} onManual={setManualRate} />
      ) : null}
      <div className="row">
        <Field label={t("payments.note")}>
          <input value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
      <ErrorMessage error={receive.error} />
      <div className="actions">
        <button type="submit" disabled={receive.pending || !account || !amountValue || (exchange && !rate)}>
          {t("deposit.pay")}
        </button>
      </div>
    </form>
  );
}
