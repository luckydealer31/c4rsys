import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { ApiError, api, uploadFile } from "../../api/client";
import { useApi } from "../../api/queries";
import type { CatalogMake, CatalogModel, VehicleOptions, WarehouseItem, WarehouseOptions } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Icon } from "../../components/icons";
import { Avatar } from "../../components/images";
import { Card, ErrorMessage, Field, Info, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { countries } from "../settings/CompanyProfilePage";
import { PartCategoryField } from "./Categories";
import { positionOf } from "./namePosition";
import { ItemStockCard, SupplierArticles } from "./Stock";
import { TagsLine } from "../tags/Tags";

const OTHER = "__other";

type Applies = { type: string; make: string; model: string; make_name: string; model_name: string };
type Offer = Omit<Applies, "model"> & { model: string | null };
const EMPTY: Applies = { type: "", make: "", model: "", make_name: "", model_name: "" };

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
const TEXTS = [
  "article",
  "category",
  "model_characteristic",
  "country",
  "country_other",
  "comment",
  "external_id",
] as const;

/** A barcode of the card: saved with the card, by «Save» (customer's remark of 22.11.2026). */
type Code = { code: string; packaging_id: string | null };
const codeKey = (c: Code) => `${c.code}|${c.packaging_id ?? ""}`;

type Form = {
  name: string;
  part_category: string;
  part_subcategory: string;
  unit: string;
  manufacturer: string;
  manufacturer_other: string;
  photo: string | null;
  applicability: Applies[];
  extra_attributes: Record<string, string[]>;
  barcodes: Code[];
} & Record<PairKey | ChoiceKey | (typeof TEXTS)[number], string>;

function formOf(item?: WarehouseItem): Form {
  return {
    name: item?.name ?? "",
    part_category: item?.part_category ?? "",
    part_subcategory: item?.part_subcategory ?? "",
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
    // A country typed by hand opens the form on «other».
    country: item?.country_other ? OTHER : (item?.country ?? ""),
    country_other: item?.country_other ?? "",
    comment: item?.comment ?? "",
    external_id: item?.external_id ?? "",
    photo: item?.photo ?? null,
    applicability: (item?.applicability ?? []).map((a) => ({ ...a, model: a.model ?? "" })),
    extra_attributes: item?.extra_attributes ?? {},
    barcodes: (item?.barcodes ?? []).map((b) => ({ code: b.code, packaging_id: b.packaging_id })),
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
  // The form is made anew after a save, so what was saved is remembered here.
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const item = useApi<{ item: WarehouseItem }>(
    ["warehouse-item", company.public_id, id],
    `/c/${company.public_id}/warehouse/items/${id}`,
  );
  if (!item.data) {
    return (
      <div className="page full">
        <Loading />
      </div>
    );
  }
  return (
    <>
      <ItemForm
        key={item.data.item.version}
        item={item.data.item}
        saved={savedVersion === item.data.item.version}
        onSaved={setSavedVersion}
      />
      <div className="page full" style={{ paddingTop: 0 }}>
        <ItemStockCard item={item.data.item} />
        {item.data.item.accounting === "batch" ? <Packagings item={item.data.item} /> : null}
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

function ItemForm({
  item,
  saved = false,
  onSaved,
}: {
  item?: WarehouseItem;
  saved?: boolean;
  onSaved?: (version: number) => void;
}) {
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
  const vehicles = useApi<VehicleOptions>(["vehicle-options"], "/catalog/vehicle-options");
  const [form, setForm] = useState<Form>(formOf(item));
  // Seen when there is something to save (customer's remark of 21.11.2026): «Save» of a card
  // works only after a change, and «Saved» stands beside it until the next one.
  const [first] = useState(() => JSON.stringify(formOf(item)));
  const changed = JSON.stringify(form) !== first;
  // The way back leads where the card was opened from; opened by itself — to the catalog.
  const from = (useLocation().state as { from?: string } | null)?.from === "stock" ? "stock" : "items";
  const [draft, setDraft] = useState<Applies | null>(null);
  const save = useCommand<Record<string, unknown>, { item: WarehouseItem }>(
    item ? `/c/${company.public_id}/warehouse/items/${item.id}` : `/c/${company.public_id}/warehouse/items`,
    item ? "PATCH" : "POST",
  );
  const codes = `/c/${company.public_id}/warehouse/items/${item?.id ?? ""}/barcodes`;
  const addCode = useCommand<{ code: string; packaging_id?: string }, unknown>(codes);
  const removeCode = useCommand<{ id: string }, unknown>((body) => `${codes}/${body.id}`, "DELETE");
  const editable = can("warehouse.catalog.manage");
  const set = (changes: Partial<Form>) => setForm((prev) => ({ ...prev, ...changes }));
  // Makes and models named in the name are offered as compatibility (customer's decision of
  // 20.11.2026); nothing is added until the offer is pressed.
  const [offers, setOffers] = useState<Applies[]>([]);
  const firstName = useRef(form.name);
  const byHand = useRef(new Set<PairKey>(PAIRS.map(([key]) => key).filter((key) => Boolean(item?.[key]))));
  useEffect(() => {
    if (!editable || form.name === firstName.current) return;
    // The position named in the name presses its buttons (customer's decision of 20.11.2026);
    // a button pressed by hand, or saved earlier, is not touched any more.
    const read = positionOf(form.name);
    const auto = PAIRS.map(([key]) => key).filter((key) => !byHand.current.has(key));
    set(Object.fromEntries(auto.map((key) => [key, read[key]])));
    const timer = window.setTimeout(async () => {
      const found = form.name.trim()
        ? (await api<{ matches: Offer[] }>(`/catalog/vehicle-match?text=${encodeURIComponent(form.name)}`)).matches
        : [];
      setOffers(found.map((m) => ({ ...m, model: m.model ?? "" })));
    }, 350);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reacts to the name only
  }, [form.name]);
  const fresh = offers.filter((o) => !form.applicability.some((a) => a.make === o.make && a.model === o.model));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = {
      name: form.name,
      manufacturer: form.manufacturer && form.manufacturer !== OTHER ? form.manufacturer : "",
      manufacturer_other: form.manufacturer === OTHER ? form.manufacturer_other : "",
      photo: form.photo ?? "",
      part_category: form.part_category || null,
      part_subcategory: form.part_subcategory || null,
      applicability: form.applicability
        .filter((a) => a.make)
        .map((a) => (a.model ? { make: a.make, model: a.model } : { make: a.make })),
    };
    for (const [key] of PAIRS) body[key] = form[key];
    for (const [key] of CHOICES) body[key] = form[key];
    body.extra_attributes = form.extra_attributes;
    for (const key of TEXTS) body[key] = form[key];
    body.country = form.country === OTHER ? "" : form.country;
    body.country_other = form.country === OTHER ? form.country_other : "";
    if (item) body.expected_version = item.version;
    else body.unit = form.unit;
    if (item) {
      // The barcodes go first: they do not change the version of the card, and a code that is
      // refused leaves the card as it is written, to be corrected and saved again.
      const kept = new Set(form.barcodes.map(codeKey));
      const had = new Set(item.barcodes.map(codeKey));
      for (const b of item.barcodes.filter((x) => !kept.has(codeKey(x)))) {
        if ((await removeCode.run({ id: b.id })) === null) return void (await refresh());
      }
      for (const b of form.barcodes.filter((x) => !had.has(codeKey(x)))) {
        const added = await addCode.run(b.packaging_id ? { code: b.code, packaging_id: b.packaging_id } : { code: b.code });
        if (added === null) return void (await refresh());
      }
    }
    const result = await save.run(body);
    if (result) {
      await refresh();
      if (!item) navigate(companyPath(company, `/warehouse/items/${result.item.id}`));
      else onSaved?.(result.item.version);
    }
  }

  const text = (key: (typeof TEXTS)[number]) => (
    <Field label={t(`warehouse.${key}`)}>
      <input
        value={form[key]}
        disabled={!editable}
        onChange={(e) => set({ [key]: e.target.value })}
      />
    </Field>
  );

  return (
    <div className="page full">
      <p>
        <Link to={companyPath(company, `/warehouse?tab=${from}`)}>← {t(`warehouse.tab_${from}`)}</Link>
      </p>
      {/* The card of an item (customer's decision of 21.11.2026): the whole width, the tags beside
          the name, one block without headings — the fields at the left, compatibility and
          applicability at the right, the photo and the comment below. */}
      <div className="card-head">
        <h1>{item ? item.name : t("warehouse.create_item")}</h1>
        {item ? <TagsLine kind="item" id={item.id} /> : null}
      </div>
      <form className="stack" onSubmit={submit}>
        <section className="card">
          <div className="item-columns">
            <div className="stack">
              <div className="row">
            <Field label={t("warehouse.unit")} info={item ? undefined : t("warehouse.unit_hint")}>
              <select value={form.unit} disabled={Boolean(item)} onChange={(e) => set({ unit: e.target.value })}>
                {(options.data?.units ?? []).map((u) => (
                  <option key={u.code} value={u.code}>
                    {t(`warehouse.unit_${u.code}`)}
                  </option>
                ))}
              </select>
            </Field>
                {text("article")}
              </div>
              <Field label={t("warehouse.name")}>
                <input value={form.name} disabled={!editable} onChange={(e) => set({ name: e.target.value })} />
              </Field>
              {text("model_characteristic")}
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
            {/* Where it is made (customer's decision of 20.11.2026): one brand has factories in
                several countries. */}
            <Field label={t("warehouse.country")}>
              <select value={form.country} disabled={!editable} onChange={(e) => set({ country: e.target.value })}>
                <option value="">—</option>
                {countries(i18n.language).map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.name}
                  </option>
                ))}
                <option value={OTHER}>{t("warehouse.country_other_option")}</option>
              </select>
            </Field>
            {form.country === OTHER ? text("country_other") : null}
              </div>
          <PartCategoryField
            name={form.name}
            category={form.part_category}
            subcategory={form.part_subcategory}
            editable={editable}
            autoFromStart={!item || !item.part_category}
            onChange={(category, subcategory) => set({ part_category: category, part_subcategory: subcategory })}
          />
            </div>
            <div className="stack">
              <div className="field">
                <span>{t("warehouse.applicability")}</span>
            {/* Compatibility (customer's decision of 20.11.2026): a chosen row is text with a cross;
                «Add» opens a row of type, make and model that OK adds and the cross drops. */}
            {form.applicability.map((row, index) => (
              <div key={index} className="applies-row">
                <span>
                  {[t(`vehicles.type_${row.type}`), row.make_name, row.model_name || t("warehouse.all_models")].join(
                    " · ",
                  )}
                </span>
                {editable ? (
                  <button
                    type="button"
                    className="link"
                    aria-label={t("rights.remove")}
                    onClick={() => set({ applicability: form.applicability.filter((_, i) => i !== index) })}
                  >
                    ×
                  </button>
                ) : null}
              </div>
            ))}
            {editable && fresh.length ? (
              <p className="small">
                {t("warehouse.applicability_offers")}{" "}
                {fresh.map((o) => (
                  <button
                    key={`${o.make}/${o.model}`}
                    type="button"
                    className="link"
                    style={{ marginRight: 10 }}
                    onClick={() => set({ applicability: [...form.applicability, o] })}
                  >
                    {[o.make_name, o.model_name || t("warehouse.all_models")].join(" ")}
                  </button>
                ))}
              </p>
            ) : null}
            {draft ? (
              <ApplicabilityRow
                row={draft}
                types={vehicles.data?.vehicle_types ?? []}
                locale={i18n.language}
                onChange={setDraft}
                onOk={() => {
                  const same = form.applicability.some((a) => a.make === draft.make && a.model === draft.model);
                  if (!same) set({ applicability: [...form.applicability, draft] });
                  setDraft(null);
                }}
                onCancel={() => setDraft(null)}
              />
            ) : editable ? (
              <button type="button" className="secondary" onClick={() => setDraft(EMPTY)}>
                {t("warehouse.add_applicability")}
              </button>
            ) : null}
              </div>
              <div className="field">
                <span>
                  {t("warehouse.section_parts")} <Info text={t("warehouse.toggles_hint")} />
                </span>
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
                    onClick={() => {
                      byHand.current.add(key);
                      set({ [key]: togglePair(form[key], value, value === a ? b : a) });
                    }}
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
            {(options.data?.extra_attributes ?? [])
              .filter((a) => a.state === "active" || (form.extra_attributes[a.code] ?? []).length)
              .map((a) => {
                const chosen = form.extra_attributes[a.code] ?? [];
                const code = i18n.language.slice(0, 2);
                const lang: "ru" | "en" | "th" = code === "ru" || code === "th" ? code : "en";
                return (
                  <div key={a.code} className="toggle-row">
                    <span className="muted small">{a.name[lang]}</span>
                    {a.values.map((v) => (
                      <ToggleButton
                        key={v.code}
                        pressed={chosen.includes(v.code)}
                        disabled={!editable || a.state !== "active"}
                        label={v.name[lang]}
                        onClick={() => {
                          const next = chosen.includes(v.code)
                            ? chosen.filter((c) => c !== v.code)
                            : a.multiple
                              ? [...chosen, v.code]
                              : [v.code];
                          set({ extra_attributes: { ...form.extra_attributes, [a.code]: next } });
                        }}
                      />
                    ))}
                  </div>
                );
              })}
          </div>
              </div>
            </div>
          </div>
          <div className="item-columns item-more">
            {/* The barcodes and the comment are one line, the photo is under them (customer's
                remark of 21.11.2026); a new item has no barcodes yet. */}
            {item ? <Barcodes item={item} value={form.barcodes} onChange={(barcodes) => set({ barcodes })} /> : <div />}
            <Field label={t("warehouse.comment")}>
              <GrowingText value={form.comment} disabled={!editable} onChange={(comment) => set({ comment })} />
            </Field>
            <ItemPhoto photo={form.photo} name={form.name} editable={editable} onChange={(photo) => set({ photo })} />
          </div>
        </section>
        <ErrorMessage error={addCode.error ?? removeCode.error ?? save.error} />
        {editable ? (
          <div className="actions">
            <button
              type="submit"
              disabled={
                !form.name.trim() || save.pending || addCode.pending || removeCode.pending || (Boolean(item) && !changed)
              }
            >
              {item ? t("common.save") : t("warehouse.create_item")}
            </button>
            {saved && !changed ? (
              <span className="saved-mark" role="status">
                <Icon name="check" size={16} /> {t("common.saved")}
              </span>
            ) : null}
          </div>
        ) : null}
      </form>
    </div>
  );
}

/** A text of several lines that takes one line while it is empty and grows with what is
 * written (customer's remark of 21.11.2026). */
function GrowingText({
  value,
  disabled,
  onChange,
}: {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    // The borders are outside the height of the content.
    el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
  }, [value]);
  return (
    <textarea
      ref={area}
      className="growing"
      rows={1}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/** The photo of an item (customer's decisions of 21.11.2026): a file only, there is no link —
 * what is in the catalog is at hand and can be photographed. Without a photo there is one
 * button; with one it is shown alone with a pencil that opens «Replace» and «Remove». */
function ItemPhoto({
  photo,
  name,
  editable,
  onChange,
}: {
  photo: string | null;
  name: string;
  editable: boolean;
  onChange: (photo: string | null) => void;
}) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function choose(file: File | undefined) {
    if (!file) return;
    setPending(true);
    setError(null);
    try {
      onChange((await uploadFile("item_photo", file)).id);
      setOpen(false);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, null));
    } finally {
      setPending(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="field item-photo">
      <span>
        {t("warehouse.photo")}
        {editable && (open || !photo) ? <Info text={t("images.hint")} /> : null}
      </span>
      {photo ? (
        <div className="item-photo-shown">
          <Avatar file={photo} name={name} size={96} square />
          {editable && !open ? (
            <button
              type="button"
              className="icon-button"
              aria-label={t("common.edit")}
              title={t("common.edit")}
              onClick={() => setOpen(true)}
            >
              <Icon name="edit" size={16} />
            </button>
          ) : null}
        </div>
      ) : !editable ? (
        <span className="muted">—</span>
      ) : null}
      {editable && (open || !photo) ? (
        <div className="item-photo-edit">
          <input
            ref={input}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            hidden
            onChange={(e) => void choose(e.target.files?.[0])}
          />
          <button type="button" className="secondary" disabled={pending} onClick={() => input.current?.click()}>
            {t(photo ? "images.replace" : "images.choose")}
          </button>
          {photo ? (
            <button
              type="button"
              className="secondary"
              onClick={() => {
                onChange(null);
                setOpen(false);
              }}
            >
              {t("images.remove")}
            </button>
          ) : null}
        </div>
      ) : null}
      <ErrorMessage error={error} />
    </div>
  );
}

function ApplicabilityRow({
  row,
  types,
  locale,
  onChange,
  onOk,
  onCancel,
}: {
  row: Applies;
  types: string[];
  locale: string;
  onChange: (row: Applies) => void;
  onOk: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  // The type of the vehicle comes first (customer's decision of 19.11.2026): the makes shown
  // are the ones of the catalog for that type.
  const makes = useApi<{ makes: CatalogMake[] }>(
    ["vehicle-makes", row.type],
    `/catalog/vehicle-makes?type=${row.type}`,
    Boolean(row.type),
  );
  const models = useApi<{ models: CatalogModel[] }>(
    ["vehicle-models", row.make],
    `/catalog/vehicle-makes/${encodeURIComponent(row.make)}/models`,
    Boolean(row.make),
  );
  const label = (m: CatalogMake) =>
    locale === "ru" && m.name_cyrillic && m.name_cyrillic !== m.name ? `${m.name} (${m.name_cyrillic})` : m.name;
  const nameOf = (list: { code: string; name: string }[] | undefined, code: string) =>
    list?.find((x) => x.code === code)?.name ?? "";
  return (
    <div className="row applies-draft">
      <select
        value={row.type}
        aria-label={t("warehouse.vehicle_type")}
        onChange={(e) => onChange({ ...EMPTY, type: e.target.value })}
      >
        <option value="">{t("warehouse.vehicle_type")}</option>
        {types.map((code) => (
          <option key={code} value={code}>
            {t(`vehicles.type_${code}`)}
          </option>
        ))}
      </select>
      <select
        value={row.make}
        aria-label={t("vehicles.make")}
        disabled={!row.type}
        onChange={(e) =>
          onChange({
            ...EMPTY,
            type: row.type,
            make: e.target.value,
            make_name: nameOf(makes.data?.makes, e.target.value),
          })
        }
      >
        <option value="">{t("vehicles.make")}</option>
        {(makes.data?.makes ?? []).map((m) => (
          <option key={m.code} value={m.code}>
            {label(m)}
          </option>
        ))}
      </select>
      <select
        value={row.model}
        aria-label={t("vehicles.model")}
        disabled={!row.make}
        onChange={(e) =>
          onChange({ ...row, model: e.target.value, model_name: nameOf(models.data?.models, e.target.value) })
        }
      >
        <option value="">{t("warehouse.all_models")}</option>
        {(models.data?.models ?? []).map((m) => (
          <option key={m.code} value={m.code}>
            {m.name}
          </option>
        ))}
      </select>
      <button type="button" disabled={!row.make} onClick={onOk}>
        {t("calendar.ok")}
      </button>
      <button type="button" className="link" aria-label={t("common.cancel")} onClick={onCancel}>
        ×
      </button>
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

/** Barcodes of the company: a scan gives the item and the packaging (§ 11.1.16). They stand in
 * the card itself, as marks with a cross and a short field (customer's remark of 21.11.2026), and
 * are saved with the card: the plus and the cross only change what «Save» will write (customer's
 * remark of 22.11.2026). */
function Barcodes({ item, value, onChange }: { item: WarehouseItem; value: Code[]; onChange: (value: Code[]) => void }) {
  const { t } = useTranslation();
  const can = useCan();
  const [code, setCode] = useState("");
  const [packaging, setPackaging] = useState("");
  const unit = t(`warehouse.unit_short_${item.unit}`);
  const packName = (id: string | null) => {
    const p = item.packagings.find((x) => x.id === id);
    return p ? `${p.name ? `${p.name} — ` : ""}${Number(p.quantity)} ${unit}` : "";
  };
  const editable = can("warehouse.catalog.manage");
  const written = code.trim();
  function put() {
    if (!written) return;
    if (!value.some((b) => b.code === written)) onChange([...value, { code: written, packaging_id: packaging || null }]);
    setCode("");
    setPackaging("");
  }
  if (!editable && !value.length) return null;
  return (
    <div className="field">
      <span>{t("warehouse.barcodes")}</span>
      <div className="barcode-line">
        {value.map((b) => (
          <span key={b.code} className="code-chip">
            <span className="code">{b.code}</span>
            {b.packaging_id ? <span className="muted small">{packName(b.packaging_id)}</span> : null}
            {editable ? (
              <button
                type="button"
                className="link"
                aria-label={t("rights.remove")}
                title={t("rights.remove")}
                onClick={() => onChange(value.filter((x) => x.code !== b.code))}
              >
                ×
              </button>
            ) : null}
          </span>
        ))}
        {editable ? (
          <>
            <input
              value={code}
              aria-label={t("warehouse.barcode")}
              placeholder={t("warehouse.barcode")}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => {
                // Enter puts the barcode into the card instead of saving the whole card.
                if (e.key === "Enter") {
                  e.preventDefault();
                  put();
                }
              }}
            />
            {item.accounting === "batch" && item.packagings.some((p) => p.state === "active") ? (
              <select value={packaging} aria-label={t("warehouse.packaging")} onChange={(e) => setPackaging(e.target.value)}>
                <option value="">{t("warehouse.packaging")}: —</option>
                {item.packagings
                  .filter((p) => p.state === "active")
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {packName(p.id)}
                    </option>
                  ))}
              </select>
            ) : null}
            <button
              type="button"
              className="secondary"
              aria-label={t("warehouse.add_barcode")}
              title={t("warehouse.add_barcode")}
              disabled={!written}
              onClick={put}
            >
              <Icon name="plus" size={14} />
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}

/** One button of deletion (customer's remark of 21.11.2026): an item nothing has been done with
 * is deleted; one that is used cannot be (§ 11.1.11), so the answer of the server turns the
 * question into a move to the archive. An archived item is brought back by its own button. */
function ItemState({ item }: { item: WarehouseItem }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const refresh = useRefresh(item);
  const base = `/c/${company.public_id}/warehouse/items/${item.id}`;
  const archived = item.state === "archived";
  const toggle = useCommand<{ expected_version: number }, unknown>(`${base}/${archived ? "restore" : "archive"}`);
  const remove = useCommand<Record<string, never>, unknown>(base, "DELETE");
  const [confirm, setConfirm] = useState(false);
  if (!can("warehouse.catalog.manage")) return null;
  // Used already: it is not deleted, it goes to the archive.
  const used = remove.error?.code === "ITEM_IN_USE";
  const move = async () => {
    if ((await toggle.run({ expected_version: item.version })) !== null) {
      setConfirm(false);
      await refresh();
    }
  };
  return (
    <div className="item-state">
      {archived ? (
        <button type="button" className="secondary" disabled={toggle.pending} onClick={() => void move()}>
          {t("warehouse.restore")}
        </button>
      ) : null}
      {!confirm ? (
        <button type="button" className="secondary" onClick={() => setConfirm(true)}>
          {t("locations.delete")}
        </button>
      ) : used && !archived ? (
        <>
          <span role="alert">{t("warehouse.delete_used")}</span>
          <button type="button" disabled={toggle.pending} onClick={() => void move()}>
            {t("warehouse.to_archive")}
          </button>
          <button type="button" className="secondary" onClick={() => setConfirm(false)}>
            {t("common.cancel")}
          </button>
        </>
      ) : (
        <>
          <span>{t("warehouse.delete_ask")}</span>
          <button
            type="button"
            className="danger"
            disabled={remove.pending}
            onClick={async () => {
              if ((await remove.run({})) !== null) {
                await refresh();
                navigate(companyPath(company, "/warehouse?tab=items"));
              }
            }}
          >
            {t("locations.delete_confirm")}
          </button>
          <button type="button" className="secondary" onClick={() => setConfirm(false)}>
            {t("common.cancel")}
          </button>
        </>
      )}
      <ErrorMessage error={toggle.error ?? (used && !archived ? null : remove.error)} />
    </div>
  );
}
