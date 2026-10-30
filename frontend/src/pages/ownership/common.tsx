import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../../api/client";
import type { PartyCard, ShareTransfer, StartedFlow } from "../../api/types";
import { ErrorMessage, Field } from "../../components/ui";
import { deviceTimeZone, formatMoment } from "../../time";

/** A user is shown by name once he is an owner (§ 3.3.6), before that by ID and username only
 * (§ 2.2.4); a company by its display name and company ID. */
export function partyLabel(card: PartyCard | null | undefined): string {
  if (!card) return "—";
  if (card.type === "company") return `${card.display_name} · ${card.public_id}`;
  const name = [card.first_name, card.last_name].filter(Boolean).join(" ");
  if (name) return `${name} · ${card.public_id}`;
  return card.username ? `@${card.username} · ${card.public_id}` : card.public_id;
}

export function useMoment(): (value: string | null | undefined) => string {
  const { i18n } = useTranslation();
  return (value) => formatMoment(value, deviceTimeZone(), i18n.language);
}

/** A critical action confirmed by a one-time code to the confirmed email (§ 2.1.1, § 2.4.5):
 * the first press asks for the code, the second sends the action with it. */
export function CodeConfirm({
  action,
  label,
  disabled,
  danger,
  onConfirm,
  error,
}: {
  action: string;
  label: string;
  disabled?: boolean;
  danger?: boolean;
  onConfirm: (flow: string, code: string) => Promise<boolean>;
  error: ApiError | null;
}) {
  const { t } = useTranslation();
  const [flow, setFlow] = useState<StartedFlow | null>(null);
  const [code, setCode] = useState("");
  const [requestError, setRequestError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  async function press() {
    setRequestError(null);
    if (!flow) {
      try {
        setFlow(await api<StartedFlow>("/me/confirmations", { method: "POST", body: { action } }));
      } catch (caught) {
        setRequestError(caught instanceof ApiError ? caught : new ApiError(0, null));
      }
      return;
    }
    setBusy(true);
    const done = await onConfirm(flow.flow, code);
    setBusy(false);
    if (done) {
      setFlow(null);
      setCode("");
    }
  }

  return (
    <div className="stack">
      {flow ? (
        <Field label={t("auth.code")} hint={t("ownership.code_sent")}>
          <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
      ) : null}
      <ErrorMessage error={requestError ?? error} />
      <div className="actions">
        <button
          type="button"
          className={danger ? "danger" : undefined}
          disabled={disabled || busy || (flow !== null && !code.trim())}
          onClick={() => void press()}
        >
          {flow ? t("auth.confirm") : label}
        </button>
        {flow ? (
          <button type="button" className="secondary" onClick={() => setFlow(null)}>
            {t("common.cancel")}
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function TransferRow({ transfer, actions }: { transfer: ShareTransfer; actions?: ReactNode }) {
  const { t } = useTranslation();
  const moment = useMoment();
  const done = transfer.approvals.filter((a) => a.state === "approved").length;
  return (
    <tr>
      <td>
        <div>{partyLabel(transfer.sender)}</div>
        <div className="muted small">→ {partyLabel(transfer.recipient)}</div>
        {transfer.successor ? (
          <div className="muted small">
            {t("ownership.successor")}: {partyLabel(transfer.successor)}
          </div>
        ) : null}
      </td>
      <td>{Number(transfer.percent)}%</td>
      <td>
        <span className={`badge ${transfer.state === "completed" ? "ok" : transfer.state === "pending" ? "warn" : ""}`}>
          {t(`ownership.transfer_state_${transfer.state}`)}
        </span>
        <div className="muted small">
          {t("ownership.confirmations", { done, total: transfer.approvals.length })}
        </div>
      </td>
      <td className="small">
        {moment(transfer.created_at)}
        {transfer.state === "pending" ? (
          <div className="muted">{t("ownership.expires", { at: moment(transfer.expires_at) })}</div>
        ) : null}
      </td>
      <td>{actions}</td>
    </tr>
  );
}
