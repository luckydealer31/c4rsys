import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { ProfileLink } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { deviceTimeZone, formatMoment } from "../../time";
import { useCan, useCompany } from "../../layout/CompanyShell";

/** Fields of the snapshot that can be taken into the working data of a card (§ 6.4.2). */
const APPLICABLE: Record<"client" | "counterparty", string[]> = {
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
  ],
  counterparty: [
    "first_name",
    "last_name",
    "middle_name",
    "email",
    "phone",
    "display_name",
    "legal_name",
    "legal_address",
    "actual_address",
    "identifiers",
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
];

export function orderedFields(data: Record<string, unknown>): [string, unknown][] {
  return Object.entries(data).sort(([a], [b]) => FIELD_ORDER.indexOf(a) - FIELD_ORDER.indexOf(b));
}

export function sharedValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) {
    return value
      .map((item: { type?: string; value?: string }) => `${item.type ?? ""}: ${item.value ?? ""}`)
      .join("; ");
  }
  if (typeof value === "object") {
    return Object.values(value as Record<string, unknown>)
      .filter(Boolean)
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
  cardType: "client" | "counterparty";
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
  const key = ["profile-links", company.public_id, cardType, cardId];
  const links = useApi<{ links: ProfileLink[] }>(key, `${base}?card_type=${cardType}&card_id=${cardId}`);
  const send = useCommand<{ card_type: string; card_id: string; target: string }, unknown>(base);
  const cancel = useCommand<Record<string, never>, unknown>(
    () => `${base}/${links.data?.links[0]?.id}/cancel`,
  );
  const breakLink = useCommand<{ initiator: string }, unknown>(
    () => `${base}/${links.data?.links[0]?.id}/break`,
  );
  const apply = useCommand<{ field: string; expected_version: number }, unknown>(
    () => `${base}/${links.data?.links[0]?.id}/apply`,
  );
  const [target, setTarget] = useState("");
  const [initiator, setInitiator] = useState("company");
  const editable = can(cardType === "client" ? "client.edit" : "counterparty.edit");

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: key });
    await onChanged();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if ((await send.run({ card_type: cardType, card_id: cardId, target })) !== null) {
      setTarget("");
      await refresh();
    }
  }

  const last = links.data?.links[0];
  const state = last?.state;
  const open = state === "pending" || state === "accepted";
  const shown = last && (state === "accepted" || state === "broken") ? last : undefined;

  return (
    <Card title={t("links.title")}>
      {!last || !open ? <p className="muted">{t("links.not_linked")}</p> : null}
      {last ? (
        <div className="stack">
          <p>
            <span className={`badge ${state === "accepted" ? "ok" : state === "pending" ? "warn" : ""}`}>
              {t(`links.state_${state}`)}
            </span>{" "}
            {shown?.linked_profile
              ? `${shown.linked_profile.name} (${shown.linked_profile.public_id})`
              : last.target}
          </p>
          <p className="muted small">
            {t("links.sent_at", { at: formatDateTime(last.created_at) })}
            {state === "pending" ? ` · ${t("links.expires_at", { at: formatDateTime(last.expires_at) })}` : ""}
            {last.decided_at ? ` · ${t("links.decided_at", { at: formatDateTime(last.decided_at) })}` : ""}
            {last.broken_at
              ? ` · ${t("links.broken_at", { at: formatDateTime(last.broken_at) })} (${t(`links.initiator_${last.broken_initiator}`)})`
              : ""}
          </p>
          {state === "pending" && last.by_phone ? <p className="muted small">{t("links.no_sms")}</p> : null}
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
              <table className="table">
                <tbody>
                  {orderedFields(shown.shared).map(([field, value]) => (
                    <tr key={field}>
                      <td>{t(`links.field_${field}`)}</td>
                      <td>{sharedValue(value)}</td>
                      <td>
                        {editable && APPLICABLE[cardType].includes(field) ? (
                          <button
                            type="button"
                            className="secondary small"
                            disabled={apply.pending}
                            onClick={async () => {
                              if ((await apply.run({ field, expected_version: version })) !== null) await refresh();
                            }}
                          >
                            {t("links.apply")}
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
          {state === "accepted" && editable ? (
            <div className="row">
              <Field label={t("links.initiator")}>
                <select value={initiator} onChange={(e) => setInitiator(e.target.value)}>
                  <option value="company">{t("links.initiator_company")}</option>
                  <option value="client">{t("links.initiator_client")}</option>
                </select>
              </Field>
              <div className="actions">
                <button
                  type="button"
                  className="secondary"
                  disabled={breakLink.pending}
                  onClick={async () => {
                    if (!window.confirm(t("links.break_confirm"))) return;
                    if ((await breakLink.run({ initiator })) !== null) await refresh();
                  }}
                >
                  {t("links.break")}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
      {!open && editable ? (
        <form className="stack" onSubmit={submit}>
          <Field label={t(cardKind === "company" ? "links.target_company" : "links.target_person")}>
            <input value={target} onChange={(e) => setTarget(e.target.value)} />
          </Field>
          <p className="muted small">{t(cardKind === "company" ? "links.hint_company" : "links.hint_person")}</p>
          <div className="actions">
            <button type="submit" disabled={!target.trim() || send.pending}>
              {t("links.send")}
            </button>
          </div>
        </form>
      ) : null}
      <ErrorMessage error={send.error ?? cancel.error ?? breakLink.error ?? apply.error} />
    </Card>
  );
}
