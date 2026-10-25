import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";

import { useApi } from "../../api/queries";
import type { Storage, Vehicle, WarehouseItem } from "../../api/types";
import { Icon } from "../../components/icons";
import { Card, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { useVehicles } from "../vehicles/VehiclesPage";

type Tab = "items" | "storages";

/** Section «Warehouse» (ТЗ гл. 11), part С1: the catalog of the company and its places of
 * storage; stock appears with receipts (part С2). */
export function WarehousePage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "storages" ? "storages" : "items";
  return (
    <div className="page">
      <h1>{t("nav.warehouse")}</h1>
      <div className="tabs" role="tablist">
        {(["items", "storages"] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "active" : ""}
            onClick={() => setParams(key === "items" ? {} : { tab: key })}
          >
            {t(`warehouse.tab_${key}`)}
          </button>
        ))}
      </div>
      {tab === "items" ? <Items /> : <Storages />}
    </div>
  );
}

function Items() {
  const { t } = useTranslation();
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
                <th>{t("warehouse.category")}</th>
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
                  <td>{item.category || "—"}</td>
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

/** Contexts with storage, each with its own stock (§ 11.1.1, § 11.1.8). */
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
      {storages.isLoading ? <Loading /> : null}
      {storages.data?.storages.length === 0 ? (
        <Card>
          <p className="muted">{t("warehouse.no_storages")}</p>
        </Card>
      ) : null}
      {(storages.data?.storages ?? []).map((storage) => (
        <Card
          key={storage.context_id}
          title={`${t(`warehouse.storage_${storage.type}`)} — ${storage.location_name}`}
        >
          {storage.state !== "enabled" || storage.location_deactivated ? (
            <span className="badge warn">{t("locations.context_disabled")}</span>
          ) : null}
          <p className="muted small">{t("warehouse.stock_later")}</p>
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
  if (items.length === 0) return null;
  return (
    <>
      <h3 style={{ marginTop: 12 }}>{t("vehicles.stored_title")}</h3>
      <ul>
        {items.map((v) => (
          <li key={v.id}>
            <Link to={path(v.id)}>{v.name}</Link> <span className="muted small">{v.vehicle_id}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
