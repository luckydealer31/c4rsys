/** The parts of a card of a client or a counterparty (customer's decisions of 15.11.2026).
 *
 * A card looks like «About the company»: what is filled is shown as text, and nothing can be
 * changed by a stray key — the data are edited in a window opened by the pencil, with the
 * captions inside the fields. The documents of a person (the driving licence first: the system is
 * about vehicles) and the requisites of a company stand in a card of their own. */
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type {
  Client,
  ClientContact,
  ClientHistory,
  Counterparty,
  DriverLicense,
  ExtendedCard,
  IdDocument,
  Messenger,
  OtherDocument,
} from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, FloatField, IconButton, Loading, Modal } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";

export type CardType = "client" | "counterparty";
type AnyCard = (Client | Counterparty) & ExtendedCard;

export const MESSENGERS: Messenger[] = [
  "",
  "whatsapp",
  "telegram",
  "line",
  "wechat",
  "viber",
  "signal",
  "kakaotalk",
  "zalo",
  "facebook_messenger",
  "other",
];

/** The categories of the international driving permit (PROPOSAL): they are marked in a list. */
export const LICENSE_CATEGORIES = ["AM", "A1", "A2", "A", "B1", "B", "C1", "C", "D1", "D", "BE", "C1E", "CE", "D1E", "DE"];

type Row = [label: string, value: ReactNode];

/** Label — value, the filled ones only. */
export function Facts({ rows }: { rows: Row[] }) {
  const filled = rows.filter(([, value]) => value !== "" && value !== null && value !== undefined && value !== false);
  if (filled.length === 0) return null;
  return (
    <dl className="facts">
      {filled.map(([label, value], index) => (
        <div key={index}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Head({ title, edit }: { title: ReactNode; edit?: { label: string; onClick: () => void } | null }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
      <h2 style={{ margin: 0 }}>{title}</h2>
      {edit ? <IconButton icon="edit" label={edit.label} onClick={edit.onClick} /> : null}
    </div>
  );
}

function path(cardType: CardType) {
  return cardType === "client" ? "clients" : "counterparties";
}

function useEditable(cardType: CardType, card: AnyCard) {
  const can = useCan();
  return can(`${cardType}.edit`) && (cardType === "client" ? card.state !== "merged" : card.state === "active");
}

function dates(from: string | null, to: string | null, t: (key: string) => string): string {
  return [from ? `${t("cards.valid_from")} ${from}` : "", to ? `${t("cards.valid_to")} ${to}` : ""].filter(Boolean).join(" ");
}

// --- The main data --------------------------------------------------------------------------

type MainDraft = {
  first_name: string;
  last_name: string;
  middle_name: string;
  company_name: string;
  phone: string;
  phone_messenger: Messenger;
  email: string;
  registration_address: string;
  residence_address: string;
  comment: string;
  address: string;
  external_id: string;
  registration: string;
  category: string;
};

function draftOf(card: AnyCard): MainDraft {
  const phone = card.contacts.find((c) => c.kind === "phone");
  return {
    first_name: card.first_name,
    last_name: card.last_name,
    middle_name: card.middle_name,
    company_name: card.company_name,
    phone: phone?.value ?? "",
    phone_messenger: phone?.messenger ?? "",
    email: card.contacts.find((c) => c.kind === "email")?.value ?? "",
    registration_address: card.registration_address,
    residence_address: card.residence_address,
    comment: card.comment,
    address: card.address,
    external_id: card.external_id,
    registration: "registration" in card ? card.registration : "",
    category: "category" in card ? card.category : "",
  };
}

/** Contacts beyond the first phone and the first email, which the form edits. */
function furtherContacts(contacts: ClientContact[]): ClientContact[] {
  const firstPhone = contacts.find((c) => c.kind === "phone");
  const firstEmail = contacts.find((c) => c.kind === "email");
  return contacts.filter((c) => c !== firstPhone && c !== firstEmail);
}

/** The main card: the name, the contacts, the addresses and the comment; edited in a window. */
export function MainCard({
  cardType,
  card,
  badges,
  onSaved,
}: {
  cardType: CardType;
  card: AnyCard;
  badges?: ReactNode;
  onSaved: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const editable = useEditable(cardType, card);
  const [editing, setEditing] = useState(false);
  const person = card.kind === "person";
  const phone = card.contacts.find((c) => c.kind === "phone");
  const email = card.contacts.find((c) => c.kind === "email");
  const rows: Row[] = [
    person && "birth_date" in card && card.birth_date ? [t("clients.birth_date"), card.birth_date] : ["", ""],
    [
      t("clients.phone"),
      phone ? `${phone.value}${phone.messenger ? ` · ${t(`clients.messenger_${phone.messenger}`)}` : ""}` : "",
    ],
    [t("auth.email"), email?.value ?? ""],
    ...furtherContacts(card.contacts).map(
      (c): Row => [
        c.kind === "phone" ? t("clients.phone") : t("auth.email"),
        `${c.value}${c.messenger ? ` · ${t(`clients.messenger_${c.messenger}`)}` : ""}`,
      ],
    ),
    person ? [t("cards.field_registration_address"), card.registration_address] : ["", ""],
    person ? [t("cards.field_residence_address"), card.residence_address] : ["", ""],
    // The single address of the old form: shown while it is filled (for example from an import).
    [t("cards.field_address"), card.address],
    "category" in card ? [t("cards.field_category"), card.category] : ["", ""],
    "registration" in card ? [t("cards.field_registration"), card.registration] : ["", ""],
    [t("cards.field_comment"), card.comment],
  ];
  return (
    <div style={{ maxWidth: 760 }}>
      <Card>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0 }}>{card.name}</h1>
          {editable ? <IconButton icon="edit" label={t("common.edit")} onClick={() => setEditing(true)} /> : null}
          {badges}
        </div>
        <div className="muted" style={{ marginBottom: 16 }}>
          {t(`clients.kind_${card.kind}`)}
        </div>
        <Facts rows={rows} />
      </Card>
      {editing ? (
        <Modal wide title={card.name} onClose={() => setEditing(false)}>
          <MainForm
            cardType={cardType}
            card={card}
            onDone={async (saved) => {
              setEditing(false);
              if (saved) await onSaved();
            }}
          />
        </Modal>
      ) : null}
    </div>
  );
}

function MainForm({ cardType, card, onDone }: { cardType: CardType; card: AnyCard; onDone: (saved: boolean) => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const update = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/${path(cardType)}/${card.id}`, "PATCH");
  const [draft, setDraft] = useState<MainDraft>(draftOf(card));
  const person = card.kind === "person";
  const text = (key: keyof MainDraft, label: string, area = false) => (
    <FloatField label={label}>
      {area ? (
        <textarea value={draft[key]} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
      ) : (
        <input value={draft[key]} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
      )}
    </FloatField>
  );
  async function submit(event: FormEvent) {
    event.preventDefault();
    const contacts: ClientContact[] = [];
    if (draft.phone.trim()) contacts.push({ kind: "phone", value: draft.phone, messenger: draft.phone_messenger });
    if (draft.email.trim()) contacts.push({ kind: "email", value: draft.email });
    // Further phones and emails (from an import) are kept as they are.
    contacts.push(...furtherContacts(card.contacts));
    const body: Record<string, unknown> = {
      ...(person
        ? {
            first_name: draft.first_name,
            last_name: draft.last_name,
            middle_name: draft.middle_name,
            registration_address: draft.registration_address,
            residence_address: draft.residence_address,
          }
        : { company_name: draft.company_name }),
      contacts,
      comment: draft.comment,
      address: draft.address,
      external_id: draft.external_id,
      ...(cardType === "counterparty" ? { registration: draft.registration, category: draft.category } : {}),
      expected_version: card.version,
    };
    if ((await update.run(body)) !== null) await onDone(true);
  }
  return (
    <form className="stack" onSubmit={submit}>
      <div className="form-grid">
        {person ? (
          <>
            {text("last_name", t("profile.last_name"))}
            {text("first_name", t("profile.first_name"))}
            {text("middle_name", t("profile.middle_name"))}
          </>
        ) : (
          text("company_name", t("clients.company_name"))
        )}
        {text("email", t("auth.email"))}
        {text("phone", t("clients.phone"))}
        <FloatField label={t("clients.messenger")}>
          <select value={draft.phone_messenger} onChange={(e) => setDraft({ ...draft, phone_messenger: e.target.value as Messenger })}>
            {MESSENGERS.map((m) => (
              <option key={m} value={m}>
                {t(`clients.messenger_${m || "none"}`)}
              </option>
            ))}
          </select>
        </FloatField>
        {person ? (
          <>
            {text("registration_address", t("cards.field_registration_address"))}
            {text("residence_address", t("cards.field_residence_address"))}
          </>
        ) : null}
        {cardType === "counterparty" ? (
          <>
            {text("category", t("cards.field_category"))}
            {text("registration", t("cards.field_registration"))}
          </>
        ) : null}
        {/* The single address of the old form is offered only while it is filled. */}
        {card.address ? text("address", t("cards.field_address")) : null}
      </div>
      {text("comment", t("cards.field_comment"), true)}
      <ErrorMessage error={update.error} />
      <div className="actions">
        <button type="submit" disabled={update.pending}>
          {t("common.save")}
        </button>
        <button type="button" className="secondary" onClick={() => void onDone(false)}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

// --- Documents of a person, requisites of a company -----------------------------------------

const COMPANY_TEXTS = [
  "country",
  "legal_form",
  "registration_number",
  "tax_number",
  "legal_address",
  "actual_address",
  "director_name",
  "contact_person",
  "signatory_name",
  "signatory_basis",
  "bank_details",
] as const;

const NO_DOCUMENT: IdDocument = { type: "passport", number: "", country: "", issued_on: null, expires_on: null };
const NO_LICENSE: DriverLicense = { number: "", country: "", issued_on: null, expires_on: null, categories: [] };
const NO_OTHER: OtherDocument = { title: "", number: "", issued_on: null, expires_on: null, issued_by: "", note: "" };

/** § 6.2.12–6.2.13: the documents of a person — the driving licence, the passport or ID, other
 * documents; for a company — its requisites. None is required. */
export function DocumentsCard({ cardType, card, onSaved }: { cardType: CardType; card: AnyCard; onSaved: () => Promise<void> }) {
  const { t } = useTranslation();
  const editable = useEditable(cardType, card);
  const [editing, setEditing] = useState(false);
  const person = card.kind === "person";
  const title = t(person ? "cards.documents_title" : "cards.requisites_title");
  const license = card.driver_license;
  const doc = card.id_document;
  const rows: Row[] = person
    ? [
        [
          t("cards.driver_license"),
          license
            ? [
                license.number,
                license.categories.length ? `${t("cards.categories_short")}: ${license.categories.join(", ")}` : "",
                dates(license.issued_on, license.expires_on, t),
              ]
                .filter(Boolean)
                .join(" · ")
            : "",
        ],
        [t("cards.driving_since"), card.driving_since ? card.driving_since.slice(0, 4) : ""],
        [
          doc ? t(`cards.document_${doc.type}`) : t("cards.id_document"),
          doc ? [doc.number, doc.country, dates(doc.issued_on, doc.expires_on, t)].filter(Boolean).join(" · ") : "",
        ],
        ...card.other_documents.map(
          (d): Row => [
            d.title,
            [d.number, dates(d.issued_on, d.expires_on, t), d.issued_by ? `${t("cards.issued_by")}: ${d.issued_by}` : ""]
              .filter(Boolean)
              .join(" · ") || "—",
          ],
        ),
      ]
    : COMPANY_TEXTS.map((name): Row => [t(`cards.field_${name}`), card[name]]);
  const empty = rows.every(([, value]) => !value);
  return (
    <div style={{ maxWidth: 760 }}>
      <Card>
        <Head title={title} edit={editable ? { label: `${t("common.edit")}: ${title}`, onClick: () => setEditing(true) } : null} />
        {empty ? <p className="muted">{t("cards.documents_none")}</p> : <Facts rows={rows} />}
      </Card>
      {editing ? (
        <Modal wide title={title} onClose={() => setEditing(false)}>
          <DocumentsForm
            cardType={cardType}
            card={card}
            onDone={async (saved) => {
              setEditing(false);
              if (saved) await onSaved();
            }}
          />
        </Modal>
      ) : null}
    </div>
  );
}

function DocumentsForm({ cardType, card, onDone }: { cardType: CardType; card: AnyCard; onDone: (saved: boolean) => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const update = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/${path(cardType)}/${card.id}`, "PATCH");
  const person = card.kind === "person";
  const [texts, setTexts] = useState<Record<string, string>>(Object.fromEntries(COMPANY_TEXTS.map((n) => [n, card[n] ?? ""])));
  const [doc, setDoc] = useState<IdDocument>(card.id_document ?? NO_DOCUMENT);
  const [license, setLicense] = useState<DriverLicense>(card.driver_license ?? NO_LICENSE);
  // The experience is asked as a year: few remember the day they got the licence.
  const [since, setSince] = useState(card.driving_since ? card.driving_since.slice(0, 4) : "");
  const [others, setOthers] = useState<OtherDocument[]>(card.other_documents.map((d) => ({ ...NO_OTHER, ...d })));
  // Categories written before the list (for example from an import) stay available.
  const categories = [...LICENSE_CATEGORIES, ...license.categories.filter((c) => !LICENSE_CATEGORIES.includes(c))];
  const thisYear = new Date().getFullYear();

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = { expected_version: card.version };
    if (person) {
      body.driver_license = license.number.trim() ? license : null;
      body.id_document = doc.number.trim() ? doc : null;
      // The day of the year the card already has is kept when the year is the same.
      body.driving_since = since
        ? card.driving_since?.startsWith(since)
          ? card.driving_since
          : `${since}-01-01`
        : null;
      body.other_documents = others.filter((d) => d.title.trim());
    } else {
      Object.assign(body, texts);
    }
    if ((await update.run(body)) !== null) await onDone(true);
  }
  const date = (label: string, value: string | null, onChange: (v: string | null) => void) => (
    <FloatField label={label}>
      <input type="date" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} />
    </FloatField>
  );
  const other = (index: number, change: Partial<OtherDocument>) =>
    setOthers(others.map((x, i) => (i === index ? { ...x, ...change } : x)));

  return (
    <form className="stack" onSubmit={submit}>
      {person ? (
        <>
          <h3>{t("cards.driver_license")}</h3>
          <div className="form-grid">
            <FloatField label={t("cards.series_number")}>
              <input value={license.number} onChange={(e) => setLicense({ ...license, number: e.target.value })} />
            </FloatField>
            <FloatField label={t("cards.driving_since_year")}>
              <input
                type="number"
                inputMode="numeric"
                min={thisYear - 90}
                max={thisYear}
                value={since}
                onChange={(e) => setSince(e.target.value.replace(/\D/g, "").slice(0, 4))}
              />
            </FloatField>
            {date(t("cards.issued_on"), license.issued_on, (v) => setLicense({ ...license, issued_on: v }))}
            {date(t("cards.expires_on"), license.expires_on, (v) => setLicense({ ...license, expires_on: v }))}
          </div>
          <fieldset className="plain">
            <legend className="muted small">{t("cards.categories")}</legend>
            <div className="chips">
              {categories.map((code) => (
                <label key={code} className="chip">
                  <input
                    type="checkbox"
                    checked={license.categories.includes(code)}
                    onChange={(e) =>
                      setLicense({
                        ...license,
                        categories: e.target.checked
                          ? categories.filter((c) => c === code || license.categories.includes(c))
                          : license.categories.filter((c) => c !== code),
                      })
                    }
                  />
                  {code}
                </label>
              ))}
            </div>
          </fieldset>

          <h3>{t("cards.id_document")}</h3>
          <div className="form-grid">
            <FloatField label={t("cards.document_type")}>
              <select value={doc.type} onChange={(e) => setDoc({ ...doc, type: e.target.value as IdDocument["type"] })}>
                {(["passport", "national_id", "other"] as const).map((v) => (
                  <option key={v} value={v}>
                    {t(`cards.document_${v}`)}
                  </option>
                ))}
              </select>
            </FloatField>
            <FloatField label={t("cards.series_number")}>
              <input value={doc.number} onChange={(e) => setDoc({ ...doc, number: e.target.value })} />
            </FloatField>
            {date(t("cards.issued_on"), doc.issued_on, (v) => setDoc({ ...doc, issued_on: v }))}
            {date(t("cards.expires_on"), doc.expires_on, (v) => setDoc({ ...doc, expires_on: v }))}
            <FloatField label={t("cards.field_country")}>
              <input value={doc.country} maxLength={2} onChange={(e) => setDoc({ ...doc, country: e.target.value })} />
            </FloatField>
          </div>

          <h3>{t("cards.other_documents")}</h3>
          {others.map((d, index) => (
            <div key={index} className="stack" style={{ gap: 8 }}>
              <div className="form-grid">
                <FloatField label={`${t("cards.document_title")} ${index + 1}`}>
                  <input value={d.title} onChange={(e) => other(index, { title: e.target.value })} />
                </FloatField>
                <FloatField label={`${t("cards.series_number")} ${index + 1}`}>
                  <input value={d.number} onChange={(e) => other(index, { number: e.target.value })} />
                </FloatField>
                {date(`${t("cards.valid_from_label")} ${index + 1}`, d.issued_on, (v) => other(index, { issued_on: v }))}
                {date(`${t("cards.expires_on")} ${index + 1}`, d.expires_on, (v) => other(index, { expires_on: v }))}
                <FloatField label={`${t("cards.issued_by")} ${index + 1}`}>
                  <input value={d.issued_by ?? ""} onChange={(e) => other(index, { issued_by: e.target.value })} />
                </FloatField>
                <div style={{ display: "flex", alignItems: "center" }}>
                  <button type="button" className="link" onClick={() => setOthers(others.filter((_, i) => i !== index))}>
                    {t("cards.remove_document")}
                  </button>
                </div>
              </div>
            </div>
          ))}
          <div>
            <button type="button" className="link" onClick={() => setOthers([...others, NO_OTHER])}>
              + {t("cards.add_document")}
            </button>
          </div>
        </>
      ) : (
        <div className="form-grid">
          {COMPANY_TEXTS.map((name) => (
            <FloatField key={name} label={t(`cards.field_${name}`)}>
              {name === "bank_details" ? (
                <textarea value={texts[name]} onChange={(e) => setTexts({ ...texts, [name]: e.target.value })} />
              ) : (
                <input
                  value={texts[name]}
                  maxLength={name === "country" ? 2 : undefined}
                  onChange={(e) => setTexts({ ...texts, [name]: e.target.value })}
                />
              )}
            </FloatField>
          ))}
        </div>
      )}
      <ErrorMessage error={update.error} />
      <div className="actions">
        <button type="submit" disabled={update.pending}>
          {t("common.save")}
        </button>
        <button type="button" className="secondary" onClick={() => void onDone(false)}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

// --- Money of a client ----------------------------------------------------------------------

type Balance = {
  balance: Record<string, string>;
  available: Record<string, string>;
  unpaid: Record<string, string>;
  deposit_held: Record<string, string>;
};

/** What stands between the client and the company now: a debt or an overpayment, and — apart —
 * the deposit the company holds (§ 6.7.2, § 17.2.13). */
export function ClientBalanceCard({ clientId }: { clientId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const balance = useApi<Balance>(["client-balance", company.public_id, clientId], `/c/${company.public_id}/clients/${clientId}/balance`);
  const data = balance.data;
  const money = (value: string | number, currency: string) => `${Math.abs(Number(value)).toFixed(2)} ${currency}`;
  const debts = Object.entries(data?.balance ?? {}).filter(([, v]) => Number(v) < 0);
  const overpaid = Object.entries(data?.balance ?? {}).filter(([, v]) => Number(v) > 0);
  const deposits = Object.entries(data?.deposit_held ?? {}).filter(([, v]) => Number(v) > 0);
  return (
    <div style={{ maxWidth: 760 }}>
      <Card>
        <Head title={t("cards.money_now")} />
        {balance.isLoading ? <Loading /> : null}
        {data ? (
          <dl className="facts">
            <div>
              <dt>{t("cards.money_debt")}</dt>
              <dd>
                {debts.length ? (
                  <strong className="error-text">{debts.map(([c, v]) => money(v, c)).join(", ")}</strong>
                ) : (
                  t("cards.money_no")
                )}
              </dd>
            </div>
            <div>
              <dt>{t("cards.money_overpaid")}</dt>
              <dd>{overpaid.length ? <strong>{overpaid.map(([c, v]) => money(v, c)).join(", ")}</strong> : t("cards.money_no")}</dd>
            </div>
            <div>
              <dt>{t("cards.money_deposit")}</dt>
              <dd>{deposits.length ? <strong>{deposits.map(([c, v]) => money(v, c)).join(", ")}</strong> : t("cards.money_no")}</dd>
            </div>
          </dl>
        ) : null}
        <p className="muted small" style={{ marginTop: 12, marginBottom: 0 }}>
          {t("cards.money_now_hint")}
        </p>
      </Card>
    </div>
  );
}

// --- History of a client --------------------------------------------------------------------

const BOOKED = ["proposed", "booked"];

/** What the client did with the company (customer's decision of 15.11.2026): rentals, bookings,
 * services, purchases — operation by operation, the cancelled ones too; the vehicle is named in
 * the line and the line leads to the operation. */
export function ClientInteractionsCard({ clientId }: { clientId: string }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const history = useApi<ClientHistory>(["client-history", company.public_id, clientId], `/c/${company.public_id}/clients/${clientId}/history`);
  if (history.isLoading) return <Loading />;
  const data = history.data;
  if (!data) return null;
  const zone = company.timezone || deviceTimeZone();
  const target = (row: ClientHistory["interactions"][number]) =>
    row.type === "rental"
      ? `/rentals/${row.id}`
      : row.type === "service_work"
        ? `/service/operations/${row.id}`
        : row.type === "service"
          ? `/rentals/service-lines/${row.id}`
          : `/orders/${row.order_id}`;
  return (
    <Card>
      {data.cards.length > 1 ? <p className="muted small">{t("cards.history_merged", { count: data.cards.length - 1 })}</p> : null}
      {data.interactions.length === 0 ? (
        <p className="muted">{t("cards.history_none")}</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>{t("cards.money_when")}</th>
              <th>{t("cards.history_what")}</th>
              <th>{t("cards.history_about")}</th>
              <th>{t("cards.orders_state")}</th>
            </tr>
          </thead>
          <tbody>
            {data.interactions.map((row) => (
              <tr key={`${row.type}-${row.id}`}>
                <td className="small">{formatMoment(row.at, zone, i18n.language)}</td>
                <td>
                  <Link to={companyPath(company, target(row))}>
                    {t(`cards.kind_${row.type === "rental" && BOOKED.includes(row.state) ? "booking" : row.type}`, {
                      defaultValue: row.type,
                    })}
                  </Link>
                </td>
                <td>
                  {row.vehicles.length ? `${row.vehicles.join(", ")} · ` : ""}
                  <Link to={companyPath(company, `/orders/${row.order_id}`)}>{t("cards.money_order", { number: row.order_number })}</Link>
                </td>
                <td>{t(`cards.op_state_${row.state}`, { defaultValue: row.state })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
