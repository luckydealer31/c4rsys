import { useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { Counterparty, ItemPrices, ItemStock, StockRow, Storage, SupplierArticle, WarehouseItem } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { MultiPick } from "../../components/MultiPick";
import { Card, ErrorMessage, Field, Info, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { PlaceTags, RowTags, useTagsOf } from "../tags/Tags";
import { useVehicles } from "../vehicles/VehiclesPage";
import { useCategoryLabel } from "./Categories";
import { PricesCard, VehiclePrice } from "./Prices";

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

/** Stock of the company (§ 11.1.1, § 11.1.10; customer's decisions of 20.11.2026). Two views:
 * by items — one list, a row per item with its whole quantity and the warehouses it lies on;
 * by warehouses — warehouse by warehouse, each with what lies on it (all of them, or the
 * ones chosen); the same item is then repeated under every warehouse that has it. The main columns stand first; the arrow at the end of the heading opens the
 * rest and the table is then scrolled sideways. The arrow of a row unfolds its units, lots and
 * sale price right in the list (customer's decision of 21.11.2026); the name does the same,
 * and the card of the catalog, where the item is edited, is a link inside what is unfolded.
 * The vehicles put on a warehouse as stock stand below. */
const VIEWS = ["items", "places"] as const;
type View = (typeof VIEWS)[number];
const VIEW_KEY = "c4r.stock.view";
function storedView(): View {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "places" ? "places" : "items";
  } catch {
    return "items";
  }
}

export function StockList() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const quantity = useQuantity();
  const [query, setQuery] = useState("");
  // The warehouses chosen; none — all of them.
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [more, setMore] = useState(false);
  // The rows unfolded: «warehouse:item», the warehouse empty in the view by items.
  const [open, setOpen] = useState<Set<string>>(new Set());
  // The items chosen in the view by items; none — all of them.
  const [items, setItems] = useState<Set<string>>(new Set());
  // The view is remembered in the browser: it is a habit of the employee, not a filter.
  const [view, setView] = useState<View>(storedView);
  const tagsOf = useTagsOf("item");
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
  const all = rows.data?.stock ?? [];
  /** What lies on one warehouse: its own quantity and place tags. */
  const on = (place: string) =>
    all.flatMap((row) => {
      const here = row.places.find((p) => p.context_id === place);
      return here && Number(here.quantity) > 0 ? [{ ...row, quantity: here.quantity, tags: here.tags, places: [here] }] : [];
    });
  const picked = view === "places" && chosen.size > 0;
  const stored = (vehicles.data?.vehicles ?? []).filter((v) => v.storage && (!picked || chosen.has(v.storage.context_id)));
  const placeName = (id: string) => places.find((p) => p.context_id === id)?.location_name ?? "";

  const table = (stock: StockRow[], withPlaces: boolean, place = "") => (
    <div className="table-scroll">
      <table className={`stock-table${more ? " wide" : ""}`}>
        <thead>
          <tr>
            <th>{t("warehouse.name")}</th>
            <th>{t("warehouse.article")}</th>
            <th>{t("warehouse.manufacturer")}</th>
            <th>{t("warehouse.country")}</th>
            <th>{t("stock.category")}</th>
            <th>{t("tags.title")}</th>
            <th>{t("stock.rest")}</th>
            {withPlaces ? <th>{t("stock.warehouses")}</th> : null}
            {more ? (
              <>
                <th>{t("stock.cost")}</th>
                <th>{t("stock.retail")}</th>
                <th>{t("warehouse.attr_originality")}</th>
                <th>{t("warehouse.attr_condition")}</th>
                <th>{t("warehouse.model_characteristic")}</th>
                <th>{t("stock.tags")}</th>
                <th>{t("stock.units")}</th>
                <th>{t("warehouse.comment")}</th>
              </>
            ) : null}
            {/* The rest of the columns are opened here, at the end of the heading. */}
            <th className="more-col">
              <button
                type="button"
                className="icon-button"
                aria-expanded={more}
                aria-label={t(more ? "stock.less_columns" : "stock.more_columns")}
                title={t(more ? "stock.less_columns" : "stock.more_columns")}
                onClick={() => setMore(!more)}
              >
                {more ? "‹" : "›"}
              </button>
            </th>
          </tr>
        </thead>
        <tbody>
          {stock.map((row) => {
            const key = `${place}:${row.item.id}`;
            const shown = open.has(key);
            const toggle = () => {
              const next = new Set(open);
              if (shown) next.delete(key);
              else next.add(key);
              setOpen(next);
            };
            return (
            <Fragment key={row.item.id}>
            <tr>
              <td>
                <div className="stock-name">
                  <button
                    type="button"
                    className="icon-button"
                    aria-expanded={shown}
                    aria-label={t(shown ? "stock.details_hide" : "stock.details_show")}
                    title={t(shown ? "stock.details_hide" : "stock.details_show")}
                    onClick={toggle}
                  >
                    {shown ? "▾" : "▸"}
                  </button>
                  <div>
                    <button type="button" className="link" onClick={toggle}>
                      {row.item.name}
                    </button>
                    {/* The only unit or lot is named by its own identifier. */}
                    {row.unit_id ? <div className="muted small">{row.unit_id}</div> : null}
                  </div>
                </div>
              </td>
              <td>{row.item.article || "—"}</td>
              <td>{row.item.manufacturer_name || "—"}</td>
              <td>
                {row.item.country
                  ? (regions.of(row.item.country) ?? row.item.country)
                  : row.item.country_other || "—"}
              </td>
              <td className="small">{categoryLabel(row.item.part_category, row.item.part_subcategory) || "—"}</td>
              <td>
                <RowTags tags={tagsOf(row.item.id)} />
              </td>
              <td className="nowrap">
                {quantity(row.quantity, row.item.unit)}
                {row.below_minimum ? (
                  <span className="badge warn" style={{ marginLeft: 6 }}>
                    {t("prices.below_minimum")}
                  </span>
                ) : null}
              </td>
              {withPlaces ? <td className="small">{row.places.map((p) => p.location_name).join(", ")}</td> : null}
              {more ? (
                <>
                  <td className="nowrap">
                    {range(row.cost_min, row.cost_max, row.currency) || (row.cost_unknown ? t("receipts.cost_not_given") : "—")}
                  </td>
                  <td className="nowrap">
                    {row.price
                      ? `${Number(row.price)} ${row.price_currency}`
                      : range(row.unit_price_min, row.unit_price_max, row.currency) || "—"}
                  </td>
                  <td>{row.item.originality ? t(`warehouse.value_${row.item.originality}`) : "—"}</td>
                  <td>{row.item.condition ? t(`warehouse.value_${row.item.condition}`) : "—"}</td>
                  <td className="small">{row.item.model_characteristic || "—"}</td>
                  <td className="small muted">{row.tags.join(", ") || "—"}</td>
                  <td>{row.units}</td>
                  <td className="small">{row.item.comment || "—"}</td>
                </>
              ) : null}
              <td className="more-col" />
            </tr>
            {shown ? (
              <tr className="stock-details">
                <td colSpan={withPlaces ? (more ? 17 : 9) : more ? 16 : 8}>
                  <StockDetails itemId={row.item.id} place={place} />
                </td>
              </tr>
            ) : null}
            </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  // The chosen warehouses in the order of their list, each with what lies on it.
  const blocks = places
    .filter((p) => !picked || chosen.has(p.context_id))
    .map((p) => ({ place: p, stock: on(p.context_id) }));
  return (
    <>
      <Card>
        <div className="list-bar">
          {places.length > 1 ? (
            <div className="segmented" role="group" aria-label={t("stock.view")}>
              {VIEWS.map((key) => (
                <button
                  key={key}
                  type="button"
                  aria-pressed={view === key}
                  onClick={() => {
                    setView(key);
                    try {
                      window.localStorage.setItem(VIEW_KEY, key);
                    } catch {
                      // The view is then kept until the page is left.
                    }
                  }}
                >
                  {t(`stock.view_${key}`)}
                </button>
              ))}
            </div>
          ) : null}
          {/* By items the same part of several manufacturers is picked out of the list and
              compared (customer's decision of 20.11.2026). */}
          {view === "items" || places.length < 2 ? (
            <MultiPick
              options={all.map((row) => ({
                id: row.item.id,
                name: [row.item.name, row.item.article].filter(Boolean).join(" · "),
              }))}
              chosen={items}
              onChange={setItems}
              label={t("stock.item")}
              all={t("stock.all_items")}
              count={(n) => t("stock.items_count", { count: n })}
              search={t("stock.item_search")}
            />
          ) : null}
          {places.length > 1 && view === "places" ? (
            <MultiPick
              options={places.map((p) => ({ id: p.context_id, name: p.location_name }))}
              chosen={chosen}
              onChange={setChosen}
              label={t("stock.warehouse")}
              all={t("stock.all_warehouses")}
              count={(n) => t("stock.warehouses_count", { count: n })}
            />
          ) : null}
          <input
            className="list-search at-end"
            value={query}
            placeholder={t("warehouse.search")}
            aria-label={t("warehouse.search")}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {rows.isLoading ? <Loading /> : null}
        {view === "items" || places.length < 2 ? (
          <>
            {rows.data && all.length === 0 ? <p className="muted">{t("stock.empty")}</p> : null}
            {all.length ? table(items.size ? all.filter((row) => items.has(row.item.id)) : all, true) : null}
          </>
        ) : (
          blocks.map(({ place, stock }) => (
            <section key={place.context_id} className="stock-block" aria-label={place.location_name}>
              <h3>{place.location_name}</h3>
              {rows.data && stock.length === 0 ? <p className="muted">{t("stock.empty_here")}</p> : null}
              {stock.length ? table(stock, false, place.context_id) : null}
            </section>
          ))
        )}
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

/** A row of the stock unfolded: the units or the lots of the item warehouse by warehouse, with
 * their place marks, the repacking and the receipts, what is issued and still out, and the sale
 * prices (§ 11.1.10, § 11.2.13, § 11.7.4; customer's decision of 21.11.2026). Under a warehouse
 * only what lies on it is shown. */
function StockDetails({ itemId, place }: { itemId: string; place: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const quantity = useQuantity();
  const full = useApi<{ item: WarehouseItem }>(
    ["warehouse-item", company.public_id, itemId],
    `/c/${company.public_id}/warehouse/items/${itemId}`,
  );
  const data = useApi<{ stock: ItemStock }>(
    ["warehouse-item-stock", company.public_id, itemId],
    `/c/${company.public_id}/warehouse/items/${itemId}/stock`,
  );
  const item = full.data?.item;
  if (!item || !data.data) return <Loading />;
  const places = data.data.stock.places.filter((p) => !place || p.context_id === place);
  return (
    <>
      {places.map((p) => (
        <div key={p.context_id} className="stock-place">
          {place ? null : (
            <p>
              <strong>{p.location_name}</strong> — {quantity(p.quantity, item.unit)}
            </p>
          )}
          <PlaceDetails item={item} place={p} />
        </div>
      ))}
      <IssuedList item={item} />
      <PricesCard item={item} />
      {can("warehouse.catalog.manage") ? (
        <p className="stock-to-card">
          <Link to={companyPath(company, `/warehouse/items/${item.id}`)} state={{ from: "stock" }}>
            {t("stock.to_card")}
          </Link>
        </p>
      ) : null}
    </>
  );
}

/** The minimum of an item warehouse by warehouse, beside what lies there (§ 11.7.8). The units,
 * the lots and the prices are in the stock list (customer's decision of 21.11.2026). */
export function ItemStockCard({ item }: { item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const quantity = useQuantity();
  const data = useApi<{ stock: ItemStock }>(
    ["warehouse-item-stock", company.public_id, item.id],
    `/c/${company.public_id}/warehouse/items/${item.id}/stock`,
    can("warehouse.view"),
  );
  // Every warehouse has a line: what lies there and the minimum beside it (customer's remark of
  // 21.11.2026); a warehouse without the item is in the list too, for its minimum to be set.
  const prices = useApi<{ prices: ItemPrices }>(
    ["warehouse-prices", company.public_id, item.id],
    `/c/${company.public_id}/warehouse/items/${item.id}/prices`,
    can("warehouse.view"),
  );
  if (!can("warehouse.view")) return null;
  const places = data.data?.stock.places ?? [];
  const minimums = prices.data?.prices.minimums ?? [];
  const lines = [
    ...minimums.map((m) => ({ context_id: m.context_id, name: m.location_name, minimum: m.quantity as string | null, known: true })),
    ...places
      .filter((p) => !minimums.some((m) => m.context_id === p.context_id))
      .map((p) => ({ context_id: p.context_id, name: p.location_name, minimum: null as string | null, known: false })),
  ];
  return (
    <Card>
      {data.isLoading ? <Loading /> : null}
      {data.data && lines.length === 0 ? <p className="muted">{t("stock.item_empty")}</p> : null}
      {lines.length ? (
        <div className="place-line head muted small">
          <span>{t("warehouse.storage_warehouse")}</span>
          <span>{t("stock.rest")}</span>
          <span>
            {t("prices.minimum_title")} <Info text={t("prices.minimum_hint")} />
          </span>
        </div>
      ) : null}
      {lines.map((line) => {
        const place = places.find((p) => p.context_id === line.context_id);
        return (
          <div key={line.context_id} className="place-block">
            <div className="place-line">
              <strong>{line.name}</strong>
              <span>{quantity(place?.quantity ?? 0, item.unit)}</span>
              <span>
                {line.known ? (
                  <MinimumField item={item} contextId={line.context_id} value={line.minimum} />
                ) : (
                  "—"
                )}
              </span>
            </div>
          </div>
        );
      })}
    </Card>
  );
}

/** The minimum of an item on a warehouse (§ 11.7.8): empty — no minimum. */
function MinimumField({ item, contextId, value }: { item: WarehouseItem; contextId: string; value: string | null }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const save = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/warehouse/items/${item.id}/minimum`, "PUT");
  const saved = value ? String(Number(value)) : "";
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <span className="price-input">
      <input
        inputMode="decimal"
        aria-label={t("prices.minimum_title")}
        value={draft ?? saved}
        disabled={!can("warehouse.catalog.manage")}
        placeholder="—"
        onChange={(e) => setDraft(e.target.value)}
      />
      <span className="small muted">{t(`warehouse.unit_short_${item.unit}`)}</span>
      {draft !== null && draft !== saved ? (
        <button
          type="button"
          onClick={async () => {
            if ((await save.run({ context_id: contextId, quantity: draft.trim() === "" ? null : draft })) === null) return;
            await client.invalidateQueries({ queryKey: ["warehouse-prices", company.public_id, item.id] });
            await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
            setDraft(null);
          }}
        >
          {t("common.save")}
        </button>
      ) : null}
      <ErrorMessage error={save.error} />
    </span>
  );
}

/** The units and the lots of an item on one warehouse. */
function PlaceDetails({ item, place }: { item: WarehouseItem; place: ItemStock["places"][number] }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const quantity = useQuantity();
  const money = useMoney();
  return (
    <>
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
    </>
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
