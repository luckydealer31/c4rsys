import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { OwnerSeat, OwnersView, OwnershipPolicy, RightSpec, ShareTransfer } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { useCatalog } from "../settings/EmployeesPage";
import { useLocations } from "../settings/LocationsPage";
import { cleanRights, RightsEditor } from "../settings/RightsEditor";
import { CodeConfirm, partyLabel, TransferRow, useMoment } from "./common";

/** Owners of the company (§ 3.3.4, § 3.3.6): the current owners with their shares, transfers,
 * the history of ownership and the approval policies. Seen by the owners; a share gives owner
 * rights only. */
export function OwnersPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const view = useApi<OwnersView>(["owners", company.public_id], `/c/${company.public_id}/owners`);
  const moment = useMoment();

  if (view.isLoading) return <div className="page"><Loading /></div>;
  if (view.error || !view.data) {
    return (
      <div className="page">
        <h1>{t("ownership.title")}</h1>
        <ErrorMessage error={view.error as ApiError | null} />
      </div>
    );
  }
  const data = view.data;
  return (
    <div className="page">
      <h1>{t("ownership.title")}</h1>
      <p className="muted">{t("ownership.owner_rights_hint")}</p>
      <Card title={t("ownership.owners")}>
        <table>
          <thead>
            <tr>
              <th>{t("ownership.owner")}</th>
              <th>{t("ownership.percent")}</th>
              <th>{t("ownership.since")}</th>
            </tr>
          </thead>
          <tbody>
            {data.owners.map((o) => (
              <tr key={o.owner.public_id}>
                <td>
                  {partyLabel(o.owner)}{" "}
                  {o.is_top_authority ? <span className="badge ok">{t("ownership.top_authority")}</span> : null}
                </td>
                <td>{Number(o.percent)}%</td>
                <td className="small">{moment(o.since)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {data.me.is_owner && data.me.percent ? (
        <TransferWizard
          path={`/c/${company.public_id}/owners/transfers`}
          ownPercent={data.me.percent}
          isTop={data.me.is_top_authority && data.owners.some((o) => o.is_top_authority && o.owner.public_id === data.me.public_id)}
          owners={data.owners.map((o) => o.owner.public_id).filter((id) => id !== data.me.public_id)}
          invalidate={["owners", company.public_id]}
        />
      ) : null}
      <TransfersCard transfers={data.transfers} />
      <PolicyCard policy={data.policy} editable={data.me.is_top_authority} />
      {data.me.is_top_authority && data.seats.length ? <SeatsCard seats={data.seats} /> : null}
      <Card title={t("ownership.history")}>
        <table>
          <thead>
            <tr>
              <th>{t("ownership.owner")}</th>
              <th>{t("ownership.percent")}</th>
              <th>{t("ownership.period")}</th>
            </tr>
          </thead>
          <tbody>
            {data.history.map((h, index) => (
              <tr key={index}>
                <td>{partyLabel(h.owner)}</td>
                <td>{Number(h.percent)}%</td>
                <td className="small">
                  {moment(h.valid_from)} — {h.valid_to ? moment(h.valid_to) : t("ownership.now")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function TransfersCard({ transfers }: { transfers: ShareTransfer[] }) {
  const { t } = useTranslation();
  if (!transfers.length) return null;
  return (
    <Card title={t("ownership.transfers")}>
      <table>
        <thead>
          <tr>
            <th>{t("ownership.parties")}</th>
            <th>{t("ownership.percent")}</th>
            <th>{t("ownership.state")}</th>
            <th>{t("ownership.date")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {transfers.map((x) => (
            <TransferRow key={x.id} transfer={x} />
          ))}
        </tbody>
      </table>
    </Card>
  );
}

/** § 3.4.3: recipient by c4rsys ID, username or company ID; the size in percent of the whole
 * capital, at most the own share; a successor of the top authority when the director gives away
 * the whole remaining share (§ 3.4.6); then a one-time code. */
export function TransferWizard({
  path,
  ownPercent,
  isTop,
  owners,
  invalidate,
}: {
  path: string;
  ownPercent: string;
  isTop: boolean;
  owners: string[];
  invalidate: readonly unknown[];
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [recipient, setRecipient] = useState("");
  const [percent, setPercent] = useState("");
  const [successor, setSuccessor] = useState("");
  const [sent, setSent] = useState(false);
  const start = useCommand<Record<string, string>, { transfer: ShareTransfer }>(path);
  const whole = percent !== "" && Number(percent) === Number(ownPercent);
  const needsSuccessor = whole && isTop;
  const candidates = [...owners, ...(recipient.trim() ? [recipient.trim()] : [])];
  const valid =
    recipient.trim() !== "" &&
    Number(percent) > 0 &&
    Number(percent) <= Number(ownPercent) &&
    (!needsSuccessor || successor !== "");

  async function confirm(flow: string, code: string): Promise<boolean> {
    setSent(false);
    const body: Record<string, string> = { recipient: recipient.trim(), percent, flow, code };
    if (needsSuccessor) body.successor = successor;
    const result = await start.run(body);
    if (result) {
      setSent(true);
      setRecipient("");
      setPercent("");
      setSuccessor("");
      await client.invalidateQueries({ queryKey: invalidate });
      await client.invalidateQueries({ queryKey: ["ownership-requests"] });
      return true;
    }
    return false;
  }

  return (
    <Card title={t("ownership.transfer_title")}>
      <form className="stack" onSubmit={(e: FormEvent) => e.preventDefault()}>
        <div className="row">
          <Field label={t("ownership.recipient")} hint={t("ownership.recipient_hint")}>
            <input value={recipient} onChange={(e) => setRecipient(e.target.value)} />
          </Field>
          <Field
            label={t("ownership.transfer_percent")}
            hint={t("ownership.transfer_percent_hint", { percent: Number(ownPercent) })}
          >
            <input
              inputMode="decimal"
              value={percent}
              onChange={(e) => setPercent(e.target.value.replace(",", "."))}
            />
          </Field>
        </div>
        {needsSuccessor ? (
          <>
            <div className="alert warn">{t("ownership.successor_required")}</div>
            <Field label={t("ownership.successor")}>
              <select value={successor} onChange={(e) => setSuccessor(e.target.value)}>
                <option value="">—</option>
                {candidates.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </Field>
          </>
        ) : null}
        <p className="muted small">{t("ownership.transfer_explained")}</p>
        {sent ? <div className="alert ok">{t("ownership.transfer_sent")}</div> : null}
        <CodeConfirm
          action="transfer_share"
          label={t("ownership.transfer_start")}
          disabled={!valid}
          onConfirm={confirm}
          error={start.error}
        />
      </form>
    </Card>
  );
}

const TRANSFER_MODES = ["none", "all", "percent"] as const;
const STATE_MODES = ["unset", "none", "all", "percent"] as const;

/** § 3.4.2 and § 3.6.2: additional approval of a transfer and approval of deactivation. */
export function PolicyCard({ policy, editable }: { policy: OwnershipPolicy; editable: boolean }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const [draft, setDraft] = useState(policy);
  const save = useCommand<Record<string, unknown>, { policy: OwnershipPolicy }>(
    `/c/${company.public_id}/ownership-policy`,
    "PUT",
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await save.run({
      transfer_mode: draft.transfer_mode,
      transfer_percent: draft.transfer_mode === "percent" ? draft.transfer_percent : null,
      state_mode: draft.state_mode,
      state_percent: draft.state_mode === "percent" ? draft.state_percent : null,
      expected_version: policy.version,
    });
    if (result) {
      setDraft(result.policy);
      await client.invalidateQueries({ queryKey: ["owners", company.public_id] });
      await client.invalidateQueries({ queryKey: ["company-state", company.public_id] });
    }
  }

  return (
    <Card title={t("ownership.policy")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("ownership.transfer_policy")} hint={t("ownership.transfer_policy_hint")}>
            <select
              disabled={!editable}
              value={draft.transfer_mode}
              onChange={(e) => setDraft({ ...draft, transfer_mode: e.target.value as OwnershipPolicy["transfer_mode"] })}
            >
              {TRANSFER_MODES.map((m) => (
                <option key={m} value={m}>
                  {t(`ownership.mode_${m}`)}
                </option>
              ))}
            </select>
          </Field>
          {draft.transfer_mode === "percent" ? (
            <Field label={t("ownership.mode_percent_value")}>
              <input
                disabled={!editable}
                inputMode="decimal"
                value={draft.transfer_percent ?? ""}
                onChange={(e) => setDraft({ ...draft, transfer_percent: e.target.value })}
              />
            </Field>
          ) : null}
        </div>
        <div className="row">
          <Field label={t("ownership.state_policy")} hint={t("ownership.state_policy_hint")}>
            <select
              disabled={!editable}
              value={draft.state_mode}
              onChange={(e) => setDraft({ ...draft, state_mode: e.target.value as OwnershipPolicy["state_mode"] })}
            >
              {STATE_MODES.map((m) => (
                <option key={m} value={m}>
                  {t(`ownership.mode_${m}`)}
                </option>
              ))}
            </select>
          </Field>
          {draft.state_mode === "percent" ? (
            <Field label={t("ownership.mode_percent_value")}>
              <input
                disabled={!editable}
                inputMode="decimal"
                value={draft.state_percent ?? ""}
                onChange={(e) => setDraft({ ...draft, state_percent: e.target.value })}
              />
            </Field>
          ) : null}
        </div>
        <ErrorMessage error={save.error} />
        {editable ? (
          <div className="actions">
            <button type="submit" disabled={save.pending}>
              {t("common.save")}
            </button>
          </div>
        ) : null}
      </form>
    </Card>
  );
}

/** § 3.4.1: the top authority sets the limit of rights of each owner company's representative. */
function SeatsCard({ seats }: { seats: OwnerSeat[] }) {
  const { t } = useTranslation();
  return (
    <>
      <h2>{t("ownership.representatives")}</h2>
      <p className="muted">{t("ownership.limit_hint")}</p>
      {seats.map((seat) => (
        <SeatLimit key={seat.id} seat={seat} />
      ))}
    </>
  );
}

function SeatLimit({ seat }: { seat: OwnerSeat }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const catalog = useCatalog();
  const locations = useLocations();
  const [limit, setLimit] = useState<RightSpec[]>(seat.limit);
  const save = useCommand<{ rights: RightSpec[] }, unknown>(
    `/c/${company.public_id}/owners/seats/${seat.id}/limit`,
    "PUT",
  );
  return (
    <Card title={partyLabel(seat.owner)}>
      <p className="small">
        {t("ownership.representative")}:{" "}
        {seat.holder
          ? `${seat.holder.first_name} ${seat.holder.last_name} · ${seat.holder.public_id ?? ""}`
          : t("ownership.vacant")}
        {seat.state === "prepared" ? ` · ${t("ownership.seat_prepared")}` : ""}
      </p>
      {catalog.data ? (
        <RightsEditor
          value={limit}
          onChange={setLimit}
          catalog={catalog.data}
          locations={locations.data?.locations ?? []}
        />
      ) : null}
      <ErrorMessage error={save.error} />
      <div className="actions">
        <button
          type="button"
          disabled={save.pending}
          onClick={async () => {
            if ((await save.run({ rights: cleanRights(limit) })) !== null)
              await client.invalidateQueries({ queryKey: ["owners", company.public_id] });
          }}
        >
          {t("ownership.save_limit")}
        </button>
      </div>
    </Card>
  );
}
