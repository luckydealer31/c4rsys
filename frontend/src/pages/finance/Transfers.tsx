import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import type { CashAccount } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { RateField, effectiveRate, useRateSuggestion } from "./Rates";

/** A transfer between own accounts (§ 17.1.6): the amount leaves the source account in its
 * currency and arrives in the currency of the target account by the applied rate. Neither income
 * nor expense; fees are recorded as separate expenses. */
export function TransferForm({ accounts }: { accounts: CashAccount[] }) {
  const { t } = useTranslation();
  const can = useCan();
  const company = useCompany();
  const queryClient = useQueryClient();
  const active = accounts.filter((a) => a.state === "active");
  const [chosen, setForm] = useState({ from: "", to: "", amount: "", rate: "", note: "" });
  const transfer = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/transfers`);
  const from = active.find((a) => a.id === (chosen.from || active[0]?.id));
  const others = active.filter((a) => a.id !== from?.id);
  const to = others.find((a) => a.id === chosen.to) ?? others[0];
  const exchange = Boolean(from && to && from.currency !== to.currency);
  const suggestion = useRateSuggestion(exchange ? from?.currency : undefined, to?.currency);
  if (!can("finance.transfer") || active.length < 2) return null;

  const rate = exchange ? effectiveRate(suggestion.data, chosen.rate) : "1";
  const arriving = rate && chosen.amount ? (Number(chosen.amount) * Number(rate)).toFixed(2) : "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!from || !to) return;
    const body = {
      from_account_id: from.id,
      to_account_id: to.id,
      amount: chosen.amount,
      rate: exchange && chosen.rate ? chosen.rate : null,
      note: chosen.note,
    };
    if ((await transfer.run(body)) !== null) {
      setForm({ ...chosen, amount: "", rate: "", note: "" });
      for (const key of ["cash-accounts", "account-records", "payments"]) {
        await queryClient.invalidateQueries({ queryKey: [key, company.public_id] });
      }
    }
  }

  return (
    <Card title={t("money.transfer_title")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("money.transfer_from")}>
            <select
              value={from?.id ?? ""}
              onChange={(e) => setForm({ ...chosen, from: e.target.value, rate: "" })}
            >
              {active.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} · {a.currency}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("money.transfer_to")}>
            <select
              value={to?.id ?? ""}
              onChange={(e) => setForm({ ...chosen, to: e.target.value, rate: "" })}
            >
              {others.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} · {a.currency}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("payments.amount", { currency: from?.currency ?? "" })}>
            <input
              inputMode="decimal"
              value={chosen.amount}
              onChange={(e) => setForm({ ...chosen, amount: e.target.value.replace(",", ".") })}
            />
          </Field>
          <Field label={t("money.note")}>
            <input value={chosen.note} onChange={(e) => setForm({ ...chosen, note: e.target.value })} />
          </Field>
        </div>
        {exchange && from && to ? (
          <RateField
            from={from.currency}
            to={to.currency}
            manual={chosen.rate}
            onManual={(value) => setForm({ ...chosen, rate: value })}
          />
        ) : null}
        {exchange && arriving ? (
          <p>{t("money.transfer_arrives", { amount: arriving, currency: to?.currency ?? "" })}</p>
        ) : null}
        <ErrorMessage error={transfer.error} />
        <div className="actions">
          <button type="submit" disabled={!chosen.amount || !to || !rate || transfer.pending}>
            {t("money.transfer_submit")}
          </button>
        </div>
      </form>
    </Card>
  );
}
