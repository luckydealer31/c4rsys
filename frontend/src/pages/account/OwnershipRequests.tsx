import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import type { OwnershipRequests as Requests, ShareTransfer } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage } from "../../components/ui";
import { partyLabel, useMoment } from "../ownership/common";

/** Confirmations addressed to the user in the personal area: share transfers to accept or
 * approve — as the recipient, a co-owner, an owner of an owner company or the successor of the
 * top authority (§ 3.4.1–3.4.3, § 3.4.6) — and owners' approvals of a deactivation or a
 * restoration (§ 3.6.2). Shown only when there are any. */
export function OwnershipRequestsSection() {
  const { t } = useTranslation();
  const requests = useQuery({
    queryKey: ["ownership-requests"],
    queryFn: () => api<Requests>("/ownership-requests"),
  });
  const data = requests.data;
  if (!data || (!data.transfers.length && !data.states.length && !data.outgoing.length)) return null;
  return (
    <>
      <h2>{t("ownership.requests_title")}</h2>
      {data.transfers.map((x) => (
        <TransferDecision key={x.approval_id} item={x} />
      ))}
      {data.states.map((x) => (
        <StateDecision key={`${x.request_id}-${x.party_key}`} item={x} />
      ))}
      {data.outgoing.map((x) => (
        <Outgoing key={x.id} transfer={x} />
      ))}
    </>
  );
}

function useRefresh() {
  const client = useQueryClient();
  return async () => {
    await client.invalidateQueries({ queryKey: ["ownership-requests"] });
    await client.invalidateQueries({ queryKey: ["companies"] });
  };
}

function TransferDecision({ item }: { item: Requests["transfers"][number] }) {
  const { t } = useTranslation();
  const moment = useMoment();
  const refresh = useRefresh();
  const approve = useCommand<Record<string, never>, unknown>(
    `/ownership-requests/transfers/${item.approval_id}/approve`,
  );
  const decline = useCommand<Record<string, never>, unknown>(
    `/ownership-requests/transfers/${item.approval_id}/decline`,
  );
  const tr = item.transfer;
  // An owner of an owner company confirms for that company; a co-owner or the successor for
  // himself or the company he acts for.
  const subject =
    item.role === "sender_owner" ? tr.sender : item.role === "recipient_owner" ? tr.recipient : item.party;
  return (
    <Card title={partyLabel(item.company)}>
      <p>{t(`ownership.role_${item.role}`, { party: partyLabel(subject) })}</p>
      <p className="small">
        {partyLabel(tr.sender)} → {partyLabel(tr.recipient)}: <strong>{Number(tr.percent)}%</strong>
        {tr.successor ? <> · {t("ownership.successor")}: {partyLabel(tr.successor)}</> : null}
      </p>
      <p className="muted small">
        {t("ownership.expires", { at: moment(tr.expires_at) })} ·{" "}
        {t("ownership.confirmations", {
          done: tr.approvals.filter((a) => a.state === "approved").length,
          total: tr.approvals.length,
        })}
      </p>
      {item.role === "recipient" ? <p className="muted small">{t("ownership.accept_consent")}</p> : null}
      <ErrorMessage error={approve.error ?? decline.error} />
      <div className="actions">
        <button
          type="button"
          disabled={approve.pending}
          onClick={async () => {
            if ((await approve.run({})) !== null) await refresh();
          }}
        >
          {item.role === "recipient" || item.role === "successor" ? t("ownership.accept") : t("ownership.approve")}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={async () => {
            if ((await decline.run({})) !== null) await refresh();
          }}
        >
          {t("ownership.decline")}
        </button>
      </div>
    </Card>
  );
}

function StateDecision({ item }: { item: Requests["states"][number] }) {
  const { t } = useTranslation();
  const refresh = useRefresh();
  const approve = useCommand<{ party: string }, unknown>(
    `/ownership-requests/states/${item.request_id}/approve`,
  );
  const decline = useCommand<{ party: string }, unknown>(
    `/ownership-requests/states/${item.request_id}/decline`,
  );
  return (
    <Card title={partyLabel(item.company)}>
      <p>{t(`company_state.asks_${item.kind}`, { party: partyLabel(item.party) })}</p>
      {item.blockers.length ? (
        <div className="alert warn">
          {item.blockers.map((b) => t(`company_state.blocker_${b}`)).join("; ")}
        </div>
      ) : null}
      <ErrorMessage error={approve.error ?? decline.error} />
      <div className="actions">
        <button
          type="button"
          onClick={async () => {
            if ((await approve.run({ party: item.party_key })) !== null) await refresh();
          }}
        >
          {t("ownership.approve")}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={async () => {
            if ((await decline.run({ party: item.party_key })) !== null) await refresh();
          }}
        >
          {t("ownership.decline")}
        </button>
      </div>
    </Card>
  );
}

function Outgoing({ transfer }: { transfer: ShareTransfer }) {
  const { t } = useTranslation();
  const moment = useMoment();
  const refresh = useRefresh();
  const cancel = useCommand<Record<string, never>, unknown>(`/ownership-transfers/${transfer.id}/cancel`);
  return (
    <Card title={transfer.company.display_name}>
      <p className="small">
        {t("ownership.outgoing", { percent: Number(transfer.percent), to: partyLabel(transfer.recipient) })}
      </p>
      <p className="muted small">
        {t("ownership.expires", { at: moment(transfer.expires_at) })} ·{" "}
        {t("ownership.confirmations", {
          done: transfer.approvals.filter((a) => a.state === "approved").length,
          total: transfer.approvals.length,
        })}
      </p>
      <ErrorMessage error={cancel.error} />
      <div className="actions">
        <button
          type="button"
          className="secondary"
          onClick={async () => {
            if ((await cancel.run({})) !== null) await refresh();
          }}
        >
          {t("ownership.cancel_transfer")}
        </button>
      </div>
    </Card>
  );
}
