import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import type { Rental } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Field, Info } from "../../components/ui";
import { OrderOffer, useOrderOffer } from "../orders/CancelWith";
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
          {t(rental.state === "booked" && !rental.booking?.direct ? "bookings.action_transfer" : "rental_order.transfer")}
        </button>
        <Info text={t("rental_order.transfer_hint")} />
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
  // The only position of its order: the order is offered to be cancelled with it (decision of
  // 16.11.2026). An issued rental is annulled first, then the order is cancelled.
  const linked = (rental.services ?? []).filter((line) => line.rental_id === rental.id).map((line) => line.id);
  const offer = useOrderOffer(rental.order_id, [rental.id, ...linked]);
  const [annulled, setAnnulled] = useState<Rental | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await annul.run({ reason, expected_version: rental.version });
    if (!result) return;
    if (offer.wanted && !(await offer.cancel(reason))) {
      // The rental is annulled, the order is not cancelled: said before the card is read again.
      setAnnulled(result.rental);
      return;
    }
    await onDone(result.rental);
  }

  if (annulled) {
    return (
      <div className="stack">
        <p className="alert warn">{t("orders.cancel_with_failed", { number: offer.number })}</p>
        <ErrorMessage error={offer.error} />
        <div className="actions">
          <button type="button" className="secondary" onClick={() => void onDone(annulled)}>
            {t("common.close")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="stack">
      <p className="muted small">{t("rental_order.annul_hint")}</p>
      <div className="row">
        <Field label={t("rental_order.annul_reason")}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
      <OrderOffer offer={offer} />
      <ErrorMessage error={annul.error} />
      <div className="actions">
        <button type="submit" className="danger" disabled={annul.pending || offer.pending || !reason.trim()}>
          {t("rental_order.annul")}
        </button>
      </div>
    </form>
  );
}
