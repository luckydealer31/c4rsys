import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { Storage } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Icon } from "../../components/icons";
import { ErrorMessage, Info } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";

/** A storage place of a warehouse (§ 11.1.9; customer's decision of 22.11.2026). */
export type StoragePlace = {
  id: string;
  version: number;
  context_id: string;
  name: string;
  state: "active" | "archived";
};

export function usePlaces() {
  const company = useCompany();
  const can = useCan();
  return useApi<{ places: StoragePlace[] }>(
    ["warehouse-places", company.public_id],
    `/c/${company.public_id}/warehouse/places`,
    can("warehouse.view"),
  );
}

/** The storage place of a unit or a lot: one of the places of its warehouse, or none. It is
 * chosen, not written — the list is kept in the settings of the warehouse. `value` — the names
 * of the place: none or one. */
export function PlaceSelect({
  contextId,
  value,
  onChange,
}: {
  contextId: string;
  value: string[];
  onChange: (names: string[]) => void;
}) {
  const { t } = useTranslation();
  const places = usePlaces();
  const current = value[0] ?? "";
  const names = (places.data?.places ?? [])
    .filter((p) => p.context_id === contextId && p.state === "active")
    .map((p) => p.name);
  // A place that is archived, or of another warehouse, is still shown as what was chosen.
  const shown = current && !names.includes(current) ? [current, ...names] : names;
  return (
    <span className="place-select">
      <select aria-label={t("stock.tags")} value={current} onChange={(e) => onChange(e.target.value ? [e.target.value] : [])}>
        <option value="">—</option>
        {shown.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
      {places.data && names.length === 0 ? <Info text={t("places.none_hint")} /> : null}
    </span>
  );
}

/** The storage places of every warehouse, in the settings of the warehouse — as the posts of a
 * service are kept. A place is added, renamed (for everything that lies there) and archived. */
export function PlacesSettings() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const places = usePlaces();
  const storages = useApi<{ storages: Storage[] }>(
    ["warehouse-storages", company.public_id],
    `/c/${company.public_id}/warehouse/storages`,
    can("warehouse.view"),
  );
  const warehouses = (storages.data?.storages ?? []).filter((s) => !s.location_deactivated);
  return (
    <>
      <h3>
        {t("places.title")} <Info text={t("places.hint")} />
      </h3>
      {warehouses.map((warehouse) => (
        <PlacesOf
          key={warehouse.context_id}
          warehouse={warehouse}
          places={(places.data?.places ?? []).filter((p) => p.context_id === warehouse.context_id)}
        />
      ))}
    </>
  );
}

function PlacesOf({ warehouse, places }: { warehouse: Storage; places: StoragePlace[] }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const manage = can("warehouse.policy.manage");
  const base = `/c/${company.public_id}/warehouse/places`;
  const add = useCommand<{ context_id: string; name: string }, unknown>(base);
  const change = useCommand<{ id: string; name?: string; archived?: boolean; expected_version: number }, unknown>(
    (body) => `${base}/${body.id}`,
    "PATCH",
  );
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);
  const refresh = async (result: unknown) => {
    if (result === null) return false;
    await client.invalidateQueries({ queryKey: ["warehouse-places", company.public_id] });
    await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
    await client.invalidateQueries({ queryKey: ["warehouse-item-stock", company.public_id] });
    return true;
  };
  const create = async () => {
    if (!name.trim()) return;
    if (await refresh(await add.run({ context_id: warehouse.context_id, name }))) setName("");
  };
  return (
    <section className="places-of" aria-label={warehouse.location_name}>
      <strong>{warehouse.location_name}</strong>
      {places.length === 0 ? <span className="muted small">{t("places.none")}</span> : null}
      <ul className="place-list">
        {places.map((place) =>
          editing?.id === place.id ? (
            <li key={place.id} className="editing">
              <input
                autoFocus
                maxLength={50}
                aria-label={t("places.name")}
                value={editing.name}
                onChange={(e) => setEditing({ id: place.id, name: e.target.value })}
              />
              <button
                type="button"
                disabled={change.pending || !editing.name.trim() || editing.name.trim() === place.name}
                onClick={async () => {
                  const body = { id: place.id, name: editing.name, expected_version: place.version };
                  if (await refresh(await change.run(body))) setEditing(null);
                }}
              >
                {t("common.save")}
              </button>
              <button
                type="button"
                className="secondary"
                disabled={change.pending}
                onClick={async () => {
                  const body = { id: place.id, archived: place.state === "active", expected_version: place.version };
                  if (await refresh(await change.run(body))) setEditing(null);
                }}
              >
                {place.state === "active" ? t("warehouse.to_archive") : t("warehouse.restore")}
              </button>
              <button type="button" className="secondary" onClick={() => setEditing(null)}>
                {t("common.cancel")}
              </button>
            </li>
          ) : (
            <li key={place.id} className={place.state === "archived" ? "muted" : ""}>
              {place.name}
              {place.state === "archived" ? <span className="small"> · {t("places.archived")}</span> : null}
              {manage ? (
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`${t("common.edit")}: ${place.name}`}
                  title={t("common.edit")}
                  onClick={() => setEditing({ id: place.id, name: place.name })}
                >
                  <Icon name="edit" size={14} />
                </button>
              ) : null}
            </li>
          ),
        )}
      </ul>
      {manage ? (
        <div className="barcode-line">
          <input
            maxLength={50}
            value={name}
            placeholder={t("places.new")}
            aria-label={t("places.new")}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void create();
              }
            }}
          />
          <button type="button" className="secondary" aria-label={t("articles.add")} title={t("articles.add")} disabled={add.pending || !name.trim()} onClick={() => void create()}>
            <Icon name="plus" size={16} />
          </button>
        </div>
      ) : null}
      <ErrorMessage error={add.error ?? change.error} />
    </section>
  );
}
