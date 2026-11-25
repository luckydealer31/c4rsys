import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { ClientContact, Messenger } from "../../api/types";
import { IconButton } from "../../components/ui";

/** The contacts of a card (customer's decisions of 25.11.2026): «Add a contact» — the kind is
 * chosen first from «Choose» (an email, a phone, a messenger), then its value is entered; a
 * messenger is a contact of its own, by a number or a nickname. Any one contact is enough for a
 * card. The block is titled «Contacts», so the fields carry no labels of their own (customer's
 * remark of 25.11.2026). */
export const CONTACT_CHOICES = [
  "phone",
  "email",
  "whatsapp",
  "telegram",
  "line",
  "viber",
  "wechat",
  "signal",
  "kakaotalk",
  "zalo",
  "facebook_messenger",
  "other",
] as const;

/** A row of the editor; `choice` is empty while the kind is not chosen yet. */
export type ContactRow = { choice: string; value: string };

export const EMPTY_CONTACT: ContactRow = { choice: "", value: "" };

export function rowsOf(contacts: ClientContact[]): ContactRow[] {
  return contacts.map((c) => ({ choice: c.kind === "messenger" ? (c.messenger ?? "other") : c.kind, value: c.value }));
}

export function contactsOf(rows: ContactRow[]): ClientContact[] {
  return rows
    .filter((r) => r.choice && r.value.trim())
    .map((r) =>
      r.choice === "phone" || r.choice === "email"
        ? { kind: r.choice, value: r.value.trim() }
        : { kind: "messenger", value: r.value.trim(), messenger: r.choice as Messenger },
    );
}

/** The first phone and the first email among the rows: the matches are looked for by them. */
export function phoneAndEmail(rows: ContactRow[]): { phone: string; email: string } {
  const phone = rows.find((r) => r.choice === "phone" && r.value.trim())?.value.trim() ?? "";
  const email = rows.find((r) => r.choice === "email" && r.value.trim())?.value.trim() ?? "";
  return { phone, email };
}

export function contactLabel(t: (key: string) => string, c: ClientContact): string {
  if (c.kind === "phone") return t("clients.phone");
  if (c.kind === "email") return t("auth.email");
  return t(`clients.messenger_${c.messenger || "other"}`);
}

function choiceLabel(t: (key: string) => string, choice: string): string {
  if (choice === "phone") return t("clients.phone");
  if (choice === "email") return t("auth.email");
  return t(`clients.messenger_${choice}`);
}

const PHONE = /^\+?\d{5,15}$/;
const EMAIL = /^[^\s@]+@[^\s@.]+(\.[^\s@.]+)*\.[A-Za-z]{2,}$/;
const NICKNAME = /^@?[\w.-]{2,64}$/;

/** A phone is typed with digits only (and a leading «+»). */
function phoneInput(value: string): string {
  const plus = value.trimStart().startsWith("+");
  return (plus ? "+" : "") + value.replace(/\D/g, "");
}

/** Whether the value looks like the chosen kind: a phone of digits, an email with a domain
 * (name@site.com), a messenger by a number or a nickname. Checked again by the server. */
export function contactValid(row: ContactRow): boolean {
  const value = row.value.trim();
  if (!value || !row.choice) return true;
  if (row.choice === "phone") return PHONE.test(value);
  if (row.choice === "email") return EMAIL.test(value);
  return PHONE.test(value.replace(/[\s\-().]/g, "")) || NICKNAME.test(value);
}

export function ContactsEditor({
  rows,
  onChange,
  titled = true,
}: {
  rows: ContactRow[];
  onChange: (rows: ContactRow[]) => void;
  /** «Contacts» above the rows; the step of the wizard has it in its own title. */
  titled?: boolean;
}) {
  const { t } = useTranslation();
  const [touched, setTouched] = useState<number[]>([]);
  const shown = rows.length > 0 ? rows : [EMPTY_CONTACT];
  const change = (index: number, row: Partial<ContactRow>) =>
    onChange(shown.map((r, i) => (i === index ? { ...r, ...row } : r)));
  return (
    <div className="stack" data-testid="contacts-editor">
      {titled ? <span className="muted small">{t("cards.field_contacts")}</span> : null}
      {shown.map((row, index) => {
        const wrong = touched.includes(index) && !contactValid(row);
        const kind = row.choice === "phone" || row.choice === "email" ? row.choice : "messenger";
        return (
          <div key={index}>
            <div className="row" style={{ alignItems: "center", flexWrap: "wrap" }}>
              <select
                style={{ flex: "0 1 190px" }}
                aria-label={t("clients.contact_kind")}
                value={row.choice}
                onChange={(e) => change(index, { choice: e.target.value })}
              >
                <option value="">{t("clients.contact_choose")}</option>
                {CONTACT_CHOICES.map((choice) => (
                  <option key={choice} value={choice}>
                    {choiceLabel(t, choice)}
                  </option>
                ))}
              </select>
              <input
                style={{ flex: "0 1 260px" }}
                aria-label={row.choice ? t(`clients.contact_value_${kind}`) : t("clients.contact_kind")}
                placeholder={row.choice ? t(`clients.contact_placeholder_${kind}`) : ""}
                disabled={!row.choice}
                type={row.choice === "email" ? "email" : row.choice === "phone" ? "tel" : "text"}
                inputMode={row.choice === "phone" ? "tel" : row.choice === "email" ? "email" : undefined}
                value={row.value}
                aria-invalid={wrong || undefined}
                onBlur={() => setTouched((list) => (list.includes(index) ? list : [...list, index]))}
                onChange={(e) => change(index, { value: row.choice === "phone" ? phoneInput(e.target.value) : e.target.value })}
              />
              {shown.length > 1 ? (
                <IconButton
                  icon="trash"
                  label={t("clients.contact_remove")}
                  onClick={() => {
                    setTouched([]);
                    onChange(shown.filter((_, i) => i !== index));
                  }}
                />
              ) : null}
            </div>
            {wrong ? (
              <span className="small" style={{ color: "var(--danger)" }}>
                {t(`clients.contact_wrong_${kind}`)}
              </span>
            ) : null}
          </div>
        );
      })}
      <div>
        <button type="button" className="secondary small" onClick={() => onChange([...shown, EMPTY_CONTACT])}>
          + {t("clients.contact_add")}
        </button>
      </div>
    </div>
  );
}
