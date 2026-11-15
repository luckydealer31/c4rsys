import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type {
  CardMerge,
  Client,
  ClientHistory,
  ClientRestriction,
  Counterparty,
  MergeComparison,
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

function useClientHistory(clientId: string) {
  const company = useCompany();
  return useApi<ClientHistory>(
    ["client-history", company.public_id, clientId],
    `/c/${company.public_id}/clients/${clientId}/history`,
  );
}

/** The money of the client in one list by date (customer's decision of 15.11.2026): what was
 * charged and what was paid or paid back, each with a link to its order. */
export function ClientMoneyCard({ clientId }: { clientId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const when = useWhen();
  const history = useClientHistory(clientId);
  if (history.isLoading) return <Loading />;
  const data = history.data;
  if (!data) return null;
  const order = (o: { id: string; number: number }) => (
    <Link key={o.id} to={companyPath(company, `/orders/${o.id}`)}>
      {t("cards.money_order", { number: o.number })}
    </Link>
  );
  const rows = [
    ...data.charges.map((c) => ({
      key: `c-${c.id}`,
      at: c.created_at,
      kind: t("cards.money_charge"),
      what: (
        <>
          {t(`cabinet.source_${c.source_type}`, { defaultValue: c.source_type })} · {order({ id: c.order_id, number: c.order_number })}
        </>
      ),
      // A charge takes from the client's balance; a recalculation downwards gives back.
      plus: Number(c.amount) < 0,
      amount: `${Math.abs(Number(c.amount)).toFixed(2)} ${c.currency}`,
    })),
    ...data.payments.map((p) => ({
      key: `p-${p.id}`,
      at: p.created_at,
      kind: t(`cards.payment_${p.kind}`, { defaultValue: p.kind }),
      what: (
        <>
          № {p.number}
          {p.orders.map((o) => (
            <span key={o.id}> · {order(o)}</span>
          ))}
        </>
      ),
      plus: p.direction === "in",
      amount: `${p.amount} ${p.currency}`,
    })),
  ].sort((x, y) => y.at.localeCompare(x.at));
  return (
    <Card title={t("cards.money_title")}>
      {rows.length === 0 ? (
        <p className="muted">{t("cards.history_none")}</p>
      ) : (
        <table className="finance-moves">
          <thead>
            <tr>
              <th>{t("cards.money_when")}</th>
              <th>{t("cards.money_kind")}</th>
              <th>{t("cards.money_what")}</th>
              <th className="num">{t("cards.money_amount")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td className="small">{when(row.at)}</td>
                <td>{row.kind}</td>
                <td>{row.what}</td>
                <td className={row.plus ? "num" : "num error-text"}>
                  {row.plus ? "+" : "−"}
                  {row.amount}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
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
