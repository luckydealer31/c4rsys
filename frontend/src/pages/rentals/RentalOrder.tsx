import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import type { Rental } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Field } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { useOrders } from "../orders/OrdersPage";

type Done = (next?: Rental) => Promise<void>;

/** A right rental in a wrong order moves to another open order of the same client or to a new
 * one; its charges go with it, payments stay and are relinked in finance (§ 12.3.7; decision З6). */
export function TransferRental({ rental, onDone }: { rental: Rental; onDone: Done }) {
  const { t } = useTranslation();
  const company = useCompany();
  const orders = useOrders();
  const [target, setTarget] = useState("new");
  const transfer = useCommand<Record<string, unknown>, { rental: Rental }>(
    `/c/${company.public_id}/rentals/${rental.id}/transfer`,
  );
  const options = (orders.data?.orders ?? []).filter(
    (o) =>
      o.id !== rental.order_id &&
      !o.is_internal &&
      o.client_id === rental.client_id &&
      (o.state === "open" || o.state === "ready_to_close"),
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await transfer.run({
      order_id: target === "new" ? null : target,
      expected_version: rental.version,
    });
    if (result) await onDone(result.rental);
  }

  return (
    <form onSubmit={submit}>
      <p className="muted small">{t("rental_order.transfer_hint")}</p>
      <div className="row">
        <Field label={t("rental_order.target")}>
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="new">{t("rental_order.new_order")}</option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                № {o.number}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <ErrorMessage error={transfer.error} />
      <div className="actions">
        <button type="submit" className="secondary" disabled={transfer.pending}>
          {t("rental_order.transfer")}
        </button>
      </div>
    </form>
  );
}

/** A wrong rental is annulled with a reason: its charges and those of its linked services go to
 * zero; the vehicle's place is corrected apart (§ 12.3.7; decision З5). */
export function AnnulRental({ rental, onDone }: { rental: Rental; onDone: Done }) {
  const { t } = useTranslation();
  const company = useCompany();
  const [reason, setReason] = useState("");
  const annul = useCommand<Record<string, unknown>, { rental: Rental }>(
    `/c/${company.public_id}/rentals/${rental.id}/annul`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await annul.run({ reason, expected_version: rental.version });
    if (result) await onDone(result.rental);
  }

  return (
    <form onSubmit={submit}>
      <p className="muted small">{t("rental_order.annul_hint")}</p>
      <div className="row">
        <Field label={t("rental_order.annul_reason")}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
      <ErrorMessage error={annul.error} />
      <div className="actions">
        <button type="submit" className="danger" disabled={annul.pending || !reason.trim()}>
          {t("rental_order.annul")}
        </button>
      </div>
    </form>
  );
}
