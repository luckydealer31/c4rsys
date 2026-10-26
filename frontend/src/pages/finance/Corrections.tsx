import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { Payment } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { useOrders } from "../orders/OrdersPage";
import { useVehicles } from "../vehicles/VehiclesPage";

const REVERSIBLE = new Set(["manual", "payment", "supplier", "refund", "transfer"]);

function useRefreshMoney() {
  const client = useQueryClient();
  const company = useCompany();
  return async () => {
    for (const key of ["cash-accounts", "account-records", "payments", "orders", "settlements", "client-settlement", "supplier-settlement"]) {
      await client.invalidateQueries({ queryKey: [key, company.public_id] });
    }
  };
}

/** Actions with a posted record: reversal with a reason (§ 23.2.7) and split of an expense
 * between vehicles and orders (§ 17.3.3). The record itself never changes. */
export function RecordActions({ record }: { record: Payment }) {
  const { t } = useTranslation();
  const can = useCan();
  const [mode, setMode] = useState<"" | "reverse" | "split">("");
  const reversible =
    can("finance.correct") && REVERSIBLE.has(record.kind) && !record.reversed_by_id && record.allocations.every((a) => a.state !== "active");
  const splittable =
    can("finance.payout") && record.direction === "out" && (record.kind === "manual" || record.kind === "supplier") && !record.reversed_by_id;
  return (
    <div className="stack">
      {record.reversed_by_id ? <span className="badge">{t("corrections.reversed")}</span> : null}
      {record.kind === "reversal" && record.reason ? <span className="muted">{record.reason}</span> : null}
      {record.shares.length ? (
        <span className="muted">
          {t("corrections.split", { count: record.shares.length })}
        </span>
      ) : null}
      {mode === "" ? (
        <div className="actions" style={{ marginTop: 0 }}>
          {reversible ? (
            <button type="button" className="secondary" onClick={() => setMode("reverse")}>
              {t("corrections.reverse")}
            </button>
          ) : null}
          {splittable ? (
            <button type="button" className="secondary" onClick={() => setMode("split")}>
              {t("corrections.split_action")}
            </button>
          ) : null}
        </div>
      ) : null}
      {mode === "reverse" ? <ReverseForm record={record} onDone={() => setMode("")} /> : null}
      {mode === "split" ? <SplitForm record={record} onDone={() => setMode("")} /> : null}
    </div>
  );
}

function ReverseForm({ record, onDone }: { record: Payment; onDone: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefreshMoney();
  const [reason, setReason] = useState("");
  const reverse = useCommand<Record<string, string>, unknown>(`/c/${company.public_id}/money-records/${record.id}/reverse`);
  return (
    <div className="stack">
      <p className="muted">{t(record.kind === "transfer" ? "corrections.reverse_transfer_hint" : "corrections.reverse_hint")}</p>
      <input placeholder={t("corrections.reason")} value={reason} onChange={(e) => setReason(e.target.value)} />
      <ErrorMessage error={reverse.error} />
      <div className="actions" style={{ marginTop: 0 }}>
        <button
          type="button"
          disabled={!reason.trim() || reverse.pending}
          onClick={async () => {
            if ((await reverse.run({ reason })) !== null) {
              await refresh();
              onDone();
            }
          }}
        >
          {t("corrections.reverse_submit")}
        </button>
        <button type="button" className="secondary" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}

type Row = { object: string; amount: string };

function SplitForm({ record, onDone }: { record: Payment; onDone: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefreshMoney();
  const vehicles = useVehicles();
  const orders = useOrders();
  const initial: Row[] = record.shares.length
    ? record.shares.map((s) => ({ object: s.vehicle_id ? `vehicle:${s.vehicle_id}` : `order:${s.order_id}`, amount: s.amount }))
    : [
        { object: "", amount: "" },
        { object: "", amount: "" },
      ];
  const [rows, setRows] = useState<Row[]>(initial);
  const save = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/money-records/${record.id}/shares`);
  const total = rows.reduce((sum, row) => sum + (Number(row.amount) || 0), 0);
  const left = Number(record.amount) - total;

  function update(index: number, change: Partial<Row>) {
    setRows(rows.map((row, i) => (i === index ? { ...row, ...change } : row)));
  }

  async function submit() {
    const shares = rows
      .filter((row) => row.object && row.amount)
      .map((row) => {
        const [kind, id] = row.object.split(":");
        return { amount: row.amount, vehicle_id: kind === "vehicle" ? id : null, order_id: kind === "order" ? id : null };
      });
    if ((await save.run({ shares })) !== null) {
      await refresh();
      onDone();
    }
  }

  return (
    <div className="stack">
      <p className="muted">{t("corrections.split_hint")}</p>
      {rows.map((row, index) => (
        <div className="row" key={index}>
          <select value={row.object} onChange={(e) => update(index, { object: e.target.value })}>
            <option value="">—</option>
            <optgroup label={t("corrections.vehicles")}>
              {(vehicles.data?.vehicles ?? []).map((v) => (
                <option key={v.id} value={`vehicle:${v.id}`}>
                  {v.name}
                </option>
              ))}
            </optgroup>
            <optgroup label={t("corrections.orders")}>
              {(orders.data?.orders ?? []).map((o) => (
                <option key={o.id} value={`order:${o.id}`}>
                  № {o.number}
                </option>
              ))}
            </optgroup>
          </select>
          <input inputMode="decimal" value={row.amount} onChange={(e) => update(index, { amount: e.target.value.replace(",", ".") })} />
        </div>
      ))}
      <p>{t("corrections.left", { amount: left.toFixed(2), currency: record.currency })}</p>
      <ErrorMessage error={save.error} />
      <div className="actions" style={{ marginTop: 0 }}>
        <button type="button" className="secondary" onClick={() => setRows([...rows, { object: "", amount: "" }])}>
          {t("corrections.add_row")}
        </button>
        <button type="button" disabled={save.pending || Math.abs(left) > 0.000001} onClick={submit}>
          {t("common.save")}
        </button>
        <button type="button" className="secondary" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}

/** Removes the link of a payment to an order or moves it to another order of the same client;
 * the old link stays in history; the amount is corrected by a reversal, not here (§ 17.2.11). */
export function RelinkAllocation({ record, allocationId }: { record: Payment; allocationId: string }) {
  const { t } = useTranslation();
  const can = useCan();
  const company = useCompany();
  const refresh = useRefreshMoney();
  const orders = useOrders();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const relink = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/payment-allocations/${allocationId}/relink`);
  if (!can("finance.correct")) return null;
  if (!open)
    return (
      <button type="button" className="link" onClick={() => setOpen(true)}>
        {t("corrections.relink")}
      </button>
    );
  const others = (orders.data?.orders ?? []).filter(
    (o) => o.client_id === record.client_id && o.state !== "cancelled" && !record.allocations.some((a) => a.id === allocationId && a.order_id === o.id),
  );
  return (
    <div className="stack">
      <select value={target} onChange={(e) => setTarget(e.target.value)}>
        <option value="">{t("corrections.unlink")}</option>
        {others.map((o) => (
          <option key={o.id} value={o.id}>
            № {o.number}
          </option>
        ))}
      </select>
      <input placeholder={t("corrections.reason")} value={reason} onChange={(e) => setReason(e.target.value)} />
      <ErrorMessage error={relink.error} />
      <div className="actions" style={{ marginTop: 0 }}>
        <button
          type="button"
          disabled={!reason.trim() || relink.pending}
          onClick={async () => {
            if ((await relink.run({ order_id: target || null, reason })) !== null) {
              setOpen(false);
              await refresh();
            }
          }}
        >
          {t("common.save")}
        </button>
        <button type="button" className="secondary" onClick={() => setOpen(false)}>
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}
