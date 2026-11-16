import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { Order } from "../../api/types";
import { ErrorMessage, Info, Switch } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";

type OrderResponse = { order: Order };

/** A position no longer counted: it is cancelled already. */
const GONE = ["cancelled", "annulled", "removed"];

/** When the only position of an order is cancelled, the order is offered to be cancelled with
 * it, so that an empty order does not stay forgotten (customer's decision of 16.11.2026).
 * `own` — the position being cancelled and what belongs to it (the services linked to a
 * rental). The offer needs the right «Cancel orders»; the order is cancelled by its own command,
 * with the same reason. */
export function useOrderOffer(orderId: string | null | undefined, own: string[]) {
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const path = `/c/${company.public_id}/orders/${orderId}`;
  const found = useApi<OrderResponse>(
    ["order", company.public_id, orderId ?? ""],
    path,
    Boolean(orderId) && can("order.cancel"),
  );
  const order = found.data?.order;
  const alone = Boolean(
    order &&
      ["preliminary", "open", "ready_to_close"].includes(order.state) &&
      order.operations.every((o) => own.includes(o.id) || GONE.includes(o.state)),
  );
  const [wanted, setWanted] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  /** Cancels the order as it is now: its positions not started are cancelled with it. */
  async function cancel(reason: string): Promise<boolean> {
    setPending(true);
    setError(null);
    try {
      // The version is read again: cancelling a position before this may have moved it.
      const fresh = (await api<OrderResponse>(path)).order;
      await api<OrderResponse>(`${path}/cancel`, {
        method: "POST",
        body: { reason, expected_version: fresh.version },
        idempotencyKey: crypto.randomUUID(),
      });
      await queryClient.invalidateQueries({ queryKey: ["order", company.public_id] });
      await queryClient.invalidateQueries({ queryKey: ["orders", company.public_id] });
      return true;
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, null));
      return false;
    } finally {
      setPending(false);
    }
  }

  return { offered: alone, wanted: alone && wanted, setWanted, cancel, pending, error, number: order?.number };
}

/** Said when the position is cancelled and the order is not: it stays open. */
export function OrderLeft({ offer, onClose }: { offer: ReturnType<typeof useOrderOffer>; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="stack">
      <p className="alert warn">{t("orders.cancel_with_failed", { number: offer.number })}</p>
      <ErrorMessage error={offer.error} />
      <div className="actions">
        <button type="button" className="secondary" onClick={onClose}>
          {t("common.close")}
        </button>
      </div>
    </div>
  );
}

/** The switch of the offer: on by itself. */
export function OrderOffer({ offer }: { offer: ReturnType<typeof useOrderOffer> }) {
  const { t } = useTranslation();
  if (!offer.offered) return null;
  const name = t("orders.cancel_with", { number: offer.number });
  return (
    <div className="switch-line">
      <Switch name={name} checked={offer.wanted} onChange={offer.setWanted}>
        {name}
      </Switch>
      <Info text={t("orders.cancel_with_hint")} />
    </div>
  );
}
