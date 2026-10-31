import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type {
  CardMerge,
  Client,
  ClientHistory,
  ClientRestriction,
  Counterparty,
  DriverLicense,
  ExtendedCard,
  IdDocument,
  MergeComparison,
  OtherDocument,
  SplitRecord,
} from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";

type CardType = "client" | "counterparty";

function useWhen() {
  const { i18n } = useTranslation();
  const company = useCompany();
  return (value: string) => formatMoment(value, company.timezone || deviceTimeZone(), i18n.language);
}

const PERSON_TEXTS = ["registration_address", "residence_address"] as const;
const COMPANY_TEXTS = [
  "country",
  "legal_form",
  "registration_number",
  "tax_number",
  "legal_address",
  "actual_address",
  "director_name",
  "contact_person",
  "bank_details",
  "signatory_name",
  "signatory_basis",
] as const;

const NO_DOCUMENT: IdDocument = { type: "passport", number: "", country: "", issued_on: null, expires_on: null };
const NO_LICENSE: DriverLicense = { number: "", country: "", issued_on: null, expires_on: null, categories: [] };

/** § 6.2.12–6.2.13: the extended data of the card; none is required at creation. */
export function ExtendedFieldsCard({
  cardType,
  card,
  onSaved,
}: {
  cardType: CardType;
  card: (Client | Counterparty) & ExtendedCard;
  onSaved: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const path = cardType === "client" ? "clients" : "counterparties";
  const update = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/${path}/${card.id}`, "PATCH");
  const editable = can(`${cardType}.edit`) && card.state !== "merged";
  const [texts, setTexts] = useState<Record<string, string> | null>(null);
  const [doc, setDoc] = useState<IdDocument | null>(null);
  const [license, setLicense] = useState<(DriverLicense & { text: string }) | null>(null);
  const [since, setSince] = useState<string | null>(null);
  const [others, setOthers] = useState<OtherDocument[] | null>(null);
  const names: readonly string[] = card.kind === "person" ? PERSON_TEXTS : COMPANY_TEXTS;
  const currentTexts = texts ?? Object.fromEntries(names.map((n) => [n, String(card[n as keyof ExtendedCard] ?? "")]));
  const currentDoc = doc ?? card.id_document ?? NO_DOCUMENT;
  const currentLicense = license ?? {
    ...(card.driver_license ?? NO_LICENSE),
    text: (card.driver_license?.categories ?? []).join(", "),
  };
  const currentOthers = others ?? card.other_documents;
  const changed = texts !== null || doc !== null || license !== null || since !== null || others !== null;

  async function save(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = { ...currentTexts, expected_version: card.version };
    if (card.kind === "person") {
      body.id_document = currentDoc.number.trim() ? currentDoc : null;
      const { text, ...rest } = currentLicense;
      body.driver_license = rest.number.trim()
        ? { ...rest, categories: text.split(/[,\s]+/).filter(Boolean) }
        : null;
      body.driving_since = (since ?? card.driving_since) || null;
      body.other_documents = currentOthers.filter((d) => d.title.trim());
    }
    if ((await update.run(body)) !== null) {
      setTexts(null);
      setDoc(null);
      setLicense(null);
      setSince(null);
      setOthers(null);
      await onSaved();
    }
  }

  const text = (name: string) => (
    <Field key={name} label={t(`cards.field_${name}`)}>
      {name === "bank_details" ? (
        <textarea
          value={currentTexts[name] ?? ""}
          disabled={!editable}
          onChange={(e) => setTexts({ ...currentTexts, [name]: e.target.value })}
        />
      ) : (
        <input
          value={currentTexts[name] ?? ""}
          disabled={!editable}
          maxLength={name === "country" ? 2 : undefined}
          onChange={(e) => setTexts({ ...currentTexts, [name]: e.target.value })}
        />
      )}
    </Field>
  );
  const date = (value: string | null, onChange: (v: string | null) => void, label: string) => (
    <Field label={label}>
      <input type="date" value={value ?? ""} disabled={!editable} onChange={(e) => onChange(e.target.value || null)} />
    </Field>
  );

  return (
    <Card title={t("cards.extended_title")}>
      <form className="stack" onSubmit={save}>
        <p className="muted small">{t("cards.extended_hint")}</p>
        {card.kind === "person" ? (
          <>
            <h3>{t("cards.id_document")}</h3>
            <div className="row">
              <Field label={t("cards.document_type")}>
                <select
                  value={currentDoc.type}
                  disabled={!editable}
                  onChange={(e) => setDoc({ ...currentDoc, type: e.target.value as IdDocument["type"] })}
                >
                  {(["passport", "national_id", "other"] as const).map((v) => (
                    <option key={v} value={v}>
                      {t(`cards.document_${v}`)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("cards.number")}>
                <input value={currentDoc.number} disabled={!editable} onChange={(e) => setDoc({ ...currentDoc, number: e.target.value })} />
              </Field>
              <Field label={t("cards.field_country")}>
                <input
                  value={currentDoc.country}
                  maxLength={2}
                  disabled={!editable}
                  onChange={(e) => setDoc({ ...currentDoc, country: e.target.value })}
                />
              </Field>
              {date(currentDoc.issued_on, (v) => setDoc({ ...currentDoc, issued_on: v }), t("cards.issued_on"))}
              {date(currentDoc.expires_on, (v) => setDoc({ ...currentDoc, expires_on: v }), t("cards.expires_on"))}
            </div>
            <h3>{t("cards.driver_license")}</h3>
            <div className="row">
              <Field label={t("cards.number")}>
                <input
                  value={currentLicense.number}
                  disabled={!editable}
                  onChange={(e) => setLicense({ ...currentLicense, number: e.target.value })}
                />
              </Field>
              <Field label={t("cards.categories")}>
                <input
                  value={currentLicense.text}
                  disabled={!editable}
                  placeholder="B, A"
                  onChange={(e) => setLicense({ ...currentLicense, text: e.target.value })}
                />
              </Field>
              {date(currentLicense.issued_on, (v) => setLicense({ ...currentLicense, issued_on: v }), t("cards.issued_on"))}
              {date(currentLicense.expires_on, (v) => setLicense({ ...currentLicense, expires_on: v }), t("cards.expires_on"))}
              {date(since ?? card.driving_since, (v) => setSince(v ?? ""), t("cards.driving_since"))}
            </div>
            <h3>{t("cards.other_documents")}</h3>
            {currentOthers.map((d, index) => (
              <div className="row" key={index}>
                <Field label={t("cards.document_title")}>
                  <input
                    value={d.title}
                    disabled={!editable}
                    onChange={(e) => setOthers(currentOthers.map((x, i) => (i === index ? { ...x, title: e.target.value } : x)))}
                  />
                </Field>
                <Field label={t("cards.number")}>
                  <input
                    value={d.number}
                    disabled={!editable}
                    onChange={(e) => setOthers(currentOthers.map((x, i) => (i === index ? { ...x, number: e.target.value } : x)))}
                  />
                </Field>
                {date(d.expires_on, (v) => setOthers(currentOthers.map((x, i) => (i === index ? { ...x, expires_on: v } : x))), t("cards.expires_on"))}
              </div>
            ))}
            {editable ? (
              <div className="actions">
                <button
                  type="button"
                  className="secondary small"
                  onClick={() =>
                    setOthers([...currentOthers, { title: "", number: "", issued_on: null, expires_on: null, note: "" }])
                  }
                >
                  {t("cards.add_document")}
                </button>
              </div>
            ) : null}
            <div className="row">{PERSON_TEXTS.map(text)}</div>
          </>
        ) : (
          <div className="row" style={{ flexWrap: "wrap" }}>
            {COMPANY_TEXTS.map(text)}
          </div>
        )}
        <ErrorMessage error={update.error} />
        {editable ? (
          <div className="actions">
            <button type="submit" disabled={!changed || update.pending}>
              {t("common.save")}
            </button>
          </div>
        ) : null}
      </form>
    </Card>
  );
}

/** § 6.7.1, § 23.8.7: the blacklist of the company — two rights, a reason, author and time. */
export function RestrictionsCard({ clientId, onChanged }: { clientId: string; onChanged: () => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const when = useWhen();
  const queryClient = useQueryClient();
  const base = `/c/${company.public_id}`;
  const key = ["client-restrictions", company.public_id, clientId];
  const marks = useApi<{ restrictions: ClientRestriction[] }>(key, `${base}/clients/${clientId}/restrictions`);
  const add = useCommand<{ reason: string }, unknown>(`${base}/clients/${clientId}/restrictions`);
  const lift = useCommand<{ id: string; reason: string }, unknown>((b) => `${base}/client-restrictions/${b.id}/lift`);
  const [reason, setReason] = useState("");
  const [liftReason, setLiftReason] = useState("");
  const items = marks.data?.restrictions ?? [];
  const active = items.find((m) => !m.lifted_at);

  async function refresh() {
    setReason("");
    setLiftReason("");
    await queryClient.invalidateQueries({ queryKey: key });
    await onChanged();
  }

  return (
    <Card title={t("cards.blacklist_title")}>
      <p className="muted small">{t("cards.blacklist_hint")}</p>
      {active ? (
        <div className="alert error">
          <strong>{t("cards.blacklisted")}</strong>: {active.reason}
          <div className="small">{t("cards.added_by", { name: active.added_by, at: when(active.added_at) })}</div>
        </div>
      ) : (
        <p>{t("cards.not_blacklisted")}</p>
      )}
      {!active && can("client.blacklist.add") ? (
        <div className="row">
          <Field label={t("cards.reason")}>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <div className="actions">
            <button
              type="button"
              disabled={!reason.trim() || add.pending}
              onClick={async () => {
                if ((await add.run({ reason })) !== null) await refresh();
              }}
            >
              {t("cards.blacklist_add")}
            </button>
          </div>
        </div>
      ) : null}
      {active && can("client.blacklist.remove") ? (
        <div className="row">
          <Field label={t("cards.lift_reason")}>
            <input value={liftReason} onChange={(e) => setLiftReason(e.target.value)} />
          </Field>
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={!liftReason.trim() || lift.pending}
              onClick={async () => {
                if ((await lift.run({ id: active.id, reason: liftReason })) !== null) await refresh();
              }}
            >
              {t("cards.blacklist_remove")}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorMessage error={add.error ?? lift.error} />
      {items.some((m) => m.lifted_at) ? (
        <>
          <h3>{t("cards.blacklist_history")}</h3>
          <ul className="small">
            {items
              .filter((m) => m.lifted_at)
              .map((m) => (
                <li key={m.id}>
                  {m.reason} — {t("cards.added_by", { name: m.added_by, at: when(m.added_at) })};{" "}
                  {t("cards.lifted_by", { name: m.lifted_by ?? "", at: when(m.lifted_at ?? ""), reason: m.lifted_reason })}
                </li>
              ))}
          </ul>
        </>
      ) : null}
    </Card>
  );
}

/** § 6.1.14, § 8.1.2: orders, payments and vehicles of the client and of the merged cards. */
export function ClientHistoryCard({ clientId }: { clientId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const when = useWhen();
  const history = useApi<ClientHistory>(
    ["client-history", company.public_id, clientId],
    `/c/${company.public_id}/clients/${clientId}/history`,
  );
  if (history.isLoading) return <Loading />;
  const data = history.data;
  if (!data) return null;
  return (
    <Card title={t("cards.history_title")}>
      {data.cards.length > 1 ? <p className="muted small">{t("cards.history_merged", { count: data.cards.length - 1 })}</p> : null}
      <h3>{t("cards.history_orders")}</h3>
      {data.orders.length === 0 ? <p className="muted">{t("cards.history_none")}</p> : null}
      <table className="table">
        <tbody>
          {data.orders.map((o) => (
            <tr key={o.id}>
              <td>
                <Link to={companyPath(company, `/orders/${o.id}`)}>№ {o.number}</Link>
              </td>
              <td>{t(`orders.state_${o.state}`, { defaultValue: o.state })}</td>
              <td className="small">{when(o.created_at)}</td>
              <td className="small">
                {o.operations.map((op) => t(`cards.operation_${op.type}`, { defaultValue: op.type })).join(", ")}
              </td>
              <td className="small">
                {Object.entries(o.amounts)
                  .map(([currency, m]) => `${m.charged} / ${m.paid} ${currency}`)
                  .join("; ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.vehicles.length ? (
        <>
          <h3>{t("cards.history_vehicles")}</h3>
          <ul>
            {data.vehicles.map((v) => (
              <li key={v.id}>
                <Link to={companyPath(company, `/vehicles/${v.id}`)}>{v.name}</Link>{" "}
                <span className="muted small">({v.sources.map((s) => t(`cards.vehicle_source_${s}`)).join(", ")})</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {data.payments.length ? (
        <>
          <h3>{t("cards.history_payments")}</h3>
          <table className="table">
            <tbody>
              {data.payments.map((p) => (
                <tr key={p.id}>
                  <td>№ {p.number}</td>
                  <td>{t(`cards.payment_${p.kind}`, { defaultValue: p.kind })}</td>
                  <td>
                    {p.direction === "out" ? "−" : ""}
                    {p.amount} {p.currency}
                  </td>
                  <td className="small">{when(p.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </Card>
  );
}

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) {
    return (
      value
        .map((item) =>
          typeof item === "object" && item !== null
            ? String((item as Record<string, unknown>).value ?? (item as Record<string, unknown>).title ?? JSON.stringify(item))
            : String(item),
        )
        .join(", ") || "—"
    );
  }
  if (typeof value === "object") {
    return Object.values(value as Record<string, unknown>)
      .filter((v) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0))
      .map(String)
      .join(" · ");
  }
  if (typeof value === "boolean") return value ? "✓" : "—";
  return String(value);
}

/** § 6.2.3, § 6.5.1–6.5.4: merging a duplicate into this card after comparing them. */
export function MergeCard({ cardType, card, onChanged }: { cardType: CardType; card: { id: string; state: string }; onChanged: () => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const when = useWhen();
  const queryClient = useQueryClient();
  const base = `/c/${company.public_id}`;
  const listPath = cardType === "client" ? "clients" : "counterparties";
  const key = ["card-merges", company.public_id, cardType, card.id];
  const merges = useApi<{ merges: CardMerge[] }>(key, `${base}/card-merges?card_type=${cardType}&card_id=${card.id}`);
  const [query, setQuery] = useState("");
  const candidates = useApi<{ clients?: Client[]; counterparties?: Counterparty[] }>(
    ["merge-candidates", company.public_id, cardType, query],
    `${base}/${listPath}?q=${encodeURIComponent(query)}`,
    can(`${cardType}.merge`) && query.trim().length > 1,
  );
  const [duplicate, setDuplicate] = useState("");
  const comparison = useApi<{ comparison: MergeComparison }>(
    ["merge-preview", company.public_id, cardType, card.id, duplicate],
    `${base}/card-merges/preview?card_type=${cardType}&primary_id=${card.id}&duplicate_id=${duplicate}`,
    Boolean(duplicate),
  );
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const merge = useCommand<Record<string, unknown>, unknown>(`${base}/card-merges`);
  const [splitting, setSplitting] = useState<string | null>(null);
  const found = (candidates.data?.clients ?? candidates.data?.counterparties ?? []).filter((c) => c.id !== card.id);
  const compared = comparison.data?.comparison;
  const differing = (compared?.fields ?? []).filter((f) => !f.same);

  async function done() {
    setDuplicate("");
    setChoices({});
    setReason("");
    setQuery("");
    await queryClient.invalidateQueries({ queryKey: key });
    await onChanged();
  }

  const items = merges.data?.merges ?? [];
  if (!can(`${cardType}.merge`) && !items.length) return null;
  return (
    <Card title={t("cards.merge_title")}>
      {items.map((m) => (
        <div key={m.id} className="stack">
          <p>
            <span className={`badge ${m.state === "merged" ? "warn" : ""}`}>{t(`cards.merge_state_${m.state}`)}</span>{" "}
            {m.primary.id === card.id ? (
              <>
                {t("cards.merged_from")}{" "}
                <Link to={companyPath(company, cardType === "client" ? `/clients/${m.duplicate.id}` : `/clients/counterparties/${m.duplicate.id}`)}>
                  {m.duplicate.name}
                </Link>
              </>
            ) : (
              <>
                {t("cards.merged_into")}{" "}
                <Link to={companyPath(company, cardType === "client" ? `/clients/${m.primary.id}` : `/clients/counterparties/${m.primary.id}`)}>
                  {m.primary.name}
                </Link>
              </>
            )}{" "}
            <span className="muted small">
              {when(m.merged_at)}
              {m.reason ? ` · ${m.reason}` : ""}
              {m.split_at ? ` · ${t("cards.split_at", { at: when(m.split_at), reason: m.split_reason })}` : ""}
            </span>
          </p>
          {m.state === "merged" && m.primary.id === card.id && can(`${cardType}.split`) ? (
            splitting === m.id ? (
              <SplitForm mergeId={m.id} onDone={async () => { setSplitting(null); await done(); }} />
            ) : (
              <div className="actions">
                <button type="button" className="secondary small" onClick={() => setSplitting(m.id)}>
                  {t("cards.split_open")}
                </button>
              </div>
            )
          ) : null}
        </div>
      ))}
      {can(`${cardType}.merge`) && card.state === "active" ? (
        <div className="stack">
          <p className="muted small">{t("cards.merge_hint")}</p>
          <Field label={t("cards.merge_search")}>
            <input value={query} onChange={(e) => setQuery(e.target.value)} />
          </Field>
          {found.length ? (
            <Field label={t("cards.merge_duplicate")}>
              <select
                value={duplicate}
                onChange={(e) => {
                  setDuplicate(e.target.value);
                  setChoices({});
                }}
              >
                <option value="">—</option>
                {found.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} {c.contacts.map((x) => x.value).join(", ")}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          {compared ? (
            <>
              {compared.obstacles.length ? (
                <div className="alert warn">
                  <strong>{t("cards.merge_obstacles")}</strong>
                  <ul>
                    {compared.obstacles.map((o) => (
                      <li key={o}>{t(`cards.obstacle_${o}`)}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("cards.merge_field")}</th>
                    <th>{t("cards.merge_main")}</th>
                    <th>{t("cards.merge_dup")}</th>
                    <th>{t("cards.merge_choice")}</th>
                  </tr>
                </thead>
                <tbody>
                  {compared.fields
                    .filter((f) => !f.same || shown(f.primary) !== "—")
                    .map((f) => (
                      <tr key={f.key}>
                        <td>{t(`cards.field_${f.key}`, { defaultValue: f.key })}</td>
                        <td>{shown(f.primary)}</td>
                        <td>{shown(f.duplicate)}</td>
                        <td>
                          {f.same ? (
                            <span className="muted small">{t("cards.merge_same")}</span>
                          ) : (
                            <select
                              aria-label={f.key}
                              value={choices[f.key] ?? ""}
                              onChange={(e) => setChoices({ ...choices, [f.key]: e.target.value })}
                            >
                              <option value="">—</option>
                              {f.choices.map((c) => (
                                <option key={c} value={c}>
                                  {t(`cards.choice_${c}`)}
                                </option>
                              ))}
                            </select>
                          )}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
              {compared.profiles.duplicate && !compared.profiles.primary ? (
                <p className="small">{t("cards.merge_link_moves", { name: compared.profiles.duplicate.name })}</p>
              ) : null}
              {compared.restrictions.duplicate.length ? <p className="small">{t("cards.merge_dup_blacklisted")}</p> : null}
              <Field label={t("cards.reason")}>
                <input value={reason} onChange={(e) => setReason(e.target.value)} />
              </Field>
              <ErrorMessage error={merge.error} />
              <div className="actions">
                <button
                  type="button"
                  disabled={
                    merge.pending || compared.obstacles.length > 0 || differing.some((f) => !choices[f.key])
                  }
                  onClick={async () => {
                    if (!window.confirm(t("cards.merge_confirm"))) return;
                    const body = { card_type: cardType, primary_id: card.id, duplicate_id: duplicate, choices, reason };
                    if ((await merge.run(body)) !== null) await done();
                  }}
                >
                  {t("cards.merge_do")}
                </button>
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

/** § 6.5.3: the records made after the merge are assigned by hand; nothing automatically. */
function SplitForm({ mergeId, onDone }: { mergeId: string; onDone: () => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const when = useWhen();
  const path = `/c/${company.public_id}/card-merges/${mergeId}/split`;
  const preview = useApi<{ merge: CardMerge; records: SplitRecord[] }>(["split-preview", company.public_id, mergeId], path);
  const split = useCommand<Record<string, unknown>, unknown>(path);
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  if (preview.isLoading) return <Loading />;
  const records = preview.data?.records ?? [];
  return (
    <div className="stack">
      <p className="muted small">{t("cards.split_hint")}</p>
      {records.length === 0 ? <p className="muted">{t("cards.split_no_records")}</p> : null}
      {records.map((r) => (
        <div className="row" key={r.key}>
          <span>
            {t(`cards.record_${r.kind}`, { defaultValue: r.kind })} {r.label}{" "}
            <span className="muted small">{when(r.at)}</span>
          </span>
          <select
            aria-label={r.key}
            value={assignments[r.key] ?? ""}
            onChange={(e) => setAssignments({ ...assignments, [r.key]: e.target.value })}
          >
            <option value="">—</option>
            <option value="primary">{preview.data?.merge.primary.name}</option>
            <option value="duplicate">{preview.data?.merge.duplicate.name}</option>
          </select>
        </div>
      ))}
      <Field label={t("cards.reason")}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <ErrorMessage error={split.error} />
      <div className="actions">
        <button
          type="button"
          disabled={split.pending || !reason.trim() || records.some((r) => !assignments[r.key])}
          onClick={async () => {
            if ((await split.run({ assignments, reason })) !== null) await onDone();
          }}
        >
          {t("cards.split_do")}
        </button>
      </div>
    </div>
  );
}

/** § 6.2.9: instead of filling the card, a card with only the ID or a contact of the profile and
 * the request sent at once. */
export function ProvisionalForm({ cardType }: { cardType: CardType }) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const path = cardType === "client" ? "clients" : "counterparties";
  const create = useCommand<{ kind: string; target: string }, { client?: Client; counterparty?: Counterparty }>(
    `/c/${company.public_id}/${path}/provisional`,
  );
  const [kind, setKind] = useState<"person" | "company">(cardType === "client" ? "person" : "company");
  const [target, setTarget] = useState("");
  const [created, setCreated] = useState<{ id: string; name: string } | null>(null);
  return (
    <Card title={t("cards.provisional_title")}>
      <form
        className="stack"
        onSubmit={async (event) => {
          event.preventDefault();
          const result = await create.run({ kind, target });
          if (result) {
            const card = result.client ?? result.counterparty;
            setCreated(card ? { id: card.id, name: card.name } : null);
            setTarget("");
            await queryClient.invalidateQueries({ queryKey: [path, company.public_id] });
          }
        }}
      >
        <p className="muted small">{t("cards.provisional_hint")}</p>
        <div className="row">
          <Field label={t("clients.kind")}>
            <select value={kind} onChange={(e) => setKind(e.target.value as "person" | "company")}>
              <option value="person">{t("clients.kind_person")}</option>
              <option value="company">{t("clients.kind_company")}</option>
            </select>
          </Field>
          <Field label={t(kind === "company" ? "links.target_company" : "links.target_person")}>
            <input value={target} onChange={(e) => setTarget(e.target.value)} />
          </Field>
        </div>
        <ErrorMessage error={create.error} />
        {created ? (
          <p className="alert ok">
            {t("cards.provisional_created")}{" "}
            <Link
              to={companyPath(company, cardType === "client" ? `/clients/${created.id}` : `/clients/counterparties/${created.id}`)}
            >
              {created.name}
            </Link>
          </p>
        ) : null}
        <div className="actions">
          <button type="submit" disabled={!target.trim() || create.pending}>
            {t("cards.provisional_send")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** § 6.6.4–6.6.5: archive and restore are separate rights. */
export function ArchiveActions({
  card,
  onChanged,
}: {
  card: { id: string; version: number; state: string };
  onChanged: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const base = `/c/${company.public_id}/clients/${card.id}`;
  const archive = useCommand<{ expected_version: number }, unknown>(`${base}/archive`);
  const restore = useCommand<{ expected_version: number }, unknown>(`${base}/restore`);
  return (
    <>
      <ErrorMessage error={archive.error ?? restore.error} />
      <div className="actions">
        {card.state === "active" && can("client.archive") ? (
          <button
            type="button"
            className="secondary"
            disabled={archive.pending}
            onClick={async () => {
              if ((await archive.run({ expected_version: card.version })) !== null) await onChanged();
            }}
          >
            {t("cards.archive")}
          </button>
        ) : null}
        {card.state === "archived" && can("client.restore") ? (
          <button
            type="button"
            className="secondary"
            disabled={restore.pending}
            onClick={async () => {
              if ((await restore.run({ expected_version: card.version })) !== null) await onChanged();
            }}
          >
            {t("cards.restore")}
          </button>
        ) : null}
      </div>
    </>
  );
}

/** § 6.1.14: what the contractor shares with this company as its client. */
export function ContractorHistoryCard({ counterpartyId }: { counterpartyId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const when = useWhen();
  const history = useApi<{ orders: { id: string; number: number; state: string; created_at: string; amounts: Record<string, { charged: string; paid: string }> }[] }>(
    ["contractor-history", company.public_id, counterpartyId],
    `/c/${company.public_id}/counterparties/${counterpartyId}/history`,
  );
  const orders = history.data?.orders ?? [];
  return (
    <Card title={t("cards.contractor_history")}>
      <p className="muted small">{t("cards.contractor_history_hint")}</p>
      {orders.length === 0 ? <p className="muted">{t("cards.history_none")}</p> : null}
      <table className="table">
        <tbody>
          {orders.map((o) => (
            <tr key={o.id}>
              <td>№ {o.number}</td>
              <td>{t(`orders.state_${o.state}`, { defaultValue: o.state })}</td>
              <td className="small">{when(o.created_at)}</td>
              <td className="small">
                {Object.entries(o.amounts)
                  .map(([currency, m]) => `${m.charged} / ${m.paid} ${currency}`)
                  .join("; ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
