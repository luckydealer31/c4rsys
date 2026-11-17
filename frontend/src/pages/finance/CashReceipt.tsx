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
import { RateAmount, effectiveRate, useRateSuggestion } from "./Rates";

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
  prefer,
}: {
  clientId: string;
  order: Order | null;
  onDone: () => Promise<void>;
  /** Inside a window that has the title: the form alone. */
  bare?: boolean;
  /** The currency of the charges to settle first, when the order has several. */
  prefer?: string;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const accounts = useCashAccounts();
  const usable = (accounts.data?.cash_accounts ?? []).filter((a) => a.state === "active");
  const orderCurrencies = order ? Object.keys(order.amounts) : [];
  // Money is received on any account, in its currency, and settles the charges of the order by
  // the rate (§ 17.1.4; decision of 16.11.2026). An order charged in several currencies is paid
  // currency by currency: the one asked for, else the one with the most left to pay.
  const [target, setTarget] = useState("");
  const mostOwed = [...orderCurrencies].sort(
    (a, b) => Number(order?.amounts[b]?.to_pay ?? 0) - Number(order?.amounts[a]?.to_pay ?? 0),
  )[0];
  const orderCurrency =
    target || (prefer && orderCurrencies.includes(prefer) ? prefer : mostOwed) || undefined;
  const preferred = usable.find((a) => a.currency === orderCurrency) ?? usable[0];
  const [accountId, setAccountId] = useState("");
  const account = usable.find((a) => a.id === (accountId || preferred?.id));
  const exchange = Boolean(order && account && orderCurrency && orderCurrency !== account.currency);
  const [manualRate, setManualRate] = useState("");
  const suggestion = useRateSuggestion(exchange ? account?.currency : undefined, orderCurrency);
  const rate = exchange ? effectiveRate(suggestion.data, manualRate) : null;
  const owed = order && orderCurrency ? (order.amounts[orderCurrency]?.to_pay ?? "") : "";
  // What is left to pay is offered as the amount only in the currency of the account. In
  // another currency nothing is offered (decision of 16.11.2026): what was received is typed
  // in, the rate turns it into the currency of the order, and a part may stay to be paid later.
  const remainder = order && account && !exchange && Number(owed) > 0 ? owed : "";
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
  // In a window opened for this order the whole payment goes to it: no field of its own.
  const allocationValue = bare
    ? amountValue
    : (allocation ?? (Number(remainder) > 0 ? remainder : amount !== null ? amount : ""));

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
          ? [
              {
                order_id: order.id,
                amount: allocationValue,
                rate: exchange && manualRate ? manualRate : null,
                ...(orderCurrency ? { currency: orderCurrency } : {}),
              },
            ]
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

  const amountFields = (
    <>
      <Field label={t("payments.amount", { currency: account?.currency ?? "" })}>
        <input value={amountValue} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} />
      </Field>
      {order && !bare ? (
        <Field label={t("payments.allocation", { number: order.number })} hint={t("payments.allocation_hint")}>
          <input value={allocationValue} inputMode="decimal" onChange={(e) => setAllocation(e.target.value)} />
        </Field>
      ) : null}
    </>
  );
  const Frame = bare ? Bare : Card;
  return (
    <Frame title={t("payments.receive_title")}>
      {usable.length === 0 ? <p className="muted">{t("payments.no_accounts")}</p> : null}
      <form className="stack" onSubmit={submit}>
        {/* The account and what the money settles in one line; below, in one line again, the
            amount, the rate and the amount in the currency of the charges — either of the last
            two may be changed (decisions of 16.11.2026). No notes around them. */}
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
          {order && orderCurrencies.length > 1 ? (
            <Field label={t("payments.settle_currency")}>
              <select
                value={orderCurrency ?? ""}
                onChange={(e) => {
                  setTarget(e.target.value);
                  setAmount(null);
                  setAllocation(null);
                  setManualRate("");
                }}
              >
                {orderCurrencies.map((code) => (
                  <option key={code} value={code}>
                    {t("payments.settle_option", { currency: code, amount: order.amounts[code]?.to_pay ?? "0" })}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
        </div>
        {/* One line in every case: the field of the amount keeps its place while the rate comes. */}
        <RateAmount
          lead={amountFields}
          from={account?.currency}
          to={exchange && order && account && orderCurrency ? orderCurrency : undefined}
          amount={allocationValue}
          manual={manualRate}
          onManual={setManualRate}
        />
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
        <div className={bare ? "actions end" : "actions"}>
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
  prefer,
}: {
  orderId: string;
  clientId: string | null;
  onDone: () => Promise<void>;
  /** The currency of the calculation the button stands under. */
  prefer?: string;
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
        <Modal roomy title={t("payments.receive_title")} onClose={() => setOpen(false)}>
          {order.data ? (
            <CashReceipt
              bare
              prefer={prefer}
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
        <Modal roomy title={t("deposit.pay")} onClose={() => setOpen(false)}>
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

  const amountField = (
    <Field label={t("deposit.pay_amount", { currency: account?.currency ?? "" })}>
      <input value={amountValue} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} />
    </Field>
  );
  return (
    <form className="stack" onSubmit={submit}>
      {usable.length === 0 ? <p className="muted">{t("payments.no_accounts")}</p> : null}
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
      </div>
      {/* One line in every case: the field of the amount keeps its place while the rate comes. */}
      <RateAmount
        lead={amountField}
        from={account?.currency}
        to={exchange && account ? deposit.currency : undefined}
        amount={amountValue}
        manual={manualRate}
        onManual={setManualRate}
      />
      <div className="row">
        <Field label={t("payments.note")}>
          <input value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
      <ErrorMessage error={receive.error} />
      <div className="actions end">
        <Info text={t("deposit.pay_hint")} />
        <button type="submit" disabled={receive.pending || !account || !amountValue || (exchange && !rate)}>
          {t("deposit.pay")}
        </button>
      </div>
    </form>
  );
}
