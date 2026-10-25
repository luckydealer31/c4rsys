import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { Counterparty, ItemStock, StockRow, SupplierArticle, WarehouseItem } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";

/** A quantity with the short unit; trailing zeros of hundredths are dropped. */
export function useQuantity() {
  const { t } = useTranslation();
  return (value: string | number, unit: string) => `${Number(value)} ${t(`warehouse.unit_short_${unit}`)}`;
}

/** An amount, or «Cost not given» when it is unknown (§ 11.2.6). */
export function useMoney() {
  const { t } = useTranslation();
  return (value: string | null, currency: string) =>
    value === null ? t("receipts.cost_not_given") : `${value} ${currency}`;
}

export function parseTags(text: string): string[] {
  return [...new Set(text.split(",").map((tag) => tag.trim()).filter(Boolean))];
}

/** Stock of the company: one row per item with the quantity by warehouse and the joint place
 * tags; the row opens the item with its assets and lots (§ 11.1.1, § 11.1.10). */
export function StockList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const quantity = useQuantity();
  const [query, setQuery] = useState("");
  const rows = useApi<{ stock: StockRow[] }>(
    ["warehouse-stock", company.public_id, query],
    `/c/${company.public_id}/warehouse/stock?q=${encodeURIComponent(query)}`,
    can("warehouse.view"),
  );
  if (!can("warehouse.view")) return <p className="muted">{t("warehouse.no_access")}</p>;
  return (
    <>
      {can("warehouse.receive") ? (
        <div className="actions" style={{ marginTop: 0, marginBottom: 16 }}>
          <Link className="button" to={companyPath(company, "/warehouse/receipts/new")}>
            {t("receipts.create")}
          </Link>
        </div>
      ) : null}
      <Card>
        <Field label={t("warehouse.search")}>
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
        </Field>
        {rows.isLoading ? <Loading /> : null}
        {rows.data?.stock.length === 0 ? <p className="muted">{t("stock.empty")}</p> : null}
        {rows.data?.stock.length ? (
          <table>
            <thead>
              <tr>
                <th>{t("warehouse.name")}</th>
                <th>{t("warehouse.article")}</th>
                <th>{t("stock.total")}</th>
                <th>{t("stock.by_warehouse")}</th>
                <th>{t("stock.tags")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.data.stock.map((row) => (
                <tr key={row.item.id}>
                  <td>
                    <Link to={companyPath(company, `/warehouse/items/${row.item.id}`)}>{row.item.name}</Link>
                  </td>
                  <td>{row.item.article || "—"}</td>
                  <td>{quantity(row.quantity, row.item.unit)}</td>
                  <td className="small">
                    {row.places.map((p) => `${p.location_name}: ${quantity(p.quantity, row.item.unit)}`).join("; ")}
                  </td>
                  <td className="small muted">{row.tags.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Card>
    </>
  );
}

/** The expanded stock of an item: assets or lots with their quantities by packaging, own tags
 * and purchase costs of each receipt (§ 11.1.10, § 11.2.13). */
export function ItemStockCard({ item }: { item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const quantity = useQuantity();
  const money = useMoney();
  const data = useApi<{ stock: ItemStock }>(
    ["warehouse-item-stock", company.public_id, item.id],
    `/c/${company.public_id}/warehouse/items/${item.id}/stock`,
    can("warehouse.view"),
  );
  if (!can("warehouse.view")) return null;
  const places = data.data?.stock.places ?? [];
  return (
    <Card title={t("stock.title")}>
      {data.isLoading ? <Loading /> : null}
      {data.data && places.length === 0 ? <p className="muted">{t("stock.item_empty")}</p> : null}
      {places.map((place) => (
        <div key={place.context_id} style={{ marginBottom: 16 }}>
          <h3>
            {t("warehouse.storage_warehouse")} — {place.location_name}: {quantity(place.quantity, item.unit)}
          </h3>
          {place.assets.length ? (
            <table>
              <thead>
                <tr>
                  <th>{t("stock.asset_id")}</th>
                  <th>{t("receipts.serial_number")}</th>
                  <th>{t("receipts.unit_cost")}</th>
                  <th>{t("receipts.number_short")}</th>
                  <th>{t("stock.tags")}</th>
                </tr>
              </thead>
              <tbody>
                {place.assets.map((asset) => (
                  <tr key={asset.id}>
                    <td>{asset.public_id}</td>
                    <td>{asset.serial_number || "—"}</td>
                    <td>{money(asset.purchase_cost, asset.currency)}</td>
                    <td>{asset.receipt_number ?? "—"}</td>
                    <td>
                      <Tags kind="assets" id={asset.id} tags={asset.tags} item={item} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {place.lots.map((lot) => (
            <div key={lot.id} className="card" style={{ padding: 12, marginTop: 8 }}>
              <p>
                <strong>{lot.public_id}</strong> — {quantity(lot.quantity, item.unit)}
                {lot.external_number ? ` · ${t("receipts.lot_external_number")}: ${lot.external_number}` : ""}
                {lot.expiry_date ? ` · ${t("receipts.lot_expiry_date")}: ${lot.expiry_date}` : ""}{" "}
                {lot.expired ? <span className="badge warn">{t("stock.expired")}</span> : null}
              </p>
              <p className="small">
                {lot.stock
                  .map((s) =>
                    s.packaging_id
                      ? t("stock.packs", { packs: s.packs, size: quantity(s.packaging_quantity ?? 0, item.unit) })
                      : t("stock.loose", { quantity: quantity(s.quantity, item.unit) }),
                  )
                  .join("; ")}
              </p>
              <table>
                <thead>
                  <tr>
                    <th>{t("receipts.number_short")}</th>
                    <th>{t("receipts.received_at")}</th>
                    <th>{t("stock.received")}</th>
                    <th>{t("stock.remaining")}</th>
                    <th>{t("receipts.unit_cost")}</th>
                  </tr>
                </thead>
                <tbody>
                  {lot.layers.map((layer, index) => (
                    <tr key={index}>
                      <td>{layer.receipt_number ?? "—"}</td>
                      <td>{new Date(layer.received_at).toLocaleDateString()}</td>
                      <td>{quantity(layer.received_quantity, item.unit)}</td>
                      <td>{quantity(layer.remaining, item.unit)}</td>
                      <td>{money(layer.unit_cost, lot.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Tags kind="lots" id={lot.id} tags={lot.tags} item={item} />
            </div>
          ))}
        </div>
      ))}
    </Card>
  );
}

/** Text marks of a place for one asset or lot — hints where to look (§ 11.1.9). */
function Tags({ kind, id, tags, item }: { kind: "assets" | "lots"; id: string; tags: string[]; item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const [text, setText] = useState<string | null>(null);
  const save = useCommand<{ tags: string[] }, unknown>(`/c/${company.public_id}/warehouse/${kind}/${id}/tags`);
  if (text === null) {
    return (
      <span className="small">
        {tags.join(", ") || "—"}{" "}
        {can("warehouse.tags.edit") ? (
          <button type="button" className="link" onClick={() => setText(tags.join(", "))}>
            {t("stock.edit_tags")}
          </button>
        ) : null}
      </span>
    );
  }
  return (
    <span className="row" style={{ alignItems: "center" }}>
      <input value={text} placeholder={t("receipts.tags_hint")} onChange={(e) => setText(e.target.value)} />
      <button
        type="button"
        disabled={save.pending}
        onClick={async () => {
          if ((await save.run({ tags: parseTags(text) })) !== null) {
            setText(null);
            await client.invalidateQueries({ queryKey: ["warehouse-item-stock", company.public_id, item.id] });
            await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
          }
        }}
      >
        {t("common.save")}
      </button>
      <ErrorMessage error={save.error} />
    </span>
  );
}

/** Articles of suppliers linked to the item (§ 11.2.17). */
export function SupplierArticles({ item }: { item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const base = `/c/${company.public_id}/warehouse/items/${item.id}/supplier-articles`;
  const list = useApi<{ articles: SupplierArticle[] }>(["supplier-articles", company.public_id, item.id], base);
  const suppliers = useApi<{ counterparties: Counterparty[] }>(
    ["counterparties", company.public_id, "", false],
    `/c/${company.public_id}/counterparties?q=&state=active`,
    can("counterparty.view") && can("warehouse.catalog.manage"),
  );
  const add = useCommand<Record<string, string>, unknown>(base);
  const remove = useCommand<{ id: string }, unknown>((body) => `${base}/${body.id}`, "DELETE");
  const [draft, setDraft] = useState({ supplier_id: "", article: "", supplier_name: "" });
  const refresh = () => client.invalidateQueries({ queryKey: ["supplier-articles", company.public_id, item.id] });
  const articles = list.data?.articles ?? [];
  if (!can("warehouse.catalog.manage") && articles.length === 0) return null;
  return (
    <Card title={t("articles.title")}>
      {articles.length === 0 ? <p className="muted">{t("articles.none")}</p> : null}
      <ul>
        {articles.map((a) => (
          <li key={a.id}>
            {a.supplier.name}: <strong>{a.article}</strong>
            {a.supplier_name ? ` — ${a.supplier_name}` : ""}{" "}
            {can("warehouse.catalog.manage") ? (
              <button
                type="button"
                className="link"
                onClick={async () => {
                  if ((await remove.run({ id: a.id })) !== null) await refresh();
                }}
              >
                {t("articles.remove")}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      {can("warehouse.catalog.manage") ? (
        <div className="row">
          <Field label={t("receipts.supplier")}>
            <select value={draft.supplier_id} onChange={(e) => setDraft({ ...draft, supplier_id: e.target.value })}>
              <option value="">—</option>
              {(suppliers.data?.counterparties ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("articles.article")}>
            <input value={draft.article} onChange={(e) => setDraft({ ...draft, article: e.target.value })} />
          </Field>
          <Field label={t("articles.supplier_name")}>
            <input value={draft.supplier_name} onChange={(e) => setDraft({ ...draft, supplier_name: e.target.value })} />
          </Field>
          <button
            type="button"
            style={{ alignSelf: "end" }}
            disabled={add.pending || !draft.supplier_id || !draft.article.trim()}
            onClick={async () => {
              if ((await add.run(draft)) !== null) {
                setDraft({ supplier_id: "", article: "", supplier_name: "" });
                await refresh();
              }
            }}
          >
            {t("articles.add")}
          </button>
        </div>
      ) : null}
      <ErrorMessage error={add.error ?? remove.error} />
    </Card>
  );
}
