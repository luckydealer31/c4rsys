import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/queries";
import type { CatalogMake, CatalogModel, WarehouseItem, WarehouseOptions } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ImagePicker } from "../../components/images";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { MinimumsCard, PricesCard } from "./Prices";
import { ItemStockCard, SupplierArticles } from "./Stock";

const OTHER = "__other";

type PairKey = "side" | "longitudinal" | "vertical" | "placement";
type ChoiceKey = "originality" | "condition";
// Attributes of a spare part as toggle buttons (decision of 25.10.2026): both pressed — «both»,
// none — not given. Originality and condition: one of two or none.
const PAIRS: [PairKey, string, string][] = [
  ["side", "left", "right"],
  ["longitudinal", "front", "rear"],
  ["vertical", "upper", "lower"],
  ["placement", "inner", "outer"],
];
const CHOICES: [ChoiceKey, string, string][] = [
  ["originality", "original", "analog"],
  ["condition", "new", "used"],
];
const TEXTS = ["article", "category", "model_characteristic", "photo_url", "comment", "external_id"] as const;

type Form = {
  name: string;
  unit: string;
  manufacturer: string;
  manufacturer_other: string;
  photo: string | null;
  applicability: { make: string; model: string }[];
} & Record<PairKey | ChoiceKey | (typeof TEXTS)[number], string>;

function formOf(item?: WarehouseItem): Form {
  return {
    name: item?.name ?? "",
    unit: item?.unit ?? "piece",
    manufacturer: item?.manufacturer ?? (item?.manufacturer_other ? OTHER : ""),
    manufacturer_other: item?.manufacturer_other ?? "",
    side: item?.side ?? "",
    longitudinal: item?.longitudinal ?? "",
    vertical: item?.vertical ?? "",
    placement: item?.placement ?? "",
    originality: item?.originality ?? "",
    condition: item?.condition ?? "",
    article: item?.article ?? "",
    category: item?.category ?? "",
    model_characteristic: item?.model_characteristic ?? "",
    photo_url: item?.photo_url ?? "",
    comment: item?.comment ?? "",
    external_id: item?.external_id ?? "",
    photo: item?.photo ?? null,
    applicability: (item?.applicability ?? []).map((a) => ({ make: a.make, model: a.model ?? "" })),
  };
}

/** Next value of a pair: one, the other, both or none. */
function togglePair(value: string, pressed: string, other: string): string {
  const on = new Set(value === "both" ? [pressed, other] : value ? [value] : []);
  if (on.has(pressed)) on.delete(pressed);
  else on.add(pressed);
  return on.size === 2 ? "both" : ([...on][0] ?? "");
}

/** New item of the catalog (§ 11.1.11): name and base unit are required; the accounting method
 * follows the unit. */
export function NewItemPage() {
  return <ItemForm />;
}

/** Item card: its data, packagings and barcodes (§ 11.1.11–11.1.16, § 22.4.1). */
export function ItemPage() {
  const company = useCompany();
  const { id = "" } = useParams();
  const item = useApi<{ item: WarehouseItem }>(
    ["warehouse-item", company.public_id, id],
    `/c/${company.public_id}/warehouse/items/${id}`,
  );
  if (!item.data) {
    return (
      <div className="page">
        <Loading />
      </div>
    );
  }
  return (
    <>
      <ItemForm key={item.data.item.version} item={item.data.item} />
      <div className="page" style={{ paddingTop: 0 }}>
        <ItemStockCard item={item.data.item} />
        <PricesCard item={item.data.item} />
        <MinimumsCard item={item.data.item} />
        {item.data.item.accounting === "batch" ? <Packagings item={item.data.item} /> : null}
        <Barcodes item={item.data.item} />
        <SupplierArticles item={item.data.item} />
        <ItemState item={item.data.item} />
      </div>
    </>
  );
}

function useRefresh(item?: WarehouseItem) {
  const client = useQueryClient();
  const company = useCompany();
  return async () => {
    await client.invalidateQueries({ queryKey: ["warehouse-items", company.public_id] });
    await client.invalidateQueries({ queryKey: ["warehouse-categories", company.public_id] });
    if (item) await client.invalidateQueries({ queryKey: ["warehouse-item", company.public_id, item.id] });
  };
}

function ToggleButton({
  pressed,
  disabled,
  label,
  onClick,
}: {
  pressed: boolean;
  disabled: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`toggle${pressed ? " on" : ""}`}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

function ItemForm({ item }: { item?: WarehouseItem }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const refresh = useRefresh(item);
  const options = useApi<WarehouseOptions>(["warehouse-options"], "/catalog/warehouse-options");
  const manufacturers = useApi<{ manufacturers: { id: string; name: string }[] }>(
    ["manufacturers"],
    "/catalog/manufacturers",
  );
  const makes = useApi<{ makes: CatalogMake[] }>(
    ["vehicle-makes", "passenger_car"],
    "/catalog/vehicle-makes?type=passenger_car",
  );
  const categories = useApi<{ categories: string[] }>(
    ["warehouse-categories", company.public_id],
    `/c/${company.public_id}/warehouse/categories`,
  );
  const [form, setForm] = useState<Form>(formOf(item));
  const [saved, setSaved] = useState(false);
  const save = useCommand<Record<string, unknown>, { item: WarehouseItem }>(
    item ? `/c/${company.public_id}/warehouse/items/${item.id}` : `/c/${company.public_id}/warehouse/items`,
    item ? "PATCH" : "POST",
  );
  const editable = can("warehouse.catalog.manage");
  const set = (changes: Partial<Form>) => setForm((prev) => ({ ...prev, ...changes }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    const body: Record<string, unknown> = {
      name: form.name,
      manufacturer: form.manufacturer && form.manufacturer !== OTHER ? form.manufacturer : "",
      manufacturer_other: form.manufacturer === OTHER ? form.manufacturer_other : "",
      photo: form.photo ?? "",
      applicability: form.applicability
        .filter((a) => a.make)
        .map((a) => (a.model ? { make: a.make, model: a.model } : { make: a.make })),
    };
    for (const [key] of PAIRS) body[key] = form[key];
    for (const [key] of CHOICES) body[key] = form[key];
    for (const key of TEXTS) body[key] = form[key];
    if (item) body.expected_version = item.version;
    else body.unit = form.unit;
    const result = await save.run(body);
    if (result) {
      await refresh();
      if (!item) navigate(companyPath(company, `/warehouse/items/${result.item.id}`));
      else setSaved(true);
    }
  }

  const text = (key: (typeof TEXTS)[number], hint?: string) => (
    <Field label={t(`warehouse.${key}`)} hint={hint}>
      <input
        value={form[key]}
        disabled={!editable}
        list={key === "category" ? "warehouse-categories" : undefined}
        onChange={(e) => set({ [key]: e.target.value })}
      />
    </Field>
  );

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/warehouse")}>← {t("nav.warehouse")}</Link>
      </p>
      <h1>{item ? item.name : t("warehouse.create_item")}</h1>
      <form className="stack" onSubmit={submit}>
        <Card title={t("warehouse.section_main")}>
          <div className="row">
            <Field label={t("warehouse.name")}>
              <input value={form.name} disabled={!editable} onChange={(e) => set({ name: e.target.value })} />
            </Field>
            <Field label={t("warehouse.unit")} hint={t("warehouse.unit_hint")}>
              <select value={form.unit} disabled={Boolean(item)} onChange={(e) => set({ unit: e.target.value })}>
                {(options.data?.units ?? []).map((u) => (
                  <option key={u.code} value={u.code}>
                    {t(`warehouse.unit_${u.code}`)}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="row">
            {text("article")}
            {text("category")}
            <datalist id="warehouse-categories">
              {(categories.data?.categories ?? []).map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </div>
          <div className="row">
            <Field label={t("warehouse.manufacturer")}>
              <select
                value={form.manufacturer}
                disabled={!editable}
                onChange={(e) => set({ manufacturer: e.target.value })}
              >
                <option value="">{t("warehouse.not_given")}</option>
                {(manufacturers.data?.manufacturers ?? []).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
                <option value={OTHER}>{t("warehouse.manufacturer_other")}</option>
              </select>
            </Field>
            {form.manufacturer === OTHER ? (
              <Field label={t("warehouse.manufacturer_name")}>
                <input
                  value={form.manufacturer_other}
                  disabled={!editable}
                  onChange={(e) => set({ manufacturer_other: e.target.value })}
                />
              </Field>
            ) : null}
            {text("model_characteristic")}
          </div>
        </Card>
        <Card title={t("warehouse.section_parts")}>
          <p className="muted small">{t("warehouse.toggles_hint")}</p>
          <div className="toggle-grid">
            {PAIRS.map(([key, a, b]) => (
              <div key={key} className="toggle-row">
                <span className="muted small">{t(`warehouse.attr_${key}`)}</span>
                {[a, b].map((value) => (
                  <ToggleButton
                    key={value}
                    pressed={form[key] === value || form[key] === "both"}
                    disabled={!editable}
                    label={t(`warehouse.value_${value}`)}
                    onClick={() => set({ [key]: togglePair(form[key], value, value === a ? b : a) })}
                  />
                ))}
              </div>
            ))}
            {CHOICES.map(([key, a, b]) => (
              <div key={key} className="toggle-row">
                <span className="muted small">{t(`warehouse.attr_${key}`)}</span>
                {[a, b].map((value) => (
                  <ToggleButton
                    key={value}
                    pressed={form[key] === value}
                    disabled={!editable}
                    label={t(`warehouse.value_${value}`)}
                    onClick={() => set({ [key]: form[key] === value ? "" : value })}
                  />
                ))}
              </div>
            ))}
          </div>
          <div className="field" style={{ marginTop: 12 }}>
            <span>{t("warehouse.applicability")}</span>
            {form.applicability.map((row, index) => (
              <ApplicabilityRow
                key={index}
                row={row}
                makes={makes.data?.makes ?? []}
                disabled={!editable}
                locale={i18n.language}
                onChange={(next) => {
                  const rows = [...form.applicability];
                  rows[index] = next;
                  set({ applicability: rows });
                }}
                onRemove={() => set({ applicability: form.applicability.filter((_, i) => i !== index) })}
              />
            ))}
            {editable ? (
              <button
                type="button"
                className="secondary"
                onClick={() => set({ applicability: [...form.applicability, { make: "", model: "" }] })}
              >
                {t("warehouse.add_applicability")}
              </button>
            ) : null}
          </div>
        </Card>
        <Card title={t("warehouse.section_more")}>
          <Field label={t("warehouse.photo")}>
            <ImagePicker
              purpose="item_photo"
              value={form.photo}
              name={form.name}
              square
              disabled={!editable}
              onChange={(photo) => set({ photo })}
            />
          </Field>
          <div className="row">
            {text("photo_url")}
            {text("external_id", t("warehouse.external_id_hint"))}
          </div>
          <Field label={t("warehouse.comment")}>
            <textarea rows={3} value={form.comment} disabled={!editable} onChange={(e) => set({ comment: e.target.value })} />
          </Field>
        </Card>
        <ErrorMessage error={save.error} />
        {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
        {editable ? (
          <div className="actions">
            <button type="submit" disabled={!form.name.trim() || save.pending}>
              {item ? t("common.save") : t("warehouse.create_item")}
            </button>
          </div>
        ) : null}
      </form>
    </div>
  );
}

function ApplicabilityRow({
  row,
  makes,
  disabled,
  locale,
  onChange,
  onRemove,
}: {
  row: { make: string; model: string };
  makes: CatalogMake[];
  disabled: boolean;
  locale: string;
  onChange: (row: { make: string; model: string }) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const models = useApi<{ models: CatalogModel[] }>(
    ["vehicle-models", row.make],
    `/catalog/vehicle-makes/${encodeURIComponent(row.make)}/models`,
    Boolean(row.make),
  );
  const label = (m: CatalogMake) =>
    locale === "ru" && m.name_cyrillic && m.name_cyrillic !== m.name ? `${m.name} (${m.name_cyrillic})` : m.name;
  return (
    <div className="row" style={{ marginBottom: 8 }}>
      <select value={row.make} disabled={disabled} onChange={(e) => onChange({ make: e.target.value, model: "" })}>
        <option value="">{t("vehicles.make")}</option>
        {makes.map((m) => (
          <option key={m.code} value={m.code}>
            {label(m)}
          </option>
        ))}
      </select>
      <select
        value={row.model}
        disabled={disabled || !row.make}
        onChange={(e) => onChange({ ...row, model: e.target.value })}
      >
        <option value="">{t("warehouse.all_models")}</option>
        {(models.data?.models ?? []).map((m) => (
          <option key={m.code} value={m.code}>
            {m.name}
          </option>
        ))}
      </select>
      {!disabled ? (
        <button type="button" className="secondary" onClick={onRemove}>
          {t("rights.remove")}
        </button>
      ) : null}
    </div>
  );
}

/** Packagings of a material: content in the base unit, to hundredths (§ 11.1.15). */
function Packagings({ item }: { item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh(item);
  const [quantity, setQuantity] = useState("");
  const [name, setName] = useState("");
  const base = `/c/${company.public_id}/warehouse/items/${item.id}/packagings`;
  const add = useCommand<{ quantity: string; name: string }, unknown>(base);
  const archive = useCommand<{ id: string }, unknown>((body) => `${base}/${body.id}/archive`);
  const unit = t(`warehouse.unit_short_${item.unit}`);
  return (
    <Card title={t("warehouse.packagings")}>
      <p className="muted small">{t("warehouse.packagings_hint")}</p>
      <ul>
        {item.packagings
          .filter((p) => p.state === "active")
          .map((p) => (
            <li key={p.id}>
              {p.name ? `${p.name} — ` : ""}
              {Number(p.quantity)} {unit}{" "}
              {can("warehouse.catalog.manage") ? (
                <button
                  type="button"
                  className="secondary"
                  onClick={async () => {
                    if ((await archive.run({ id: p.id })) !== null) await refresh();
                  }}
                >
                  {t("warehouse.archive")}
                </button>
              ) : null}
            </li>
          ))}
      </ul>
      {can("warehouse.catalog.manage") ? (
        <div className="row">
          <Field label={t("warehouse.packaging_quantity", { unit })}>
            <input inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value.replace(",", "."))} />
          </Field>
          <Field label={t("warehouse.packaging_name")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="field" style={{ alignSelf: "end" }}>
            <button
              type="button"
              disabled={!quantity || add.pending}
              onClick={async () => {
                if ((await add.run({ quantity, name })) !== null) {
                  setQuantity("");
                  setName("");
                  await refresh();
                }
              }}
            >
              {t("warehouse.add_packaging")}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorMessage error={add.error ?? archive.error} />
    </Card>
  );
}

/** Barcodes of the company: a scan gives the item and the packaging (§ 11.1.16). */
function Barcodes({ item }: { item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh(item);
  const [code, setCode] = useState("");
  const [packaging, setPackaging] = useState("");
  const base = `/c/${company.public_id}/warehouse/items/${item.id}/barcodes`;
  const add = useCommand<{ code: string; packaging_id?: string }, unknown>(base);
  const remove = useCommand<{ id: string }, unknown>((body) => `${base}/${body.id}`, "DELETE");
  const unit = t(`warehouse.unit_short_${item.unit}`);
  const packName = (id: string | null) => {
    const p = item.packagings.find((x) => x.id === id);
    return p ? `${p.name ? `${p.name} — ` : ""}${Number(p.quantity)} ${unit}` : "";
  };
  return (
    <Card title={t("warehouse.barcodes")}>
      <ul>
        {item.barcodes.map((b) => (
          <li key={b.id}>
            <span className="code">{b.code}</span> {b.packaging_id ? <span className="muted">{packName(b.packaging_id)}</span> : null}{" "}
            {can("warehouse.catalog.manage") ? (
              <button
                type="button"
                className="secondary"
                onClick={async () => {
                  if ((await remove.run({ id: b.id })) !== null) await refresh();
                }}
              >
                {t("rights.remove")}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      {can("warehouse.catalog.manage") ? (
        <div className="row">
          <Field label={t("warehouse.barcode")}>
            <input value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
          {item.accounting === "batch" ? (
            <Field label={t("warehouse.packaging")}>
              <select value={packaging} onChange={(e) => setPackaging(e.target.value)}>
                <option value="">—</option>
                {item.packagings
                  .filter((p) => p.state === "active")
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {packName(p.id)}
                    </option>
                  ))}
              </select>
            </Field>
          ) : null}
          <div className="field" style={{ alignSelf: "end" }}>
            <button
              type="button"
              disabled={!code.trim() || add.pending}
              onClick={async () => {
                const body = packaging ? { code, packaging_id: packaging } : { code };
                if ((await add.run(body)) !== null) {
                  setCode("");
                  setPackaging("");
                  await refresh();
                }
              }}
            >
              {t("warehouse.add_barcode")}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorMessage error={add.error ?? remove.error} />
    </Card>
  );
}

/** Archive, restore, or delete while unused (§ 11.1.11). */
function ItemState({ item }: { item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const refresh = useRefresh(item);
  const base = `/c/${company.public_id}/warehouse/items/${item.id}`;
  const toggle = useCommand<{ expected_version: number }, unknown>(
    `${base}/${item.state === "archived" ? "restore" : "archive"}`,
  );
  const remove = useCommand<Record<string, never>, unknown>(base, "DELETE");
  const [confirm, setConfirm] = useState(false);
  if (!can("warehouse.catalog.manage")) return null;
  return (
    <Card>
      <div className="actions" style={{ marginTop: 0 }}>
        <button
          type="button"
          className="secondary"
          onClick={async () => {
            if ((await toggle.run({ expected_version: item.version })) !== null) await refresh();
          }}
        >
          {item.state === "archived" ? t("warehouse.restore") : t("warehouse.archive")}
        </button>
        {confirm ? (
          <>
            <button
              type="button"
              className="danger"
              onClick={async () => {
                if ((await remove.run({})) !== null) {
                  await refresh();
                  navigate(companyPath(company, "/warehouse"));
                }
              }}
            >
              {t("locations.delete_confirm")}
            </button>
            <button type="button" className="secondary" onClick={() => setConfirm(false)}>
              {t("common.cancel")}
            </button>
          </>
        ) : (
          <button type="button" className="secondary" onClick={() => setConfirm(true)}>
            {t("locations.delete")}
          </button>
        )}
      </div>
      <p className="muted small">{t("warehouse.delete_hint")}</p>
      <ErrorMessage error={toggle.error ?? remove.error} />
    </Card>
  );
}
