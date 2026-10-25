import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { ItemPrices, PriceEntry, WarehouseItem } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { useQuantity } from "./Stock";

/** Forms that have a price: loose sale of the base unit (a piece for pieces) and each active
 * packaging — independent prices (§ 11.7.5). */
function useForms(item: WarehouseItem) {
  const { t } = useTranslation();
  const quantity = useQuantity();
  const unit = t(`warehouse.unit_short_${item.unit}`);
  return [
    { packaging_id: null as string | null, label: item.accounting === "batch" ? t("prices.loose", { unit }) : t("prices.piece") },
    ...item.packagings
      .filter((p) => p.state === "active")
      .map((p) => ({ packaging_id: p.id as string | null, label: t("prices.pack", { size: quantity(p.quantity, item.unit), name: p.name }) })),
  ];
}

function amountOf(entries: PriceEntry[], packagingId: string | null) {
  return entries.find((e) => e.packaging_id === packagingId)?.amount ?? "";
}

/** One price with its own input; empty clears it. */
function PriceInput({
  value,
  currency,
  disabled,
  onSave,
}: {
  value: string;
  currency: string;
  disabled: boolean;
  onSave: (amount: string | null) => Promise<boolean>;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? value;
  return (
    <span className="row" style={{ alignItems: "center", flexWrap: "nowrap" }}>
      <input
        inputMode="decimal"
        style={{ width: 120 }}
        value={shown}
        disabled={disabled}
        placeholder={t("prices.not_set")}
        onChange={(e) => setDraft(e.target.value)}
      />
      <span className="small muted" style={{ flex: "0 0 auto" }}>
        {currency}
      </span>
      {draft !== null && draft !== value ? (
        <button
          type="button"
          style={{ flex: "0 0 auto" }}
          onClick={async () => {
            if (await onSave(draft.trim() === "" ? null : draft)) setDraft(null);
          }}
        >
          {t("common.save")}
        </button>
      ) : null}
    </span>
  );
}

/** Selling prices of an item (§ 11.7.4–11.7.6): by the item or by lots for a material, the
 * price of a piece and own prices of units for pieces. */
export function PricesCard({ item }: { item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const forms = useForms(item);
  const base = `/c/${company.public_id}/warehouse`;
  const data = useApi<{ prices: ItemPrices }>(["warehouse-prices", company.public_id, item.id], `${base}/items/${item.id}/prices`);
  const setPrice = useCommand<Record<string, unknown>, unknown>(`${base}/items/${item.id}/price`, "PUT");
  const setMode = useCommand<Record<string, unknown>, unknown>(`${base}/items/${item.id}/price-mode`);
  const setAsset = useCommand<{ id: string; amount: string | null }, unknown>((b) => `${base}/assets/${b.id}/price`, "PUT");
  const [newPrices, setNewPrices] = useState<Record<string, string>>({});
  const prices = data.data?.prices;
  if (!prices) return null;
  const editable = can("warehouse.price.set");
  const refresh = async (result: unknown) => {
    if (result === null) return false;
    await client.invalidateQueries({ queryKey: ["warehouse-prices", company.public_id, item.id] });
    return true;
  };
  const save = (lotId: string | null, packagingId: string | null) => async (amount: string | null) =>
    refresh(await setPrice.run({ lot_id: lotId, packaging_id: packagingId, amount }));

  return (
    <Card title={t("prices.title")}>
      {item.accounting === "batch" ? (
        <p>
          {t("prices.mode")}: <strong>{t(`prices.mode_${prices.mode}`)}</strong>
        </p>
      ) : null}
      {prices.mode === "item" ? (
        <table>
          <tbody>
            {forms.map((form) => (
              <tr key={form.packaging_id ?? "loose"}>
                <td>{form.label}</td>
                <td>
                  <PriceInput
                    value={amountOf(prices.item_prices, form.packaging_id)}
                    currency={prices.currency}
                    disabled={!editable}
                    onSave={save(null, form.packaging_id)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        prices.lots.map((lot) => (
          <div key={lot.id} className="line-card">
            <h3>{lot.public_id}</h3>
            <table>
              <tbody>
                {forms.map((form) => (
                  <tr key={form.packaging_id ?? "loose"}>
                    <td>{form.label}</td>
                    <td>
                      <PriceInput
                        value={amountOf(lot.prices, form.packaging_id)}
                        currency={prices.currency}
                        disabled={!editable}
                        onSave={save(lot.id, form.packaging_id)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      )}
      {item.accounting === "individual" && prices.assets.length ? (
        <>
          <h3>{t("prices.own_title")}</h3>
          <p className="muted small">{t("prices.own_hint")}</p>
          <table>
            <tbody>
              {prices.assets.map((asset) => (
                <tr key={asset.id}>
                  <td>{asset.public_id}</td>
                  <td>
                    <PriceInput
                      value={asset.sale_price ?? ""}
                      currency={prices.currency}
                      disabled={!editable}
                      onSave={async (amount) => refresh(await setAsset.run({ id: asset.id, amount }))}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
      {item.accounting === "batch" && can("warehouse.price.policy") ? (
        <div className="line-card">
          {prices.mode === "item" ? (
            <>
              <p className="small">{t("prices.to_lots_hint")}</p>
              <button type="button" className="secondary" disabled={setMode.pending} onClick={async () => void refresh(await setMode.run({ mode: "lot" }))}>
                {t("prices.to_lots")}
              </button>
            </>
          ) : (
            <>
              <p className="small">{t("prices.to_item_hint")}</p>
              {forms.map((form) => (
                <Field key={form.packaging_id ?? "loose"} label={form.label}>
                  <input
                    inputMode="decimal"
                    value={newPrices[form.packaging_id ?? ""] ?? ""}
                    onChange={(e) => setNewPrices({ ...newPrices, [form.packaging_id ?? ""]: e.target.value })}
                  />
                </Field>
              ))}
              <button
                type="button"
                className="secondary"
                disabled={setMode.pending}
                onClick={async () => {
                  const given = Object.entries(newPrices)
                    .filter(([, amount]) => amount.trim() !== "")
                    .map(([packaging, amount]) => ({ packaging_id: packaging || null, amount }));
                  if (await refresh(await setMode.run({ mode: "item", prices: given }))) setNewPrices({});
                }}
              >
                {t("prices.to_item")}
              </button>
            </>
          )}
        </div>
      ) : null}
      <p className="muted small">{t("prices.snapshot_hint")}</p>
      <ErrorMessage error={setPrice.error ?? setMode.error ?? setAsset.error} />
    </Card>
  );
}

/** A minimum per warehouse (§ 11.7.8): below it the stock is marked. */
export function MinimumsCard({ item }: { item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const data = useApi<{ prices: ItemPrices }>(
    ["warehouse-prices", company.public_id, item.id],
    `/c/${company.public_id}/warehouse/items/${item.id}/prices`,
  );
  const save = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/warehouse/items/${item.id}/minimum`, "PUT");
  const minimums = data.data?.prices.minimums ?? [];
  if (!minimums.length) return null;
  return (
    <Card title={t("prices.minimum_title")}>
      <table>
        <tbody>
          {minimums.map((m) => (
            <tr key={m.context_id}>
              <td>
                {t("warehouse.storage_warehouse")} — {m.location_name}
              </td>
              <td>
                <PriceInput
                  value={m.quantity ? String(Number(m.quantity)) : ""}
                  currency={t(`warehouse.unit_short_${item.unit}`)}
                  disabled={!can("warehouse.catalog.manage")}
                  onSave={async (quantity) => {
                    const result = await save.run({ context_id: m.context_id, quantity });
                    if (result === null) return false;
                    await client.invalidateQueries({ queryKey: ["warehouse-prices", company.public_id, item.id] });
                    await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
                    return true;
                  }}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">{t("prices.minimum_hint")}</p>
      <ErrorMessage error={save.error} />
    </Card>
  );
}

/** The price of a vehicle transferred to a warehouse (§ 11.7.4). */
export function VehiclePrice({ vehicleId, value }: { vehicleId: string; value: string }) {
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const save = useCommand<{ amount: string | null }, unknown>(`/c/${company.public_id}/warehouse/vehicles/${vehicleId}/price`, "PUT");
  return (
    <>
      <PriceInput
        value={value}
        currency={company.main_currency ?? ""}
        disabled={!can("warehouse.price.set")}
        onSave={async (amount) => {
          const result = await save.run({ amount });
          if (result === null) return false;
          await client.invalidateQueries({ queryKey: ["warehouse-vehicle-prices", company.public_id] });
          return true;
        }}
      />
      <ErrorMessage error={save.error} />
    </>
  );
}
