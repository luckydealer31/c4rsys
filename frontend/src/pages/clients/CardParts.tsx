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
  ClientHistory,
  Counterparty,
  DriverLicense,
  ExtendedCard,
  IdDocument,
  OtherDocument,
  ProfileLink,
} from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, FloatField, IconButton, Loading, Modal } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";

export type CardType = "client" | "counterparty";
type AnyCard = (Client | Counterparty) & ExtendedCard;


import { ContactsEditor, contactLabel, contactsOf, rowsOf, type ContactRow } from "./ContactsEditor";

type Row = [label: string, value: ReactNode];

/** The name in the head of a card (customer's decision of 15.11.2026): first name, patronymic,
 * last name; a patronymic that is not filled is not shown. */
export function fullName(card: Pick<AnyCard, "kind" | "first_name" | "middle_name" | "last_name" | "name">): string {
  if (card.kind !== "person") return card.name;
  return [card.first_name, card.middle_name, card.last_name].filter((x) => x.trim()).join(" ") || card.name;
}

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
  contacts: ContactRow[];
  registration_address: string;
  residence_address: string;
  comment: string;
  address: string;
  external_id: string;
  registration: string;
  category: string;
};

function draftOf(card: AnyCard): MainDraft {
  return {
    first_name: card.first_name,
    last_name: card.last_name,
    middle_name: card.middle_name,
    company_name: card.company_name,
    contacts: rowsOf(card.contacts),
    registration_address: card.registration_address,
    residence_address: card.residence_address,
    comment: card.comment,
    address: card.address,
    external_id: card.external_id,
    registration: "registration" in card ? card.registration : "",
    category: "category" in card ? card.category : "",
  };
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
  const company = useCompany();
  const editable = useEditable(cardType, card);
  const [editing, setEditing] = useState(false);
  const person = card.kind === "person";
  // The ID of the linked profile stands under the name; the link itself is made and broken on
  // the tab "More" (decision of 15.11.2026). The key is shared with ProfileLinkCard.
  const links = useApi<{ links: ProfileLink[] }>(
    ["profile-links", company.public_id, cardType, card.id],
    `/c/${company.public_id}/profile-links?card_type=${cardType}&card_id=${card.id}`,
  );
  const linked = links.data?.links[0]?.state === "accepted" ? links.data.links[0].linked_profile : null;
  const rows: Row[] = [
    person && "birth_date" in card && card.birth_date ? [t("clients.birth_date"), card.birth_date] : ["", ""],
    // Every contact under its own kind: a phone, a messenger, an email (25.11.2026).
    ...card.contacts.map((c): Row => [contactLabel(t, c), c.value]),
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
          <h1 style={{ margin: 0 }}>{fullName(card)}</h1>
          {editable ? <IconButton icon="edit" label={t("common.edit")} onClick={() => setEditing(true)} /> : null}
          {badges}
        </div>
        <div className="muted" style={{ marginBottom: 16 }}>
          {linked ? <div>{t("links.profile_id", { id: linked.public_id })}</div> : null}
          {t(`clients.kind_${card.kind}`)}
        </div>
        <Facts rows={rows} />
      </Card>
      {editing ? (
        <Modal wide title={fullName(card)} onClose={() => setEditing(false)}>
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
  const text = (key: Exclude<keyof MainDraft, "contacts">, label: string, area = false) => (
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
    const contacts = contactsOf(draft.contacts);
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
        {person ? (
          <>
            {text("registration_address", t("cards.field_registration_address"))}
            {text("residence_address", t("cards.field_residence_address"))}
          </>
        ) : null}
        {cardType === "counterparty" ? (
          <CategoryField value={draft.category} onChange={(category) => setDraft({ ...draft, category })} />
        ) : null}
        {/* The free «registration data» repeat the requisites (decision of 15.11.2026): the
            field is offered only while it is filled, for example from an import. */}
        {"registration" in card && card.registration ? text("registration", t("cards.field_registration")) : null}
        {/* The single address of the old form is offered only while it is filled. */}
        {card.address ? text("address", t("cards.field_address")) : null}
      </div>
      <ContactsEditor rows={draft.contacts} onChange={(contacts) => setDraft({ ...draft, contacts })} />
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

/** The category of a counterparty is chosen from the directory of the company (customer's
 * decision of 15.11.2026); a missing one is created right here by its name. */
function CategoryField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const list = useApi<{ categories: string[] }>(
    ["counterparty-categories", company.public_id],
    `/c/${company.public_id}/counterparties/categories`,
  );
  const known = list.data?.categories ?? [];
  const [adding, setAdding] = useState(false);
  const options = value && !known.includes(value) ? [...known, value] : known;
  if (adding) {
    return (
      <FloatField label={t("cards.category_new")}>
        <input autoFocus value={value} onChange={(e) => onChange(e.target.value)} />
      </FloatField>
    );
  }
  return (
    <FloatField label={t("cards.field_category")}>
      <select
        value={value}
        onChange={(e) => {
          if (e.target.value === "\u0000new") {
            onChange("");
            setAdding(true);
          } else onChange(e.target.value);
        }}
      >
        <option value="">{t("cards.category_none")}</option>
        {options.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
        <option value={"\u0000new"}>{t("cards.category_add")}</option>
      </select>
    </FloatField>
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

/** A document of a person in the form: its kind is chosen from a list; «other» asks for a name. */
type DocumentRow = {
  type: IdDocument["type"];
  title: string;
  number: string;
  issued_on: string | null;
  expires_on: string | null;
  issued_by: string;
};
const DOCUMENT_TYPES = ["passport", "national_id", "other"] as const;
const NO_ROW: DocumentRow = { type: "passport", title: "", number: "", issued_on: null, expires_on: null, issued_by: "" };

/** The documents of the card as one list. The card keeps one passport or ID as its identity
 * document and the rest as «other documents» named by their kind; here they are one list. */
function documentRows(card: AnyCard, t: (key: string) => string): DocumentRow[] {
  const rows: DocumentRow[] = [];
  const id = card.id_document;
  if (id) {
    rows.push({
      type: id.type,
      title: id.type === "other" ? t("cards.document_other") : "",
      number: id.number,
      issued_on: id.issued_on,
      expires_on: id.expires_on,
      issued_by: id.issued_by ?? "",
    });
  }
  for (const d of card.other_documents) {
    const type = DOCUMENT_TYPES.find((x) => x !== "other" && t(`cards.document_${x}`) === d.title) ?? "other";
    rows.push({
      type,
      title: type === "other" ? d.title : "",
      number: d.number,
      issued_on: d.issued_on,
      expires_on: d.expires_on,
      issued_by: d.issued_by ?? "",
    });
  }
  return rows;
}

const NO_LICENSE: DriverLicense = { number: "", country: "", issued_on: null, expires_on: null, categories: [] };

/** § 6.2.12–6.2.13: the documents of a person — the driving licence, the passport or ID, other
 * documents; for a company — its requisites. None is required. */
export function DocumentsCard({ cardType, card, onSaved }: { cardType: CardType; card: AnyCard; onSaved: () => Promise<void> }) {
  const { t } = useTranslation();
  const editable = useEditable(cardType, card);
  const [editing, setEditing] = useState(false);
  const person = card.kind === "person";
  const title = t(person ? "cards.documents_title" : "cards.requisites_title");
  const license = card.driver_license;
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
        ...documentRows(card, t).map(
          (d): Row => [
            d.type === "other" ? d.title : t(`cards.document_${d.type}`),
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
  const [license, setLicense] = useState<DriverLicense>(card.driver_license ?? NO_LICENSE);
  // The categories are typed by hand: they differ from country to country.
  const [categories, setCategories] = useState((card.driver_license?.categories ?? []).join(", "));
  // The experience is asked as a year: few remember the day they got the licence.
  const [since, setSince] = useState(card.driving_since ? card.driving_since.slice(0, 4) : "");
  const [rows, setRows] = useState<DocumentRow[]>(documentRows(card, t));
  const thisYear = new Date().getFullYear();

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = { expected_version: card.version };
    if (person) {
      body.driver_license = license.number.trim()
        ? { ...license, categories: categories.split(/[,\s]+/).filter(Boolean) }
        : null;
      // The first passport or ID with a number is the identity document of the card; the rest
      // are kept as other documents under the name of their kind.
      const identity = rows.find((d) => d.type !== "other" && d.number.trim());
      const before = card.id_document;
      body.id_document = identity
        ? {
            type: identity.type,
            number: identity.number,
            country: before && before.number === identity.number ? before.country : "",
            issued_on: identity.issued_on,
            expires_on: identity.expires_on,
            issued_by: identity.issued_by,
          }
        : null;
      const notes = new Map(card.other_documents.map((d) => [`${d.title}|${d.number}`, d.note]));
      const others: OtherDocument[] = rows
        .filter((d) => d !== identity)
        .map((d) => ({ ...d, title: d.type === "other" ? d.title : t(`cards.document_${d.type}`) }))
        .filter((d) => d.title.trim())
        .map((d) => ({
          title: d.title,
          number: d.number,
          issued_on: d.issued_on,
          expires_on: d.expires_on,
          issued_by: d.issued_by,
          note: notes.get(`${d.title}|${d.number}`) ?? "",
        }));
      // The day of the year the card already has is kept when the year is the same.
      body.driving_since = since
        ? card.driving_since?.startsWith(since)
          ? card.driving_since
          : `${since}-01-01`
        : null;
      body.other_documents = others;
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
  const row = (index: number, change: Partial<DocumentRow>) =>
    setRows(rows.map((x, i) => (i === index ? { ...x, ...change } : x)));

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
          <FloatField label={t("cards.categories")}>
            <input value={categories} onChange={(e) => setCategories(e.target.value)} />
          </FloatField>

          <h3>{t("cards.other_documents")}</h3>
          {rows.map((d, index) => (
            <div key={index} className="form-grid">
              <FloatField label={`${t("cards.document_type")} ${index + 1}`}>
                <select value={d.type} onChange={(e) => row(index, { type: e.target.value as DocumentRow["type"] })}>
                  {DOCUMENT_TYPES.map((v) => (
                    <option key={v} value={v}>
                      {t(`cards.document_${v}`)}
                    </option>
                  ))}
                </select>
              </FloatField>
              {d.type === "other" ? (
                <FloatField label={`${t("cards.document_title")} ${index + 1}`}>
                  <input value={d.title} onChange={(e) => row(index, { title: e.target.value })} />
                </FloatField>
              ) : null}
              <FloatField label={`${t("cards.series_number")} ${index + 1}`}>
                <input value={d.number} onChange={(e) => row(index, { number: e.target.value })} />
              </FloatField>
              {date(`${t("cards.issued_on")} ${index + 1}`, d.issued_on, (v) => row(index, { issued_on: v }))}
              {date(`${t("cards.expires_on")} ${index + 1}`, d.expires_on, (v) => row(index, { expires_on: v }))}
              <FloatField label={`${t("cards.issued_by")} ${index + 1}`}>
                <input value={d.issued_by} onChange={(e) => row(index, { issued_by: e.target.value })} />
              </FloatField>
              <div style={{ display: "flex", alignItems: "center" }}>
                <button type="button" className="link" onClick={() => setRows(rows.filter((_, i) => i !== index))}>
                  {t("cards.remove_document")}
                </button>
              </div>
            </div>
          ))}
          <div>
            <button type="button" className="link" onClick={() => setRows([...rows, NO_ROW])}>
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
  deposits?: { rental_id: string; currency: string; held: string; hold_days: number | null; release_at: string | null }[];
};

/** What stands between the client and the company now (customer's decision of 15.11.2026): the
 * balance in one line — a debt or an overpayment — and, apart, the deposit the company holds,
 * with the time left until the hold ends once the rental is over (§ 6.7.2, § 17.2.13). */
export function ClientBalanceCard({ clientId }: { clientId: string }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const balance = useApi<Balance>(["client-balance", company.public_id, clientId], `/c/${company.public_id}/clients/${clientId}/balance`);
  const data = balance.data;
  const money = (value: string | number, currency: string) => `${Math.abs(Number(value)).toFixed(2)} ${currency}`;
  const sums = Object.entries(data?.balance ?? {}).filter(([, v]) => Number(v) !== 0);
  const deposits = (data?.deposits ?? []).filter((d) => Number(d.held) > 0);
  const zone = company.timezone || deviceTimeZone();
  const left = (until: string) => Math.ceil((new Date(until).getTime() - Date.now()) / 86_400_000);
  return (
    <div style={{ maxWidth: 760 }}>
      <Card>
        <Head title={t("cards.money_now")} />
        {balance.isLoading ? <Loading /> : null}
        {data ? (
          <dl className="facts">
            <div>
              <dt>{t("cards.money_balance")}</dt>
              <dd>
                {sums.length === 0
                  ? "0"
                  : sums.map(([currency, value]) => (
                      <div key={currency}>
                        <strong className={Number(value) < 0 ? "error-text" : undefined}>
                          {t(Number(value) < 0 ? "cards.money_debt_of" : "cards.money_overpaid_of", { amount: money(value, currency) })}
                        </strong>
                      </div>
                    ))}
              </dd>
            </div>
            <div>
              <dt>{t("cards.money_deposit")}</dt>
              <dd>
                {deposits.length === 0
                  ? "0"
                  : deposits.map((d) => (
                      <div key={d.rental_id}>
                        <Link to={companyPath(company, `/rentals/${d.rental_id}`)}>
                          <strong>{money(d.held, d.currency)}</strong>
                        </Link>
                        {d.release_at ? (
                          <span className="muted">
                            {" · "}
                            {left(d.release_at) > 0
                              ? t("cards.money_hold_left", {
                                  count: left(d.release_at),
                                  until: formatMoment(d.release_at, zone, i18n.language),
                                })
                              : t("cards.money_hold_over")}
                          </span>
                        ) : null}
                      </div>
                    ))}
              </dd>
            </div>
          </dl>
        ) : null}
      </Card>
    </div>
  );
}

/** What came from the counterparty and what went back (customer's decision of 15.11.2026): the
 * receipts of goods and the returns, by the settlements with it. Shown with the rights of the
 * finance section. */
export function SupplierHistoryCard({ counterpartyId }: { counterpartyId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const data = useApi<{
    receipts: { id: string; number: number; currency: string; amount: string }[];
    returns: { id: string; number: number; receipt_id: string; currency: string; amount: string }[];
  }>(["supplier-settlement", company.public_id, counterpartyId], `/c/${company.public_id}/counterparties/${counterpartyId}/settlement`);
  if (data.isLoading) return <Loading />;
  const info = data.data;
  if (!info) return null;
  const rows = [
    ...info.receipts.map((r) => ({ key: `r-${r.id}`, kind: "receipt", number: r.number, to: r.id, amount: `${r.amount} ${r.currency}` })),
    ...info.returns.map((r) => ({ key: `b-${r.id}`, kind: "return", number: r.number, to: r.receipt_id, amount: `${r.amount} ${r.currency}` })),
  ];
  return (
    <Card title={t("cards.supplier_history")}>
      {rows.length === 0 ? (
        <p className="muted">{t("cards.history_none")}</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>{t("cards.history_what")}</th>
              <th className="num">{t("cards.money_amount")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td>
                  <Link to={companyPath(company, `/warehouse/receipts/${row.to}`)}>
                    {t(`cards.kind_${row.kind}`)} № {row.number}
                  </Link>
                </td>
                <td className="num">{row.amount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
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
