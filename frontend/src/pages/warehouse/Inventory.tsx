import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/queries";
import type { Inventory, ItemBrief, Storage, WarehouseItem } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { ItemPicker } from "./Receipts";
import { useQuantity } from "./Stock";

/** Inventories of the warehouses the employee may see: confirmed and unconfirmed with their
 * state (§ 11.6.3). A new one is started for one warehouse. */
export function InventoriesList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const list = useApi<{ inventories: Inventory[] }>(
    ["warehouse-inventories", company.public_id],
    `/c/${company.public_id}/warehouse/inventories`,
    can("warehouse.view"),
  );
  const storages = useApi<{ storages: Storage[] }>(
    ["warehouse-storages", company.public_id],
    `/c/${company.public_id}/warehouse/storages`,
  );
  const warehouses = (storages.data?.storages ?? []).filter((s) => s.state === "enabled" && !s.location_deactivated);
  const [contextId, setContextId] = useState("");
  const create = useCommand<{ context_id: string }, { inventory: Inventory }>(`/c/${company.public_id}/warehouse/inventories`);
  const chosen = contextId || (warehouses.length === 1 ? (warehouses[0]?.context_id ?? "") : "");
  if (!can("warehouse.view")) return <p className="muted">{t("warehouse.no_access")}</p>;
  return (
    <>
      {can("warehouse.count") ? (
        <Card title={t("inventory.new_title")}>
          <div className="row">
            <Field label={t("receipts.warehouse")}>
              <select value={chosen} onChange={(e) => setContextId(e.target.value)}>
                <option value="">—</option>
                {warehouses.map((w) => (
                  <option key={w.context_id} value={w.context_id}>
                    {w.location_name}
                  </option>
                ))}
              </select>
            </Field>
            <button
              type="button"
              style={{ alignSelf: "end", flex: "0 0 auto" }}
              disabled={!chosen || create.pending}
              onClick={async () => {
                const result = await create.run({ context_id: chosen });
                if (result) navigate(companyPath(company, `/warehouse/inventories/${result.inventory.id}`));
              }}
            >
              {t("inventory.start")}
            </button>
          </div>
          <p className="muted small">{t("inventory.hint")}</p>
          <ErrorMessage error={create.error} />
        </Card>
      ) : null}
      <Card>
        {list.isLoading ? <Loading /> : null}
        {list.data?.inventories.length === 0 ? <p className="muted">{t("inventory.none")}</p> : null}
        {list.data?.inventories.length ? (
          <table>
            <thead>
              <tr>
                <th>{t("receipts.number_short")}</th>
                <th>{t("purchases.created_at")}</th>
                <th>{t("receipts.warehouse")}</th>
                <th>{t("inventory.positions")}</th>
                <th>{t("inventory.counted_by")}</th>
                <th>{t("receipts.state")}</th>
              </tr>
            </thead>
            <tbody>
              {list.data.inventories.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link to={companyPath(company, `/warehouse/inventories/${c.id}`)}>№ {c.number}</Link>
                  </td>
                  <td>{new Date(c.created_at).toLocaleDateString()}</td>
                  <td>{c.context.location_name}</td>
                  <td>{c.lines.length}</td>
                  <td className="small">{c.counted_by.join(", ") || c.created_by}</td>
                  <td>
                    <span className={`badge${c.state === "confirmed" ? " ok" : c.state === "draft" ? "" : " warn"}`}>
                      {t(`inventory.state_${c.state}`)}
                    </span>
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

/** One inventory: positions with the book stock at the moment of the count, the counted and the
 * discrepancy; found items; submission, confirmation with a reason or rejection (§ 11.6). */
export function InventoryPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const quantity = useQuantity();
  const base = `/c/${company.public_id}/warehouse/inventories/${id}`;
  const data = useApi<{ inventory: Inventory }>(["warehouse-inventory", company.public_id, id], base);
  const items = useCommand<Record<string, unknown>, unknown>(`${base}/items`);
  const counted = useCommand<Record<string, unknown>, unknown>(`${base}/counted`);
  const found = useCommand<Record<string, unknown>, unknown>(`${base}/found`);
  const remove = useCommand<{ id: string }, unknown>((b) => `${base}/lines/${b.id}`, "DELETE");
  const submit = useCommand<Record<string, never>, unknown>(`${base}/submit`);
  const confirm = useCommand<{ reason: string }, unknown>(`${base}/confirm`);
  const reject = useCommand<{ reason: string }, unknown>(`${base}/reject`);
  const [values, setValues] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [foundKind, setFoundKind] = useState<"found_unit" | "found_elsewhere" | "found_vehicle">("found_unit");
  const [foundItem, setFoundItem] = useState<ItemBrief | null>(null);
  const [foundText, setFoundText] = useState({ description: "", identifiers: "", asset_public_id: "" });
  if (!data.data) return <div className="page"><Loading /></div>;
  const inventory = data.data.inventory;
  const editable = (inventory.state === "draft" || inventory.state === "rejected") && can("warehouse.count");
  const deciding = inventory.state === "submitted" && can("warehouse.adjust.confirm");
  const commands = [items, counted, found, remove, submit, confirm, reject];
  const pending = commands.some((c) => c.pending);
  const error = commands.map((c) => c.error).find(Boolean) ?? null;

  async function refresh(result: unknown) {
    if (result === null) return false;
    await client.invalidateQueries({ queryKey: ["warehouse-inventory", company.public_id, id] });
    await client.invalidateQueries({ queryKey: ["warehouse-inventories", company.public_id] });
    await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
    return true;
  }

  async function saveCounts() {
    const lines = Object.entries(values)
      .filter(([, value]) => value !== "")
      .map(([line_id, value]) => ({ line_id, quantity: value }));
    if (lines.length && (await refresh(await counted.run({ lines })))) setValues({});
  }

  const counting = inventory.lines.filter((l) => l.kind === "asset" || l.kind === "lot");
  const finds = inventory.lines.filter((l) => l.kind !== "asset" && l.kind !== "lot");
  const unit = (line: Inventory["lines"][number]) => line.item?.unit ?? "piece";

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/warehouse?tab=inventory")}>← {t("warehouse.tab_inventory")}</Link>
      </p>
      <h1>
        {t("inventory.title", { number: inventory.number })}{" "}
        <span className={`badge${inventory.state === "confirmed" ? " ok" : inventory.state === "draft" ? "" : " warn"}`}>
          {t(`inventory.state_${inventory.state}`)}
        </span>
      </h1>
      <Card>
        <p>
          {inventory.context.location_name} · {new Date(inventory.created_at).toLocaleString()} · {inventory.created_by}
        </p>
        {inventory.counted_by.length ? <p className="small">{t("inventory.counted_by")}: {inventory.counted_by.join(", ")}</p> : null}
        {inventory.decided_at ? (
          <p className="small">
            {t(inventory.state === "confirmed" ? "inventory.confirmed_by" : "inventory.rejected_by", {
              name: inventory.decided_by,
              date: new Date(inventory.decided_at).toLocaleString(),
            })}
            {inventory.decision_reason ? ` · ${inventory.decision_reason}` : ""}
          </p>
        ) : null}
      </Card>
      <Card title={t("inventory.positions")}>
        {editable ? (
          <div className="actions" style={{ marginTop: 0 }}>
            <button type="button" className="secondary" disabled={pending} onClick={async () => void refresh(await items.run({ all: true }))}>
              {t("inventory.add_all")}
            </button>
          </div>
        ) : null}
        {editable ? (
          <ItemPicker onPick={async (item: WarehouseItem) => void refresh(await items.run({ item_ids: [item.id] }))} />
        ) : null}
        {counting.length === 0 ? <p className="muted">{t("inventory.no_positions")}</p> : null}
        {counting.length ? (
          <table>
            <thead>
              <tr>
                <th>{t("warehouse.name")}</th>
                <th>{t("operations.what")}</th>
                <th>{t("inventory.counted")}</th>
                <th>{t("inventory.book")}</th>
                <th>{t("inventory.discrepancy")}</th>
                {editable ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {counting.map((line) => (
                <tr key={line.id}>
                  <td>{line.item?.name}</td>
                  <td className="small">
                    {line.asset ?? line.lot}
                    {line.packaging_quantity && !line.opened_package_id
                      ? ` · ${t("inventory.closed_packs", { size: quantity(line.packaging_quantity, unit(line)) })}`
                      : ""}
                    {line.opened_package_id ? ` · ${t("operations.opened")}` : ""}
                    {line.lot && !line.packaging_quantity ? ` · ${t("inventory.loose")}` : ""}
                  </td>
                  <td>
                    {editable ? (
                      line.kind === "asset" ? (
                        <select
                          value={values[line.id] ?? (line.counted_quantity === null ? "" : String(Number(line.counted_quantity)))}
                          onChange={(e) => setValues({ ...values, [line.id]: e.target.value })}
                        >
                          <option value="">—</option>
                          <option value="1">{t("inventory.unit_found")}</option>
                          <option value="0">{t("inventory.unit_missing")}</option>
                        </select>
                      ) : (
                        <input
                          inputMode="decimal"
                          style={{ width: 100 }}
                          value={values[line.id] ?? (line.counted_quantity === null ? "" : String(Number(line.counted_quantity)))}
                          onChange={(e) => setValues({ ...values, [line.id]: e.target.value })}
                        />
                      )
                    ) : line.counted_quantity !== null ? (
                      quantity(line.counted_quantity, unit(line))
                    ) : (
                      "—"
                    )}
                    {line.counted_at ? <div className="small muted">{new Date(line.counted_at).toLocaleString()}</div> : null}
                  </td>
                  <td>{line.book_quantity !== null ? quantity(line.book_quantity, unit(line)) : "—"}</td>
                  <td>
                    {line.discrepancy !== null && Number(line.discrepancy) !== 0 ? (
                      <span className="badge warn">
                        {Number(line.discrepancy) > 0 ? "+" : ""}
                        {quantity(line.discrepancy, unit(line))}
                      </span>
                    ) : line.discrepancy !== null ? (
                      "0"
                    ) : (
                      "—"
                    )}
                  </td>
                  {editable ? (
                    <td>
                      <button type="button" className="link" onClick={async () => void refresh(await remove.run({ id: line.id }))}>
                        {t("receipts.remove_line")}
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        {editable && counting.length ? (
          <div className="actions">
            <button type="button" disabled={pending || !Object.keys(values).length} onClick={() => void saveCounts()}>
              {t("inventory.save_counts")}
            </button>
          </div>
        ) : null}
        <p className="muted small">{t("inventory.count_hint")}</p>
      </Card>
      <Card title={t("inventory.found_title")}>
        {finds.length ? (
          <ul>
            {finds.map((line) => (
              <li key={line.id}>
                <strong>{t(`inventory.kind_${line.kind}`)}</strong>
                {line.item ? ` · ${line.item.name}` : ""}
                {line.asset ? ` · ${line.asset}` : ""}
                {line.description ? ` · ${line.description}` : ""}
                {line.identifiers ? ` · ${line.identifiers}` : ""}
                {line.created_asset ? ` → ${line.created_asset}` : ""}{" "}
                {editable ? (
                  <button type="button" className="link" onClick={async () => void refresh(await remove.run({ id: line.id }))}>
                    {t("receipts.remove_line")}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{t("inventory.no_found")}</p>
        )}
        {editable ? (
          <div className="line-card">
            <Field label={t("inventory.found_kind")}>
              <select value={foundKind} onChange={(e) => setFoundKind(e.target.value as typeof foundKind)}>
                <option value="found_unit">{t("inventory.kind_found_unit")}</option>
                <option value="found_elsewhere">{t("inventory.kind_found_elsewhere")}</option>
                <option value="found_vehicle">{t("inventory.kind_found_vehicle")}</option>
              </select>
            </Field>
            <p className="muted small">{t(`inventory.hint_${foundKind}`)}</p>
            {foundKind === "found_unit" ? (
              foundItem ? (
                <p>
                  {foundItem.name}{" "}
                  <button type="button" className="link" onClick={() => setFoundItem(null)}>
                    {t("receipts.remove_line")}
                  </button>
                </p>
              ) : (
                <ItemPicker onPick={(item: WarehouseItem) => setFoundItem(item)} />
              )
            ) : null}
            {foundKind === "found_elsewhere" ? (
              <Field label={t("stock.asset_id")}>
                <input value={foundText.asset_public_id} placeholder="AST-…" onChange={(e) => setFoundText({ ...foundText, asset_public_id: e.target.value })} />
              </Field>
            ) : (
              <div className="row">
                <Field label={t("inventory.description")}>
                  <input value={foundText.description} onChange={(e) => setFoundText({ ...foundText, description: e.target.value })} />
                </Field>
                <Field label={t("inventory.identifiers")}>
                  <input value={foundText.identifiers} onChange={(e) => setFoundText({ ...foundText, identifiers: e.target.value })} />
                </Field>
              </div>
            )}
            <button
              type="button"
              className="secondary"
              disabled={pending}
              onClick={async () => {
                const ok = await refresh(await found.run({ kind: foundKind, item_id: foundItem?.id ?? null, ...foundText }));
                if (ok) {
                  setFoundItem(null);
                  setFoundText({ description: "", identifiers: "", asset_public_id: "" });
                }
              }}
            >
              {t("inventory.add_found")}
            </button>
          </div>
        ) : null}
      </Card>
      <ErrorMessage error={error} />
      {editable ? (
        <div className="actions">
          <button type="button" disabled={pending} onClick={async () => void refresh(await submit.run({}))}>
            {t("inventory.submit")}
          </button>
        </div>
      ) : null}
      {deciding ? (
        <Card title={t("inventory.decision_title")}>
          <Field label={t("inventory.reason")} hint={t("inventory.decision_hint")}>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="button" disabled={pending || !reason.trim()} onClick={async () => void refresh(await confirm.run({ reason }))}>
              {t("inventory.confirm")}
            </button>
            <button type="button" className="secondary" disabled={pending || !reason.trim()} onClick={async () => void refresh(await reject.run({ reason }))}>
              {t("inventory.reject")}
            </button>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
