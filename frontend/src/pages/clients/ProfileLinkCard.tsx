import type { CardType } from "./cardLists";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

import { useApi } from "../../api/queries";
import type { ClientContact, ProfileLink, SharedCompare } from "../../api/types";

/** A contact of the card and how it can carry a request (decision of 25.11.2026). */
type ReachContact = ClientContact & { reach: "profile" | "email" | "none" };
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { deviceTimeZone, formatMoment } from "../../time";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { GivenDataPanel } from "./DataRequests";
import { contactLabel } from "./ContactsEditor";

/** Fields of the snapshot that can be taken into the working data of a card (§ 6.4.2). */
const APPLICABLE: Record<"client" | "counterparty", string[]> & { investor?: string[] } = {
  client: [
    "first_name",
    "last_name",
    "middle_name",
    "birth_date",
    "email",
    "phone",
    "display_name",
    "legal_name",
    "legal_address",
    "actual_address",
    "legal_form",
    "country",
    "document",
    "driver_license",
    "other_documents",
  ],
  counterparty: [
    "first_name",
    "last_name",
    "middle_name",
    "birth_date",
    "email",
    "phone",
    "display_name",
    "legal_name",
    "legal_address",
    "actual_address",
    "identifiers",
    "legal_form",
    "country",
    "document",
    "driver_license",
    "other_documents",
  ],
};

/** The order of the profile data in the card and in the answer. */
const FIELD_ORDER = [
  "first_name",
  "last_name",
  "middle_name",
  "birth_date",
  "display_name",
  "legal_name",
  "legal_form",
  "country",
  "identifiers",
  "legal_address",
  "actual_address",
  "phone",
  "email",
  "document",
  "driver_license",
  "other_documents",
];

export function orderedFields(data: Record<string, unknown>): [string, unknown][] {
  return Object.entries(data).sort(([a], [b]) => FIELD_ORDER.indexOf(a) - FIELD_ORDER.indexOf(b));
}

const DOCUMENT_KINDS = ["passport", "national_id", "other"];

/** A value given by the profile as text; with `t` the type of a document is named (passport, ID
 * card) and the categories of a licence are listed. */
export function sharedValue(value: unknown, t?: (key: string) => string): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) {
    return value
      .map((item: { type?: string; value?: string; title?: string; number?: string }) =>
        // Other documents: «Insurance S-1»; the identifiers of a company: «tax: 123».
        item.number !== undefined
          ? `${item.title ?? ""} ${item.number}`.trim()
          : `${item.type ?? ""}: ${item.value ?? ""}`,
      )
      .join("; ");
  }
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => {
        if (key === "type" && t && DOCUMENT_KINDS.includes(item as string)) return t(`cards.document_${item}`);
        if (Array.isArray(item)) return item.join(", ");
        return item;
      })
      .filter((item) => item !== "" && item !== null && item !== undefined)
      .join(" · ");
  }
  return String(value);
}

/** Link of a card with a c4rsys profile (§ 6.2.5–6.3, § 6.4, § 6.6): the request by ID, email or
 * phone, its state, the cancel, the data given by the recipient and the break. Without a link a
 * reminder is shown (§ 6.3.5). */
export function ProfileLinkCard({
  cardType,
  cardId,
  cardKind,
  version,
  onChanged,
}: {
  cardType: CardType;
  cardId: string;
  cardKind: "person" | "company";
  version: number;
  onChanged: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const formatDateTime = (value: string) =>
    formatMoment(value, company.timezone || deviceTimeZone(), i18n.language);
  const queryClient = useQueryClient();
  const base = `/c/${company.public_id}/profile-links`;
  // The version is in the key: the contacts are checked again after the card is changed.
  const key = ["profile-links", company.public_id, cardType, cardId, version];
  const links = useApi<{ links: ProfileLink[]; contacts?: ReachContact[] }>(
    key,
    `${base}?card_type=${cardType}&card_id=${cardId}`,
  );
  const send = useCommand<{ card_type: string; card_id: string; target: string }, unknown>(base);
  const cancel = useCommand<Record<string, never>, unknown>(
    () => `${base}/${links.data?.links[0]?.id}/cancel`,
  );
  const breakLink = useCommand<{ initiator: string; revoke: boolean; reason: string }, unknown>(
    () => `${base}/${links.data?.links[0]?.id}/break`,
  );
  // The break is started by a button beside the state; then one reason of three is chosen
  // (customer's decision of 26.11.2026). «Correction of an error» takes back the access to the
  // history shown by mistake (§ 6.5.5) and needs an explanation and the right of its own.
  const [breaking, setBreaking] = useState(false);
  const [breakReason, setBreakReason] = useState<"company" | "client" | "error">("company");
  const [revokeReason, setRevokeReason] = useState("");
  const apply = useCommand<{ field: string; expected_version: number }, unknown>(
    () => `${base}/${links.data?.links[0]?.id}/apply`,
  );
  const [target, setTarget] = useState("");
  // A person is asked by a contact of the card confirmed in a profile — the request comes into the
  // system; without one, by a letter to an email; or by the ID of the profile (customer's
  // decisions of 25.11.2026). A company by its CMP ID.
  const reach = cardKind === "person" ? (links.data?.contacts ?? []) : [];
  const sendable = reach.filter((c) => c.reach !== "none");
  const confirmed = reach.some((c) => c.reach === "profile");
  const [picked, setPicked] = useState<string | null>(null);
  const first = reach.findIndex((c) => c.reach === "profile");
  const fallback = first >= 0 ? first : reach.findIndex((c) => c.reach === "email");
  const choice = picked ?? (fallback >= 0 ? String(fallback) : "id");
  const chosen = choice === "id" ? undefined : reach[Number(choice)];
  const sending = chosen ? chosen.value : target.trim();
  // The request is started by a button; the field appears after it (decision of 15.11.2026).
  const [asking, setAsking] = useState(false);
  const editable = can(`${cardType}.edit`);
  // § 23.8.2 (block 14): sending an invitation or a request to link is a right of its own.
  const requestable = can(`${cardType}.link.request`);

  // The card of an investor takes the same data as the card of a counterparty.
  const applicable = APPLICABLE[cardType === "investor" ? "counterparty" : cardType];

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: key });
    await onChanged();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if ((await send.run({ card_type: cardType, card_id: cardId, target: sending })) !== null) {
      setTarget("");
      setPicked(null);
      setAsking(false);
      await refresh();
    }
  }

  // A notification of the answer leads straight here (customer's remark of 25.11.2026).
  const anchor = useRef<HTMLDivElement>(null);
  const { hash } = useLocation();
  const loaded = Boolean(links.data);
  useEffect(() => {
    if (hash === "#profile-link" && loaded) anchor.current?.scrollIntoView({ block: "start" });
  }, [hash, loaded]);

  const last = links.data?.links[0];
  const state = last?.state;
  const open = state === "pending" || state === "accepted";
  const shown = last && (state === "accepted" || state === "broken") ? last : undefined;

  return (
    <div id="profile-link" ref={anchor}>
    <Card title={t("links.title")}>
      {!last || !open ? <p className="muted">{t("links.not_linked")}</p> : null}
      {last ? (
        <div className="stack">
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <span>
              <span className={`badge ${state === "accepted" ? "ok" : state === "pending" ? "warn" : ""}`}>
                {t(`links.state_${state}`)}
              </span>{" "}
              {shown?.linked_profile
                ? `${shown.linked_profile.name} (${shown.linked_profile.public_id})`
                : last.target}
            </span>
            {state === "accepted" && editable && !breaking ? (
              <button type="button" className="secondary small" data-testid="break-link" onClick={() => setBreaking(true)}>
                {t("links.break")}
              </button>
            ) : null}
          </div>
          {state === "accepted" && editable && breaking ? (
            <div className="stack" data-testid="break-form" style={{ maxWidth: 420 }}>
              <Field label={t("links.break_reason")}>
                <select value={breakReason} onChange={(e) => setBreakReason(e.target.value as typeof breakReason)}>
                  <option value="company">{t("links.reason_company")}</option>
                  <option value="client">{t("links.reason_client")}</option>
                  {can(`${cardType}.split`) ? <option value="error">{t("links.reason_error")}</option> : null}
                </select>
              </Field>
              {breakReason === "error" ? (
                <Field label={t("links.revoke_reason")}>
                  <input value={revokeReason} onChange={(e) => setRevokeReason(e.target.value)} />
                </Field>
              ) : null}
              <p className="muted small">{t(breakReason === "error" ? "links.break_hint_error" : "links.break_hint")}</p>
              <div className="actions">
                <button
                  type="button"
                  className="small"
                  disabled={breakLink.pending || (breakReason === "error" && !revokeReason.trim())}
                  onClick={async () => {
                    const error = breakReason === "error";
                    const body = {
                      initiator: breakReason === "client" ? "client" : "company",
                      revoke: error,
                      reason: error ? revokeReason : "",
                    };
                    if ((await breakLink.run(body)) !== null) {
                      setBreaking(false);
                      setBreakReason("company");
                      setRevokeReason("");
                      await refresh();
                    }
                  }}
                >
                  {t("links.break")}
                </button>
                <button type="button" className="secondary small" onClick={() => setBreaking(false)}>
                  {t("common.cancel")}
                </button>
              </div>
            </div>
          ) : null}
          <p className="muted small">
            {t("links.sent_at", { at: formatDateTime(last.created_at) })}
            {state === "pending" ? ` · ${t("links.expires_at", { at: formatDateTime(last.expires_at) })}` : ""}
            {last.decided_at ? ` · ${t("links.decided_at", { at: formatDateTime(last.decided_at) })}` : ""}
            {last.broken_at
              ? ` · ${t("links.broken_at", { at: formatDateTime(last.broken_at) })} (${t(`links.initiator_${last.broken_initiator}`)})`
              : ""}
          </p>
          {state === "pending" && editable ? (
            <div className="actions">
              <button
                type="button"
                className="secondary"
                disabled={cancel.pending}
                onClick={async () => {
                  if ((await cancel.run({})) !== null) await refresh();
                }}
              >
                {t("links.cancel")}
              </button>
            </div>
          ) : null}
          {shown && Object.keys(shown.shared).length ? (
            <>
              <h3>{t("links.shared_title")}</h3>
              <p className="muted small">{t("links.shared_hint")}</p>
              <SharedTable
                data={shown.shared}
                compare={shown.compare}
                applicable={editable ? applicable : []}
                pending={apply.pending}
                onApply={async (field) => {
                  if ((await apply.run({ field, expected_version: version })) !== null) await refresh();
                }}
              />
            </>
          ) : null}
          {state === "accepted" ? (
            <GivenDataPanel
              linkId={last.id}
              editable={editable}
              applicable={applicable}
              version={version}
              onApplied={refresh}
            />
          ) : null}
          {last.access_revoked_at ? (
            <p className="alert warn small">{t("links.access_revoked", { reason: last.access_revoke_reason })}</p>
          ) : null}
        </div>
      ) : null}
      {!open && requestable && !asking ? (
        <div className="actions">
          <button type="button" className="secondary" onClick={() => setAsking(true)}>
            {t("links.start")}
          </button>
        </div>
      ) : null}
      {!open && requestable && asking ? (
        <form className="stack" onSubmit={submit}>
          {reach.length > 0 ? (
            <Field label={t("links.send_to")}>
              <select value={choice} onChange={(e) => setPicked(e.target.value)}>
                {reach.map((c, i) => (
                  <option key={`${c.kind}${c.messenger ?? ""}${c.value}`} value={String(i)} disabled={c.reach === "none"}>
                    {`${contactLabel(t, c)}: ${c.value} — ${t(`links.reach_${c.reach}`)}`}
                  </option>
                ))}
                <option value="id">{t("links.send_to_id")}</option>
              </select>
            </Field>
          ) : null}
          {choice === "id" ? (
            <Field label={t(cardKind === "company" ? "links.target_company" : "links.target_id")}>
              <input value={target} onChange={(e) => setTarget(e.target.value)} />
            </Field>
          ) : null}
          {reach.length > 0 && !confirmed ? (
            <p className="muted small">{t(sendable.length > 0 ? "links.none_confirmed" : "links.none_confirmed_no_email")}</p>
          ) : null}
          <p className="muted small">{t(cardKind === "company" ? "links.hint_company" : "links.hint_person")}</p>
          <div className="actions">
            <button type="submit" disabled={!sending || send.pending}>
              {t("links.send")}
            </button>
            <button type="button" className="secondary" onClick={() => setAsking(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      ) : null}
      <ErrorMessage error={send.error ?? cancel.error ?? breakLink.error ?? apply.error} />
    </Card>
    </div>
  );
}

/** The values given by a profile beside what the card holds now, so it is seen what is new, what
 * is the same and what differs before taking a value; a taken value shows «In the card»
 * (customer's remark of 25.11.2026). */
export function SharedTable({
  data,
  compare,
  applicable,
  pending,
  onApply,
}: {
  data: Record<string, unknown>;
  compare: Record<string, SharedCompare>;
  applicable: string[];
  pending: boolean;
  onApply: (field: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  return (
    <table className="table" data-testid="shared-table">
      <thead>
        <tr>
          <th />
          <th>{t("links.from_profile")}</th>
          <th>{t("links.in_card")}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {orderedFields(data).map(([field, value]) => {
          const found = compare[field];
          const status = found?.status ?? "empty";
          const contact = field === "email" || field === "phone";
          return (
            <tr key={field}>
              <td>{t(`links.field_${field}`)}</td>
              <td>{sharedValue(value, t)}</td>
              <td className={status === "different" ? "" : "muted"}>
                {status === "absent" ? t("links.no_such_field") : found?.current ? sharedValue(found.current, t) : "—"}
              </td>
              <td>
                {status === "same" ? (
                  <span className="badge ok" style={{ whiteSpace: "nowrap" }}>{t("links.taken")}</span>
                ) : status !== "absent" && applicable.includes(field) ? (
                  <button type="button" className="secondary small" disabled={pending} onClick={() => void onApply(field)}>
                    {t(status === "empty" ? "links.apply" : contact ? "links.apply_add" : "links.apply_replace")}
                  </button>
                ) : null}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
