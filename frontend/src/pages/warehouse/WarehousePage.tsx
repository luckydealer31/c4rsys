import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";

import { useApi } from "../../api/queries";
import type { Storage, Vehicle, WarehouseItem } from "../../api/types";
import { Icon } from "../../components/icons";
import { Card, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { useVehicles } from "../vehicles/VehiclesPage";
import { useCategoryLabel } from "./Categories";
import { InventoriesList } from "./Inventory";
import { OperationsList, WarehouseSettingsCard } from "./Operations";
import { VehiclePrice } from "./Prices";
import { PurchasesList } from "./Purchases";
import { ReceiptsList } from "./Receipts";
import { StockList } from "./Stock";

const TABS = ["stock", "items", "receipts", "operations", "inventory", "purchases", "storages"] as const;
type Tab = (typeof TABS)[number];

/** Section «Warehouse» (ТЗ гл. 11): stock, the catalog, receipts, purchases and the warehouses
 * of locations. PROPOSAL: the stock is the first tab. */
export function WarehousePage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const wanted = params.get("tab") as Tab | null;
  const tab: Tab = wanted && TABS.includes(wanted) ? wanted : "stock";
  return (
    <div className="page">
      <h1>{t("nav.warehouse")}</h1>
      <div className="tabs" role="tablist">
        {TABS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "active" : ""}
            onClick={() => setParams(key === "stock" ? {} : { tab: key })}
          >
            {t(`warehouse.tab_${key}`)}
          </button>
        ))}
      </div>
      {tab === "stock" ? <StockList /> : null}
      {tab === "items" ? <Items /> : null}
      {tab === "receipts" ? <ReceiptsList /> : null}
      {tab === "operations" ? <OperationsList /> : null}
      {tab === "inventory" ? <InventoriesList /> : null}
      {tab === "purchases" ? <PurchasesList /> : null}
      {tab === "storages" ? <Storages /> : null}
    </div>
  );
}

function Items() {
  const { t } = useTranslation();
  const categoryLabel = useCategoryLabel();
  const company = useCompany();
  const can = useCan();
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const items = useApi<{ items: WarehouseItem[] }>(
    ["warehouse-items", company.public_id, query, archived],
    `/c/${company.public_id}/warehouse/items?q=${encodeURIComponent(query)}&state=${archived ? "archived" : "active"}`,
    can("warehouse.view"),
  );
  if (!can("warehouse.view")) return <p className="muted">{t("warehouse.no_access")}</p>;
  return (
    <>
      {can("warehouse.catalog.manage") ? (
        <div className="actions" style={{ marginTop: 0, marginBottom: 16 }}>
          <Link className="button" to={companyPath(company, "/warehouse/items/new")}>
            <Icon name="plus" size={18} /> {t("warehouse.create_item")}
          </Link>
          {can("import.run") ? (
            <Link className="button secondary" to={companyPath(company, "/warehouse/import")}>
              {t("warehouse.import")}
            </Link>
          ) : null}
        </div>
      ) : null}
      <Card>
        <div className="row">
          <Field label={t("warehouse.search")}>
            <input value={query} onChange={(e) => setQuery(e.target.value)} />
          </Field>
          <label style={{ alignSelf: "end" }}>
            <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />{" "}
            {t("warehouse.show_archived")}
          </label>
        </div>
        {items.isLoading ? <Loading /> : null}
        {items.data?.items.length === 0 ? <p className="muted">{t("warehouse.no_items")}</p> : null}
        {items.data?.items.length ? (
          <table>
            <thead>
              <tr>
                <th>{t("warehouse.name")}</th>
                <th>{t("warehouse.article")}</th>
                <th>{t("categories.category")}</th>
                <th>{t("warehouse.unit")}</th>
                <th>{t("warehouse.manufacturer")}</th>
                <th>{t("warehouse.packagings")}</th>
              </tr>
            </thead>
            <tbody>
              {items.data.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link to={companyPath(company, `/warehouse/items/${item.id}`)}>{item.name}</Link>
                    {item.possible_duplicate ? (
                      <span className="badge warn" style={{ marginLeft: 6 }}>
                        {t("warehouse.possible_duplicate")}
                      </span>
                    ) : null}
                  </td>
                  <td>{item.article || "—"}</td>
                  <td>
                    {categoryLabel(item.part_category, item.part_subcategory) || "—"}
                    {item.category ? <div className="small muted">{item.category}</div> : null}
                  </td>
                  <td>{t(`warehouse.unit_short_${item.unit}`)}</td>
                  <td>{item.manufacturer_name || "—"}</td>
                  <td>
                    {item.packagings
                      .filter((p) => p.state === "active")
                      .map((p) => `${Number(p.quantity)} ${t(`warehouse.unit_short_${item.unit}`)}`)
                      .join(", ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Card>
    </>
  );
}

/** Warehouses of locations, each with its own stock (§ 11.1.1); a location has one at most. */
function Storages() {
  const { t } = useTranslation();
  const company = useCompany();
  const storages = useApi<{ storages: Storage[] }>(
    ["warehouse-storages", company.public_id],
    `/c/${company.public_id}/warehouse/storages`,
  );
  const vehicles = useVehicles();
  return (
    <>
      <WarehouseSettingsCard />
      {storages.isLoading ? <Loading /> : null}
      {storages.data?.storages.length === 0 ? (
        <Card>
          <p className="muted">{t("warehouse.no_storages")}</p>
        </Card>
      ) : null}
      {(storages.data?.storages ?? []).map((storage) => (
        <Card
          key={storage.context_id}
          title={`${t("warehouse.storage_warehouse")} — ${storage.location_name}`}
        >
          {storage.state !== "enabled" || storage.location_deactivated ? (
            <span className="badge warn">{t("locations.context_disabled")}</span>
          ) : null}
          <StoredVehicles
            items={(vehicles.data?.vehicles ?? []).filter((v) => v.storage?.context_id === storage.context_id)}
            path={(id) => companyPath(company, `/vehicles/${id}`)}
          />
        </Card>
      ))}
    </>
  );
}

/** Vehicles transferred to this warehouse as stock items (§ 8.1.5). */
function StoredVehicles({ items, path }: { items: Vehicle[]; path: (id: string) => string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const prices = useApi<{ prices: Record<string, string | null> }>(
    ["warehouse-vehicle-prices", company.public_id],
    `/c/${company.public_id}/warehouse/vehicle-prices`,
    items.length > 0,
  );
  if (items.length === 0) return null;
  return (
    <>
      <h3 style={{ marginTop: 12 }}>{t("vehicles.stored_title")}</h3>
      <table>
        <tbody>
          {items.map((v) => (
            <tr key={v.id}>
              <td>
                <Link to={path(v.id)}>{v.name}</Link> <span className="muted small">{v.vehicle_id}</span>
              </td>
              <td>
                <VehiclePrice vehicleId={v.id} value={prices.data?.prices[v.id] ?? ""} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
