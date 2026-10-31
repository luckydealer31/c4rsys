import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { api, type ApiError } from "../api/client";
import { useApi } from "../api/queries";
import { useCommand } from "../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../components/ui";
import { ReasonForm, useWhen } from "./common";

type Party = { type: "user" | "company"; public_id: string; name: string } | null;
type Dispute = {
  request_id: string;
  vehicle: string;
  company: { public_id: string; name: string };
  claimant: Party;
  claimed_at: string | null;
  current_owner: Party;
  current_since: string | null;
  decision: { kind: "keep" | "transfer"; reason: string; effective_at: string | null; decided_at: string } | null;
};
type Queue = {
  ownership_disputes: Dispute[];
  decided_disputes: Dispute[];
  ticket_records: { ticket_id: string; ticket_number: number; record_type: string; record_ref: string }[];
  identifier_conflicts: null;
};
type ProfileLink = {
  id: string;
  company: { public_id: string; name: string };
  card_type: string;
  profile: string;
  target: { type: string; public_id: string } | null;
  state: string;
  created_at: string;
  decided_at: string | null;
  broken_at: string | null;
  broken_initiator: string | null;
};
type Contact = { id: string; kind: string; value: string; unavailable: boolean; verified_at: string };

function PartyName({ party }: { party: Party }) {
  const { t } = useTranslation();
  if (!party) return <span className="muted">{t("staff.none")}</span>;
  return (
    <span>
      {party.name || "—"} ({party.public_id})
    </span>
  );
}

/** § 24.1.5, § 24.1.7: the queue of the verification — disputes of ownership, disputed records of
 * tickets, corrections of system links and unlinking a disputed contact. */
export function VerificationPage() {
  const { t } = useTranslation();
  const when = useWhen();
  const queue = useApi<Queue>(["staff", "verification"], "/staff/verification");
  return (
    <div className="page">
      <h1>{t("staff.nav_verification")}</h1>
      {queue.isLoading ? <Loading /> : null}
      <ErrorMessage error={queue.error as ApiError | null} />
      {queue.data ? (
        <>
          <Card title={t("staff.verify_disputes")}>
            <p className="muted small">{t("staff.verify_disputes_hint")}</p>
            {queue.data.ownership_disputes.length ? (
              <div className="stack">
                {queue.data.ownership_disputes.map((d) => (
                  <DisputeCard key={d.request_id} dispute={d} />
                ))}
              </div>
            ) : (
              <p className="muted">{t("staff.verify_no_disputes")}</p>
            )}
            {queue.data.decided_disputes.length ? (
              <>
                <h3>{t("staff.verify_decided")}</h3>
                <ul className="plain">
                  {queue.data.decided_disputes.map((d) => (
                    <li key={d.request_id} className="small">
                      {d.vehicle} · {t(`staff.verify_kind_${d.decision?.kind ?? "keep"}`)} · {d.decision?.reason} ·{" "}
                      <span className="muted">{when(d.decision?.decided_at)}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </Card>
          <Card title={t("staff.verify_records")}>
            {queue.data.ticket_records.length ? (
              <ul className="plain">
                {queue.data.ticket_records.map((r) => (
                  <li key={`${r.ticket_id}-${r.record_ref}`}>
                    <Link to={`/tickets/${r.ticket_id}`}>№ {r.ticket_number}</Link> · {r.record_type} · {r.record_ref}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">{t("staff.none")}</p>
            )}
          </Card>
        </>
      ) : null}
      <ProfileLinkCard />
      <ContactCard />
      <Card title={t("staff.verify_identifiers")}>
        {/* TODO(spec): conflicts of VIN and other identifiers — block 12. */}
        <p className="muted">{t("staff.verify_identifiers_todo")}</p>
      </Card>
    </div>
  );
}

function DisputeCard({ dispute: d }: { dispute: Dispute }) {
  const { t } = useTranslation();
  const when = useWhen();
  const client = useQueryClient();
  const [kind, setKind] = useState<"keep" | "transfer">("keep");
  const [reason, setReason] = useState("");
  const [moment, setMoment] = useState("");
  const [ticket, setTicket] = useState("");
  const decide = useCommand<Record<string, unknown>, unknown>(`/staff/verification/ownership/${d.request_id}`);
  return (
    <div className="list-link">
      <p>
        <strong>{d.vehicle}</strong> · {t("staff.verify_requested_by", { company: `${d.company.name} (${d.company.public_id})` })}
      </p>
      <p className="small">
        {t("staff.verify_current_owner")}: <PartyName party={d.current_owner} /> · {when(d.current_since)}
      </p>
      <p className="small">
        {t("staff.verify_claimant")}: <PartyName party={d.claimant} /> · {when(d.claimed_at)}
      </p>
      <form
        className="stack"
        onSubmit={async (e) => {
          e.preventDefault();
          const done = await decide.run({
            kind,
            reason,
            ...(kind === "transfer" && moment ? { effective_at: new Date(moment).toISOString() } : {}),
            ...(ticket ? { ticket_number: Number(ticket) } : {}),
          });
          if (done) void client.invalidateQueries({ queryKey: ["staff", "verification"] });
        }}
      >
        <div className="row">
          <Field label={t("staff.verify_decision")}>
            <select value={kind} onChange={(e) => setKind(e.target.value as "keep" | "transfer")}>
              <option value="keep">{t("staff.verify_kind_keep")}</option>
              <option value="transfer">{t("staff.verify_kind_transfer")}</option>
            </select>
          </Field>
          {kind === "transfer" ? (
            <Field label={t("staff.verify_effective_at")} hint={t("staff.verify_effective_at_hint")}>
              <input type="datetime-local" value={moment} onChange={(e) => setMoment(e.target.value)} />
            </Field>
          ) : null}
          <Field label={t("staff.ticket_number")}>
            <input inputMode="numeric" value={ticket} onChange={(e) => setTicket(e.target.value.replace(/\D/g, ""))} />
          </Field>
        </div>
        <Field label={t("staff.verify_reason")}>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} />
        </Field>
        <ErrorMessage error={decide.error} />
        <div className="actions">
          <button type="submit" disabled={decide.pending || !reason.trim()}>
            {t("staff.verify_decide")}
          </button>
        </div>
      </form>
    </div>
  );
}

function ProfileLinkCard() {
  const { t } = useTranslation();
  const when = useWhen();
  const [id, setId] = useState("");
  const [link, setLink] = useState<ProfileLink | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [ending, setEnding] = useState(false);
  const end = useCommand<{ reason: string }, { link: ProfileLink }>(() => `/staff/verification/profile-links/${link?.id ?? ""}/end`);
  return (
    <Card title={t("staff.verify_links")}>
      <p className="muted small">{t("staff.verify_links_hint")}</p>
      <form
        className="row"
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          try {
            setLink((await api<{ link: ProfileLink }>(`/staff/verification/profile-links/${id.trim()}`)).link);
          } catch (caught) {
            setLink(null);
            setError(caught as ApiError);
          }
        }}
      >
        <input value={id} placeholder={t("staff.verify_link_id")} onChange={(e) => setId(e.target.value)} aria-label={t("staff.verify_link_id")} />
        <button type="submit" className="secondary" disabled={!id.trim()}>
          {t("staff.verify_find")}
        </button>
      </form>
      <ErrorMessage error={error} />
      {link ? (
        <div className="stack">
          <p>
            {link.company.name} ({link.company.public_id}) · {t(`staff.verify_card_${link.card_type}`, { defaultValue: link.card_type })} →{" "}
            {link.target?.public_id ?? "—"} · <strong>{t(`staff.link_state_${link.state}`, { defaultValue: link.state })}</strong>
          </p>
          <p className="muted small">
            {when(link.created_at)}
            {link.broken_initiator ? ` · ${t(`staff.verify_initiator_${link.broken_initiator}`, { defaultValue: link.broken_initiator })}` : ""}
          </p>
          {link.state === "accepted" || link.state === "pending" ? (
            ending ? (
              <ReasonForm
                label={t("staff.verify_reason")}
                submit={t("staff.verify_link_end")}
                pending={end.pending}
                error={end.error}
                onSubmit={async (reason) => {
                  const done = await end.run({ reason });
                  if (done) {
                    setLink(done.link);
                    setEnding(false);
                  }
                }}
                onCancel={() => setEnding(false)}
              />
            ) : (
              <div className="actions">
                <button type="button" className="danger" onClick={() => setEnding(true)}>
                  {t("staff.verify_link_end")}
                </button>
              </div>
            )
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

function ContactCard() {
  const { t } = useTranslation();
  const [usr, setUsr] = useState("");
  const [shown, setShown] = useState<string | null>(null);
  const contacts = useApi<{ contacts: Contact[] }>(
    ["staff", "verify-contacts", shown],
    `/staff/verification/users/${shown ?? ""}/contacts`,
    shown !== null,
  );
  const [unlinking, setUnlinking] = useState<string | null>(null);
  const unlink = useCommand<{ reason: string }, unknown>(() => `/staff/verification/users/${shown ?? ""}/contacts/${unlinking ?? ""}/unlink`);
  return (
    <Card title={t("staff.verify_contacts")}>
      <p className="muted small">{t("staff.verify_contacts_hint")}</p>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          setShown(usr.trim());
        }}
      >
        <input value={usr} placeholder="USR-…" onChange={(e) => setUsr(e.target.value)} aria-label={t("staff.member_user")} />
        <button type="submit" className="secondary" disabled={!usr.trim()}>
          {t("staff.verify_find")}
        </button>
      </form>
      <ErrorMessage error={contacts.error as ApiError | null} />
      {contacts.data ? (
        <ul className="plain">
          {contacts.data.contacts.map((c) => (
            <li key={c.id} className="stack">
              <span>
                {t(`staff.contact_kind_${c.kind}`, { defaultValue: c.kind })}: {c.value}
                {c.unavailable ? ` · ${t("staff.contact_unavailable")}` : ""}
              </span>
              {unlinking === c.id ? (
                <ReasonForm
                  label={t("staff.verify_reason")}
                  submit={t("staff.verify_unlink")}
                  pending={unlink.pending}
                  error={unlink.error}
                  onSubmit={async (reason) => {
                    if (await unlink.run({ reason })) {
                      setUnlinking(null);
                      void contacts.refetch();
                    }
                  }}
                  onCancel={() => setUnlinking(null)}
                />
              ) : (
                <div className="actions">
                  <button type="button" className="secondary" onClick={() => setUnlinking(c.id)}>
                    {t("staff.verify_unlink")}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
