import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { Counterparty, ItemStock, StockRow, Storage, SupplierArticle, WarehouseItem } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading, Switch } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { PlaceTags } from "../tags/Tags";
import { useVehicles } from "../vehicles/VehiclesPage";
import { useCategoryLabel } from "./Categories";
import { VehiclePrice } from "./Prices";

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
 * tags; the row opens the item with its assets and lots (§ 11.1.1, § 11.1.10). One warehouse
 * may be chosen (customer's decision of 20.11.2026): then the list is of what lies on it, with
 * its quantity and its tags. The vehicles put on a warehouse as stock stand below. */
export function StockList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const quantity = useQuantity();
  const [query, setQuery] = useState("");
  const [place, setPlace] = useState("");
  const [more, setMore] = useState(false);
  const { i18n } = useTranslation();
  const categoryLabel = useCategoryLabel();
  const regions = new Intl.DisplayNames([i18n.language], { type: "region" });
  // «100 THB», or «100–150 THB» when the units differ; empty when nothing is known.
  const range = (low: string | null, high: string | null, currency: string) =>
    low === null || high === null
      ? ""
      : Number(low) === Number(high)
        ? `${Number(low)} ${currency}`
        : `${Number(low)}–${Number(high)} ${currency}`;
  const rows = useApi<{ stock: StockRow[] }>(
    ["warehouse-stock", company.public_id, query],
    `/c/${company.public_id}/warehouse/stock?q=${encodeURIComponent(query)}`,
    can("warehouse.view"),
  );
  const storages = useApi<{ storages: Storage[] }>(
    ["warehouse-storages", company.public_id],
    `/c/${company.public_id}/warehouse/storages`,
    can("warehouse.view"),
  );
  const vehicles = useVehicles();
  if (!can("warehouse.view")) return <p className="muted">{t("warehouse.no_access")}</p>;
  const places = storages.data?.storages ?? [];
  // On the chosen warehouse: the items that lie there, with its own quantity and tags.
  const stock = (rows.data?.stock ?? []).flatMap((row) => {
    if (!place) return [row];
    const here = row.places.find((p) => p.context_id === place);
    return here && Number(here.quantity) > 0 ? [{ ...row, quantity: here.quantity, tags: here.tags, places: [here] }] : [];
  });
  const stored = (vehicles.data?.vehicles ?? []).filter((v) => v.storage && (!place || v.storage.context_id === place));
  const placeName = (id: string) => places.find((p) => p.context_id === id)?.location_name ?? "";
  return (
    <>
      <Card>
        <div className="list-bar">
          {places.length > 1 ? (
            <select value={place} aria-label={t("stock.warehouse")} onChange={(e) => setPlace(e.target.value)}>
              <option value="">{t("stock.all_warehouses")}</option>
              {places.map((p) => (
                <option key={p.context_id} value={p.context_id}>
                  {p.location_name}
                </option>
              ))}
            </select>
          ) : null}
          {/* The main columns stand first; the rest of the attributes are shown on demand and
              the table is scrolled sideways (customer's decision of 20.11.2026). */}
          <Switch name={t("stock.more")} checked={more} onChange={setMore}>
            {t("stock.more")}
          </Switch>
          <input
            className="list-search at-end"
            value={query}
            placeholder={t("warehouse.search")}
            aria-label={t("warehouse.search")}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {rows.isLoading ? <Loading /> : null}
        {rows.data && stock.length === 0 ? <p className="muted">{t("stock.empty")}</p> : null}
        {stock.length ? (
          <div className="table-scroll">
            <table className={`stock-table${more ? " wide" : ""}`}>
              <thead>
                <tr>
                  <th>{t("warehouse.name")}</th>
                  <th>{t("warehouse.article")}</th>
                  <th>{t("warehouse.manufacturer")}</th>
                  <th>{t("warehouse.country")}</th>
                  <th>{t("stock.rest")}</th>
                  {place ? null : <th>{t("stock.warehouses")}</th>}
                  <th>{t("stock.cost")}</th>
                  <th>{t("stock.retail")}</th>
                  {more ? (
                    <>
                      <th>{t("stock.category")}</th>
                      <th>{t("warehouse.attr_originality")}</th>
                      <th>{t("warehouse.attr_condition")}</th>
                      <th>{t("warehouse.model_characteristic")}</th>
                      <th>{t("stock.tags")}</th>
                      <th>{t("stock.units")}</th>
                      <th>{t("warehouse.comment")}</th>
                    </>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {stock.map((row) => (
                  <tr key={row.item.id}>
                    <td>
                      <Link to={companyPath(company, `/warehouse/items/${row.item.id}`)}>{row.item.name}</Link>
                      {/* The only unit or lot is named by its own identifier. */}
                      {row.unit_id ? <div className="muted small">{row.unit_id}</div> : null}
                    </td>
                    <td>{row.item.article || "—"}</td>
                    <td>{row.item.manufacturer_name || "—"}</td>
                    <td>{row.item.country ? (regions.of(row.item.country) ?? row.item.country) : "—"}</td>
                    <td className="nowrap">
                      {quantity(row.quantity, row.item.unit)}
                      {row.below_minimum ? (
                        <span className="badge warn" style={{ marginLeft: 6 }}>
                          {t("prices.below_minimum")}
                        </span>
                      ) : null}
                    </td>
                    {place ? null : <td className="small">{row.places.map((p) => p.location_name).join(", ")}</td>}
                    <td className="nowrap">{range(row.cost_min, row.cost_max, row.currency) || (row.cost_unknown ? t("receipts.cost_not_given") : "—")}</td>
                    <td className="nowrap">
                      {row.price ? `${Number(row.price)} ${row.price_currency}` : range(row.unit_price_min, row.unit_price_max, row.currency) || "—"}
                    </td>
                    {more ? (
                      <>
                        <td className="small">{categoryLabel(row.item.part_category, row.item.part_subcategory) || "—"}</td>
                        <td>{row.item.originality ? t(`warehouse.value_${row.item.originality}`) : "—"}</td>
                        <td>{row.item.condition ? t(`warehouse.value_${row.item.condition}`) : "—"}</td>
                        <td className="small">{row.item.model_characteristic || "—"}</td>
                        <td className="small muted">{row.tags.join(", ") || "—"}</td>
                        <td>{row.units}</td>
                        <td className="small">{row.item.comment || "—"}</td>
                      </>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>
      {stored.length ? (
        <Card title={t("vehicles.stored_title")}>
          <table>
            <thead>
              <tr>
                <th>{t("warehouse.name")}</th>
                <th>{t("stock.warehouse")}</th>
                <th>{t("stock.sale_price")}</th>
              </tr>
            </thead>
            <tbody>
              {stored.map((v) => (
                <tr key={v.id}>
                  <td>
                    <Link to={companyPath(company, `/vehicles/${v.id}`)}>{v.name}</Link>{" "}
                    <span className="muted small">{v.vehicle_id}</span>
                  </td>
                  <td>{placeName(v.storage?.context_id ?? "")}</td>
                  <td>
                    <StoredPrice vehicleId={v.id} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}
    </>
  );
}

/** The sale price of a vehicle that lies on a warehouse as stock (§ 8.1.5). */
function StoredPrice({ vehicleId }: { vehicleId: string }) {
  const company = useCompany();
  const prices = useApi<{ prices: Record<string, string | null> }>(
    ["warehouse-vehicle-prices", company.public_id],
    `/c/${company.public_id}/warehouse/vehicle-prices`,
  );
  return <VehiclePrice vehicleId={vehicleId} value={prices.data?.prices[vehicleId] ?? ""} />;
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
                {[
                  ...lot.stock.map((s) =>
                    s.packaging_id
                      ? t("stock.packs", { packs: s.packs, size: quantity(s.packaging_quantity ?? 0, item.unit) })
                      : t("stock.loose", { quantity: quantity(s.quantity, item.unit) }),
                  ),
                  ...lot.opened_packages.map((o) =>
                    t("stock.opened", { remaining: quantity(o.remaining, item.unit), size: quantity(o.packaging_quantity, item.unit) }),
                  ),
                ].join("; ")}
                {lot.parent ? ` · ${t("stock.parent", { lot: lot.parent })}` : ""}
              </p>
              {can("warehouse.repack") ? (
                <Link
                  className="button secondary"
                  to={companyPath(company, `/warehouse/repack?item=${item.id}&lot=${lot.id}&context=${place.context_id}`)}
                >
                  {t("operations.repack")}
                </Link>
              ) : null}
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
      <IssuedList item={item} />
    </Card>
  );
}

/** Issued with a return possible and still out (§ 11.4.1). */
function IssuedList({ item }: { item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const quantity = useQuantity();
  const issued = useApi<{ issued: { operation_id: string; number: number; vehicle_name: string; asset: string | null; lot: string | null; quantity: string }[] }>(
    ["warehouse-item-issued", company.public_id, item.id],
    `/c/${company.public_id}/warehouse/items/${item.id}/issued`,
  );
  const rows = issued.data?.issued ?? [];
  if (!rows.length) return null;
  return (
    <>
      <h3>{t("stock.issued_title")}</h3>
      <ul>
        {rows.map((row) => (
          <li key={`${row.operation_id}-${row.asset ?? row.lot}`}>
            <Link to={companyPath(company, `/warehouse/operations/${row.operation_id}`)}>№ {row.number}</Link> ·{" "}
            {row.vehicle_name} · {row.asset ?? row.lot} · {quantity(row.quantity, item.unit)}
          </li>
        ))}
      </ul>
    </>
  );
}

/** Text marks of a place for one asset or lot — hints where to look (§ 11.1.9). */
function Tags({ kind, id, tags, item }: { kind: "assets" | "lots"; id: string; tags: string[]; item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const [chosen, setChosen] = useState<string[] | null>(null);
  const save = useCommand<{ tags: string[] }, unknown>(`/c/${company.public_id}/warehouse/${kind}/${id}/tags`);
  if (chosen === null) {
    return (
      <span className="small">
        {tags.join(", ") || "—"}{" "}
        {can("warehouse.tags.edit") ? (
          <button type="button" className="link" onClick={() => setChosen(tags)}>
            {t("stock.edit_tags")}
          </button>
        ) : null}
      </span>
    );
  }
  return (
    <span className="row" style={{ alignItems: "center" }}>
      <PlaceTags value={chosen} onChange={setChosen} />
      <button
        type="button"
        disabled={save.pending}
        onClick={async () => {
          if ((await save.run({ tags: chosen })) !== null) {
            setChosen(null);
            await client.invalidateQueries({ queryKey: ["tags", company.public_id] });
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
