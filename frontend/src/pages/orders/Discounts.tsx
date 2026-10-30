import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";

type CatalogDiscount = {
  id: string;
  name: string;
  kind: "percent" | "amount";
  value: string;
  currency: string;
  scope: string;
  client_group_ids: string[];
  goods_categories: string[];
  state: "active" | "archived";
};

type OrderDiscounts = {
  positions: {
    type: string;
    id: string;
    name: string;
    currency: string;
    base: string;
    discount: string;
    applied_id: string | null;
    applied_target: "position" | "order" | null;
  }[];
  applied: { id: string; name: string; kind: string; value: string; currency: string; target: string; position_id: string | null }[];
  offered: CatalogDiscount[];
  limit: string | null;
};

export function discountText(d: { kind: string; value: string; currency: string; name: string }) {
  const size = d.kind === "percent" ? `${Number(d.value)} %` : `${Number(d.value)} ${d.currency}`;
  return `${d.name} (${size})`;
}

/** Discounts of the order (§ 12.5): on one position or on the order, never both on a position;
 * a conflict is resolved by an explicit choice of three (§ 12.5.3). */
export function OrderDiscountsCard({ orderId, editable, onChange }: { orderId: string; editable: boolean; onChange: () => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const base = `/c/${company.public_id}/orders/${orderId}/discounts`;
  const key = ["order-discounts", company.public_id, orderId];
  const data = useApi<OrderDiscounts>(key, base);
  const apply = useCommand<Record<string, string>, OrderDiscounts>(base);
  const remove = useCommand<{ id: string }, OrderDiscounts>((b) => `${base}/${b.id}/remove`);
  const [discountId, setDiscountId] = useState("");
  const [target, setTarget] = useState("order");
  if (data.isLoading) return <Loading />;
  const d = data.data;
  if (!d || (!d.positions.length && !d.applied.length)) return null;
  const allowed = editable && can("order.discount");

  async function done() {
    await client.invalidateQueries({ queryKey: key });
    await onChange();
  }

  async function submit(event: FormEvent | null, choice = "") {
    event?.preventDefault();
    const position = d?.positions.find((p) => `${p.type}:${p.id}` === target);
    const body: Record<string, string> = { discount_id: discountId };
    if (position) {
      body.position_type = position.type;
      body.position_id = position.id;
    }
    if (choice) body.conflict = choice;
    if ((await apply.run(body)) !== null) {
      setDiscountId("");
      await done();
    }
  }

  const conflictError =
    apply.error instanceof ApiError && apply.error.code === "DISCOUNT_CONFLICT" ? apply.error : null;

  return (
    <Card title={t("discounts.title")}>
      <table className="table">
        <tbody>
          {d.positions.map((p) => (
            <tr key={`${p.type}:${p.id}`}>
              <td>
                {t(`cabinet.source_${p.type}`)}
                {p.name ? `: ${p.name}` : ""}
              </td>
              <td style={{ textAlign: "right" }}>
                {p.base} {p.currency}
              </td>
              <td style={{ textAlign: "right" }}>
                {Number(p.discount) > 0 ? (
                  <>
                    −{p.discount} {p.currency}{" "}
                    <span className="muted small">{t(`discounts.from_${p.applied_target}`)}</span>
                  </>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.applied.map((a) => (
        <div key={a.id} className="small">
          {discountText(a)} · {t(`discounts.target_${a.target}`)}
          {allowed ? (
            <button
              type="button"
              className="secondary small"
              style={{ marginLeft: 8 }}
              disabled={remove.pending}
              onClick={async () => {
                if ((await remove.run({ id: a.id })) !== null) await done();
              }}
            >
              {t("discounts.remove")}
            </button>
          ) : null}
        </div>
      ))}
      {allowed ? (
        <form className="row" onSubmit={(e) => void submit(e)}>
          <Field label={t("discounts.discount")}>
            <select value={discountId} onChange={(e) => setDiscountId(e.target.value)}>
              <option value="">—</option>
              {d.offered.map((o) => (
                <option key={o.id} value={o.id}>
                  {discountText(o)} · {t(`discounts.scope_${o.scope}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("discounts.apply_to")}>
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="order">{t("discounts.whole_order")}</option>
              {d.positions.map((p) => (
                <option key={`${p.type}:${p.id}`} value={`${p.type}:${p.id}`}>
                  {t(`cabinet.source_${p.type}`)}
                  {p.name ? `: ${p.name}` : ""}
                </option>
              ))}
            </select>
          </Field>
          <div className="actions">
            <button type="submit" disabled={!discountId || apply.pending}>
              {t("discounts.apply")}
            </button>
          </div>
        </form>
      ) : null}
      {allowed ? (
        <p className="muted small">
          {d.limit !== null ? t("discounts.limit", { percent: Number(d.limit) }) : t("discounts.no_limit")}
        </p>
      ) : null}
      {conflictError ? (
        <div className="alert warn">
          <p>{t("discounts.conflict")}</p>
          <div className="actions">
            <button type="button" onClick={() => void submit(null, "keep_positions")}>
              {t("discounts.keep_positions")}
            </button>
            <button type="button" className="secondary" onClick={() => void submit(null, "remove_positions")}>
              {t("discounts.remove_positions")}
            </button>
            <button type="button" className="secondary" onClick={() => apply.clearError()}>
              {t("discounts.cancel_order_discount")}
            </button>
          </div>
        </div>
      ) : (
        <ErrorMessage error={apply.error ?? remove.error} />
      )}
    </Card>
  );
}

const SCOPES = ["order", "rental", "extra_service", "service", "goods"];

/** The catalog of discounts (§ 12.5.2; decisions З11, В-2), kept with the company settings. */
export function DiscountCatalogPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const key = ["discounts", company.public_id];
  const catalog = useApi<{ discounts: CatalogDiscount[] }>(key, `/c/${company.public_id}/discounts`);
  const groups = useApi<{ groups: { id: string; name: string; state: string }[] }>(
    ["client-groups", company.public_id],
    `/c/${company.public_id}/client-groups`,
  );
  const create = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/discounts`);
  const archive = useCommand<{ archived: boolean; id: string }, unknown>((b) => `/c/${company.public_id}/discounts/${b.id}/archive`);
  const [form, setForm] = useState({ name: "", kind: "percent", value: "", currency: company.main_currency ?? "", scope: "order", groups: [] as string[], categories: "" });
  const editable = can("company.settings");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = {
      name: form.name,
      kind: form.kind,
      value: form.value.replace(",", "."),
      currency: form.kind === "amount" ? form.currency : "",
      scope: form.scope,
      client_group_ids: form.groups,
      goods_categories: form.scope === "goods" ? form.categories.split(",").map((c) => c.trim()).filter(Boolean) : [],
    };
    if ((await create.run(body)) !== null) {
      setForm({ ...form, name: "", value: "", groups: [], categories: "" });
      await client.invalidateQueries({ queryKey: key });
    }
  }

  const groupName = (id: string) => groups.data?.groups.find((g) => g.id === id)?.name ?? id;
  return (
    <div className="page">
      <h1>{t("discounts.catalog")}</h1>
      <Card>
        {catalog.isLoading ? <Loading /> : null}
        {catalog.data?.discounts.length === 0 ? <p className="muted">{t("discounts.none")}</p> : null}
        {(catalog.data?.discounts ?? []).map((d) => (
          <div key={d.id} style={{ padding: "6px 0", borderBottom: "1px solid var(--border, #ddd)" }}>
            <strong>{discountText(d)}</strong> · {t(`discounts.scope_${d.scope}`)}
            {d.client_group_ids.length ? ` · ${d.client_group_ids.map(groupName).join(", ")}` : ""}
            {d.goods_categories.length ? ` · ${d.goods_categories.join(", ")}` : ""}
            {d.state === "archived" ? <span className="badge"> {t("warehouse.archived")}</span> : null}
            {editable ? (
              <button
                type="button"
                className="secondary small"
                style={{ marginLeft: 8 }}
                onClick={async () => {
                  if ((await archive.run({ id: d.id, archived: d.state === "active" })) !== null) {
                    await client.invalidateQueries({ queryKey: key });
                  }
                }}
              >
                {d.state === "active" ? t("warehouse.archive") : t("warehouse.restore")}
              </button>
            ) : null}
          </div>
        ))}
      </Card>
      {editable ? (
        <Card title={t("discounts.new")}>
          <form className="stack" onSubmit={submit}>
            <div className="row">
              <Field label={t("discounts.name")}>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </Field>
              <Field label={t("discounts.kind")}>
                <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                  <option value="percent">{t("discounts.kind_percent")}</option>
                  <option value="amount">{t("discounts.kind_amount")}</option>
                </select>
              </Field>
              <Field label={t("discounts.value")}>
                <input inputMode="decimal" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} />
              </Field>
              {form.kind === "amount" ? (
                <Field label={t("discounts.currency")}>
                  <input value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })} />
                </Field>
              ) : null}
              <Field label={t("discounts.scope")}>
                <select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
                  {SCOPES.map((s) => (
                    <option key={s} value={s}>
                      {t(`discounts.scope_${s}`)}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            {form.scope === "goods" ? (
              <Field label={t("discounts.categories")} hint={t("discounts.categories_hint")}>
                <input value={form.categories} onChange={(e) => setForm({ ...form, categories: e.target.value })} />
              </Field>
            ) : null}
            {(groups.data?.groups ?? []).filter((g) => g.state === "active").length ? (
              <div>
                <div className="small">{t("discounts.groups")}</div>
                {(groups.data?.groups ?? [])
                  .filter((g) => g.state === "active")
                  .map((g) => (
                    <label key={g.id} className="small" style={{ marginRight: 12 }}>
                      <input
                        type="checkbox"
                        checked={form.groups.includes(g.id)}
                        onChange={() =>
                          setForm({
                            ...form,
                            groups: form.groups.includes(g.id) ? form.groups.filter((x) => x !== g.id) : [...form.groups, g.id],
                          })
                        }
                      />{" "}
                      {g.name}
                    </label>
                  ))}
                <p className="muted small">{t("discounts.groups_hint")}</p>
              </div>
            ) : null}
            <ErrorMessage error={create.error ?? archive.error} />
            <div className="actions">
              <button type="submit" disabled={!form.name.trim() || !form.value.trim() || create.pending}>
                {t("discounts.create")}
              </button>
            </div>
          </form>
        </Card>
      ) : null}
    </div>
  );
}

/** The groups of the client base (§ 6.2.14; decision В-3): the list and the client's groups. */
export function ClientGroupsCard({ clientId }: { clientId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const groupsKey = ["client-groups", company.public_id];
  const mineKey = ["client-groups-of", company.public_id, clientId];
  const groups = useApi<{ groups: { id: string; name: string; state: string }[] }>(groupsKey, `/c/${company.public_id}/client-groups`);
  const mine = useApi<{ group_ids: string[] }>(mineKey, `/c/${company.public_id}/clients/${clientId}/groups`);
  const save = useCommand<{ group_ids: string[] }, unknown>(`/c/${company.public_id}/clients/${clientId}/groups`, "PUT");
  const create = useCommand<{ name: string }, unknown>(`/c/${company.public_id}/client-groups`);
  const [name, setName] = useState("");
  const editable = can("client.edit");
  const selected = mine.data?.group_ids ?? [];
  const active = (groups.data?.groups ?? []).filter((g) => g.state === "active" || selected.includes(g.id));

  async function toggle(id: string) {
    const next = selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id];
    if ((await save.run({ group_ids: next })) !== null) await client.invalidateQueries({ queryKey: mineKey });
  }

  return (
    <Card title={t("client_groups.title")}>
      {active.length === 0 ? <p className="muted small">{t("client_groups.none")}</p> : null}
      {active.map((g) => (
        <label key={g.id} style={{ marginRight: 16 }}>
          <input type="checkbox" disabled={!editable || save.pending} checked={selected.includes(g.id)} onChange={() => void toggle(g.id)} />{" "}
          {g.name}
        </label>
      ))}
      {editable ? (
        <form
          className="row"
          onSubmit={async (e) => {
            e.preventDefault();
            if ((await create.run({ name })) !== null) {
              setName("");
              await client.invalidateQueries({ queryKey: groupsKey });
            }
          }}
        >
          <Field label={t("client_groups.new")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="submit" className="secondary" disabled={!name.trim() || create.pending}>
              {t("client_groups.create")}
            </button>
          </div>
        </form>
      ) : null}
      <ErrorMessage error={save.error ?? create.error} />
    </Card>
  );
}

/** The discount limit of an employee, set with the right «Apply discounts» (§ 12.5.5; З12). */
export function DiscountLimitCard({ employmentId }: { employmentId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const url = `/c/${company.public_id}/employees/${employmentId}/discount-limit`;
  const key = ["discount-limit", company.public_id, employmentId];
  const limit = useApi<{ percent: string | null }>(key, url);
  const save = useCommand<{ percent: string | null }, unknown>(url, "PUT");
  const [value, setValue] = useState<string | null>(null);
  if (limit.isLoading) return <Loading />;
  const current = value ?? (limit.data?.percent ? String(Number(limit.data.percent)) : "");
  return (
    <Card title={t("discounts.limit_title")}>
      <form
        className="row"
        onSubmit={async (e) => {
          e.preventDefault();
          if ((await save.run({ percent: current.trim() ? current.replace(",", ".") : null })) !== null) {
            setValue(null);
            await client.invalidateQueries({ queryKey: key });
          }
        }}
      >
        <Field label={t("discounts.limit_percent")} hint={t("discounts.limit_hint")}>
          <input inputMode="decimal" value={current} onChange={(e) => setValue(e.target.value)} />
        </Field>
        <div className="actions">
          <button type="submit" disabled={save.pending || value === null}>
            {t("common.save")}
          </button>
        </div>
      </form>
      <ErrorMessage error={save.error} />
    </Card>
  );
}
