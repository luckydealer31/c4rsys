import { useQueryClient } from "@tanstack/react-query";
import { Fragment, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { uploadFile } from "../../api/client";
import { useApi } from "../../api/queries";
import type {
  Counterparty,
  ItemBrief,
  LotChoice,
  Purchase,
  Receipt,
  ReceiptLine,
  Storage,
  WarehouseItem,
} from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { CounterpartyForm } from "../clients/Counterparties";
import { parseTags, useMoney, useQuantity } from "./Stock";

/** Receipts of the warehouses the employee may see, drafts and posted (§ 11.2.7, § 11.6.2). */
export function ReceiptsList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const money = useMoney();
  const list = useApi<{ receipts: Receipt[] }>(
    ["warehouse-receipts", company.public_id],
    `/c/${company.public_id}/warehouse/receipts`,
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
        {list.isLoading ? <Loading /> : null}
        {list.data?.receipts.length === 0 ? <p className="muted">{t("receipts.none")}</p> : null}
        {list.data?.receipts.length ? (
          <table>
            <thead>
              <tr>
                <th>{t("receipts.number_short")}</th>
                <th>{t("receipts.received_at")}</th>
                <th>{t("receipts.warehouse")}</th>
                <th>{t("receipts.source")}</th>
                <th>{t("receipts.supplier")}</th>
                <th>{t("receipts.total")}</th>
                <th>{t("receipts.state")}</th>
              </tr>
            </thead>
            <tbody>
              {list.data.receipts.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link to={companyPath(company, `/warehouse/receipts/${r.id}`)}>№ {r.number}</Link>
                  </td>
                  <td>{new Date(r.received_at).toLocaleDateString()}</td>
                  <td>{r.context.location_name}</td>
                  <td>{t(`receipts.source_${r.source}`)}</td>
                  <td>{r.source === "initial" ? "—" : (r.supplier?.name ?? t("receipts.supplier_unknown"))}</td>
                  <td>
                    {money(r.total_cost, r.currency)}
                    {r.cost_incomplete ? <span className="badge warn" style={{ marginLeft: 6 }}>{t("receipts.cost_incomplete")}</span> : null}
                  </td>
                  <td>
                    <span className={`badge${r.state === "posted" ? " ok" : ""}`}>{t(`receipts.state_${r.state}`)}</span>
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

type LineForm = {
  item: ItemBrief;
  quantity: string;
  packaging_id: string;
  packs: string;
  expected_quantity: string;
  unit_cost: string;
  tags: string;
  serials: string;
  lot_id: string;
  lot_external_number: string;
  lot_production_date: string;
  lot_expiry_date: string;
  purchase_line_id: string;
};

type HeadForm = {
  context_id: string;
  source: "purchase" | "initial";
  supplier_id: string;
  purchase_id: string;
  document_id: string;
  received_at: string;
  comment: string;
};

function newLine(item: ItemBrief): LineForm {
  return {
    item: { id: item.id, name: item.name, unit: item.unit, accounting: item.accounting, article: item.article },
    quantity: "",
    packaging_id: "",
    packs: "",
    expected_quantity: "",
    unit_cost: "",
    tags: "",
    serials: "",
    lot_id: "",
    lot_external_number: "",
    lot_production_date: "",
    lot_expiry_date: "",
    purchase_line_id: "",
  };
}

/** A local date and time for an input of type datetime-local. */
function localInput(moment: Date): string {
  const shifted = new Date(moment.getTime() - moment.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

function bodyOf(head: HeadForm, lines: LineForm[]): Record<string, unknown> {
  return {
    context_id: head.context_id || null,
    source: head.source,
    supplier_id: head.source === "purchase" && !head.purchase_id ? head.supplier_id || null : null,
    purchase_id: head.source === "purchase" ? head.purchase_id || null : null,
    document_id: head.document_id || null,
    received_at: head.received_at ? new Date(head.received_at).toISOString() : null,
    comment: head.comment,
    lines: lines.map((line) => {
      const batch = line.item.accounting === "batch";
      return {
        item_id: line.item.id,
        quantity: batch && line.packaging_id ? null : line.quantity || null,
        packaging_id: batch ? line.packaging_id || null : null,
        packs: batch && line.packaging_id ? Number(line.packs) || null : null,
        expected_quantity: line.expected_quantity || null,
        unit_cost: line.unit_cost || null,
        tags: parseTags(line.tags),
        serial_numbers: batch ? [] : line.serials.split("\n").map((s) => s.trim()),
        lot_id: batch ? line.lot_id || null : null,
        lot_external_number: batch && !line.lot_id ? line.lot_external_number : "",
        lot_production_date: batch && !line.lot_id ? line.lot_production_date || null : null,
        lot_expiry_date: batch && !line.lot_id ? line.lot_expiry_date || null : null,
        purchase_line_id: line.purchase_line_id || null,
      };
    }),
  };
}

/** A new receipt (draft) or an existing one: drafts are edited, posted ones are shown. */
export function ReceiptPage() {
  const { id } = useParams();
  const company = useCompany();
  const receipt = useApi<{ receipt: Receipt }>(
    ["warehouse-receipt", company.public_id, id],
    `/c/${company.public_id}/warehouse/receipts/${id}`,
    Boolean(id),
  );
  if (id && !receipt.data) {
    return (
      <div className="page">
        <Loading />
      </div>
    );
  }
  const data = receipt.data?.receipt;
  if (data && data.state === "posted") return <PostedReceipt receipt={data} />;
  return <ReceiptEditor key={data?.version ?? "new"} receipt={data} />;
}

function ReceiptEditor({ receipt }: { receipt?: Receipt }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params] = useSearchParams();
  const fromPurchase = params.get("purchase") ?? "";
  const storages = useApi<{ storages: Storage[] }>(
    ["warehouse-storages", company.public_id],
    `/c/${company.public_id}/warehouse/storages`,
  );
  const suppliers = useApi<{ counterparties: Counterparty[] }>(
    ["counterparties", company.public_id, "", false],
    `/c/${company.public_id}/counterparties?q=&state=active`,
    can("counterparty.view"),
  );
  const purchase = useApi<{ purchase: Purchase }>(
    ["warehouse-purchase", company.public_id, fromPurchase],
    `/c/${company.public_id}/warehouse/purchases/${fromPurchase}`,
    Boolean(fromPurchase) && !receipt,
  );
  const usable = (storages.data?.storages ?? []).filter((s) => s.state === "enabled" && !s.location_deactivated);
  const [head, setHead] = useState<HeadForm>({
    context_id: receipt?.context.id ?? "",
    source: receipt?.source ?? "purchase",
    supplier_id: receipt?.supplier?.id ?? "",
    purchase_id: receipt?.purchase?.id ?? fromPurchase,
    document_id: receipt?.document_id ?? "",
    received_at: localInput(receipt ? new Date(receipt.received_at) : new Date()),
    comment: receipt?.comment ?? "",
  });
  const [lines, setLines] = useState<LineForm[]>(() => (receipt?.lines ?? []).map(lineFromReceipt));
  const [newSupplier, setNewSupplier] = useState(false);
  const [uploading, setUploading] = useState(false);

  // One warehouse is chosen by default when it is the only one.
  useEffect(() => {
    const only = usable.length === 1 ? usable[0] : undefined;
    if (!head.context_id && only) setHead((h) => ({ ...h, context_id: only.context_id }));
  }, [usable, head.context_id]);

  // A receipt by a purchase starts with what is still expected (§ 11.2.11).
  const prefill = purchase.data?.purchase;
  useEffect(() => {
    if (!prefill || receipt || lines.length) return;
    {
      const filled: LineForm[] = [];
      for (const line of prefill.lines) {
        if (Number(line.expected_quantity) <= 0) continue;
        filled.push({
          ...newLine(line.item),
          quantity: line.expected_quantity,
          expected_quantity: line.expected_quantity,
          unit_cost: line.unit_cost ?? "",
          purchase_line_id: line.id,
        });
      }
      setLines(filled);
    }
  }, [prefill, receipt, lines.length]);

  const base = `/c/${company.public_id}/warehouse/receipts`;
  // The draft saved from this screen: a new receipt becomes a draft at the first save.
  const [saved, setSaved] = useState<{ id: string; version: number } | null>(
    receipt ? { id: receipt.id, version: receipt.version } : null,
  );
  const create = useCommand<Record<string, unknown>, { receipt: Receipt }>(base);
  const update = useCommand<Record<string, unknown> & { id: string }, { receipt: Receipt }>(
    (body) => `${base}/${body.id}`,
    "PATCH",
  );
  const post = useCommand<{ id: string; expected_version: number }, { receipt: Receipt }>(
    (body) => `${base}/${body.id}/post`,
  );
  const remove = useCommand<{ id: string }, unknown>((body) => `${base}/${body.id}`, "DELETE");
  const error = create.error ?? update.error ?? post.error ?? remove.error;
  const pending = create.pending || update.pending || post.pending || remove.pending;

  async function refresh() {
    await client.invalidateQueries({ queryKey: ["warehouse-receipts", company.public_id] });
    await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
    await client.invalidateQueries({ queryKey: ["warehouse-purchases", company.public_id] });
    if (receipt) await client.invalidateQueries({ queryKey: ["warehouse-receipt", company.public_id, receipt.id] });
  }

  async function save(): Promise<Receipt | null> {
    const body = bodyOf(head, lines);
    const result = saved
      ? await update.run({ ...body, id: saved.id, expected_version: saved.version })
      : await create.run(body);
    if (!result) return null;
    setSaved({ id: result.receipt.id, version: result.receipt.version });
    await refresh();
    return result.receipt;
  }

  const open = (id: string) => navigate(companyPath(company, `/warehouse/receipts/${id}`), { replace: true });

  async function saveDraft() {
    const draft = await save();
    if (draft && !receipt) open(draft.id);
  }

  async function saveAndPost() {
    const draft = await save();
    if (!draft) return;
    // A refusal keeps this screen with the saved draft and the message.
    if (await post.run({ id: draft.id, expected_version: draft.version })) {
      await refresh();
      open(draft.id);
    }
  }

  const byPurchase = Boolean(head.purchase_id);
  const supplierName = prefill?.supplier?.name ?? receipt?.supplier?.name;

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/warehouse?tab=receipts")}>← {t("warehouse.tab_receipts")}</Link>
      </p>
      <h1>{receipt ? t("receipts.title_number", { number: receipt.number }) : t("receipts.new_title")}</h1>
      {receipt ? <p><span className="badge">{t("receipts.state_draft")}</span> {t("receipts.draft_hint")}</p> : null}
      <Card>
        <div className="row">
          <Field label={t("receipts.warehouse")}>
            <select value={head.context_id} onChange={(e) => setHead({ ...head, context_id: e.target.value })}>
              <option value="">—</option>
              {usable.map((s) => (
                <option key={s.context_id} value={s.context_id}>
                  {s.location_name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("receipts.source")}>
            <select
              value={head.source}
              disabled={byPurchase}
              onChange={(e) => setHead({ ...head, source: e.target.value as HeadForm["source"] })}
            >
              <option value="purchase">{t("receipts.source_purchase")}</option>
              <option value="initial">{t("receipts.source_initial")}</option>
            </select>
          </Field>
          <Field label={t("receipts.received_at")}>
            <input
              type="datetime-local"
              value={head.received_at}
              onChange={(e) => setHead({ ...head, received_at: e.target.value })}
            />
          </Field>
        </div>
        {usable.length === 0 && storages.data ? <p className="alert warn">{t("warehouse.no_storages")}</p> : null}
        {head.source === "purchase" ? (
          byPurchase ? (
            <p>
              {t("receipts.by_purchase", { number: prefill?.number ?? receipt?.purchase?.number ?? "" })} ·{" "}
              {t("receipts.supplier")}: {supplierName ?? t("receipts.supplier_unknown")}
            </p>
          ) : (
            <div className="row">
              <Field label={t("receipts.supplier")} hint={t("receipts.supplier_hint")}>
                <select value={head.supplier_id} onChange={(e) => setHead({ ...head, supplier_id: e.target.value })}>
                  <option value="">{t("receipts.supplier_unknown")}</option>
                  {(suppliers.data?.counterparties ?? []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </Field>
              {can("counterparty.create") ? (
                <button type="button" className="secondary" style={{ alignSelf: "end", flex: "0 0 auto" }} onClick={() => setNewSupplier(!newSupplier)}>
                  {t("receipts.new_supplier")}
                </button>
              ) : null}
            </div>
          )
        ) : (
          <p className="muted small">{t("receipts.initial_hint")}</p>
        )}
        <div className="row">
          <Field label={t("receipts.document")} hint={t("receipts.document_hint")}>
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp,application/pdf"
              disabled={uploading}
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                setUploading(true);
                try {
                  const uploaded = await uploadFile("warehouse_document", file);
                  setHead((h) => ({ ...h, document_id: uploaded.id }));
                } finally {
                  setUploading(false);
                }
              }}
            />
          </Field>
          {head.document_id ? (
            <p style={{ alignSelf: "end" }}>
              <a href={`/api/files/${head.document_id}`} target="_blank" rel="noreferrer">
                {t("receipts.document_open")}
              </a>{" "}
              <button type="button" className="link" onClick={() => setHead({ ...head, document_id: "" })}>
                {t("articles.remove")}
              </button>
            </p>
          ) : null}
          <Field label={t("warehouse.comment")}>
            <input value={head.comment} onChange={(e) => setHead({ ...head, comment: e.target.value })} />
          </Field>
        </div>
      </Card>
      {newSupplier ? (
        <CounterpartyForm
          onCreated={(card) => {
            setHead((h) => ({ ...h, supplier_id: card.id }));
            setNewSupplier(false);
            void client.invalidateQueries({ queryKey: ["counterparties", company.public_id] });
          }}
        />
      ) : null}
      <Card title={t("receipts.lines")}>
        {lines.map((line, index) => (
          <LineEditor
            key={index}
            line={line}
            contextId={head.context_id}
            onChange={(next) => setLines(lines.map((l, i) => (i === index ? next : l)))}
            onRemove={() => setLines(lines.filter((_, i) => i !== index))}
          />
        ))}
        {!byPurchase ? <ItemPicker onPick={(item) => setLines([...lines, newLine(item)])} /> : null}
        {lines.length === 0 ? <p className="muted small">{t("receipts.no_lines")}</p> : null}
      </Card>
      <ErrorMessage error={error} />
      <div className="actions">
        <button type="button" className="secondary" disabled={pending || !lines.length} onClick={() => void saveDraft()}>
          {t("receipts.save_draft")}
        </button>
        <button type="button" disabled={pending || !lines.length || !head.context_id} onClick={() => void saveAndPost()}>
          {t("receipts.post")}
        </button>
        {saved ? (
          <button
            type="button"
            className="danger"
            disabled={pending}
            onClick={async () => {
              if ((await remove.run({ id: saved.id })) !== null) {
                await refresh();
                navigate(companyPath(company, "/warehouse?tab=receipts"));
              }
            }}
          >
            {t("receipts.delete_draft")}
          </button>
        ) : null}
      </div>
      <p className="muted small">{t("receipts.post_hint")}</p>
    </div>
  );
}

function lineFromReceipt(line: ReceiptLine): LineForm {
  return {
    item: line.item,
    quantity: line.packaging_id ? "" : line.quantity,
    packaging_id: line.packaging_id ?? "",
    packs: line.packs ? String(line.packs) : "",
    expected_quantity: line.expected_quantity ?? "",
    unit_cost: line.unit_cost ?? "",
    tags: line.tags.join(", "),
    serials: line.serial_numbers.join("\n"),
    lot_id: line.lot?.id ?? "",
    lot_external_number: line.lot_external_number,
    lot_production_date: line.lot_production_date ?? "",
    lot_expiry_date: line.lot_expiry_date ?? "",
    purchase_line_id: line.purchase_line_id ?? "",
  };
}

/** Search of an item by name, article or barcode; a missing item is created in the catalog. */
export function ItemPicker({ onPick }: { onPick: (item: WarehouseItem) => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [query, setQuery] = useState("");
  const found = useApi<{ items: WarehouseItem[] }>(
    ["warehouse-items", company.public_id, query, false],
    `/c/${company.public_id}/warehouse/items?q=${encodeURIComponent(query)}&state=active`,
    query.trim().length >= 2,
  );
  return (
    <div className="line-card">
      <Field label={t("receipts.add_item")} hint={t("receipts.add_item_hint")}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("warehouse.search")} />
      </Field>
      {query.trim().length >= 2 ? (
        <ul>
          {(found.data?.items ?? []).slice(0, 10).map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className="link"
                onClick={() => {
                  onPick(item);
                  setQuery("");
                }}
              >
                {item.name}
              </button>{" "}
              <span className="small muted">
                {item.article ? `${item.article} · ` : ""}
                {t(`warehouse.unit_short_${item.unit}`)}
              </span>
            </li>
          ))}
          {found.data?.items.length === 0 ? <li className="muted">{t("warehouse.no_items")}</li> : null}
        </ul>
      ) : null}
      {can("warehouse.catalog.manage") ? (
        <p className="small">
          <Link to={companyPath(company, "/warehouse/items/new")} target="_blank">
            {t("receipts.create_item")}
          </Link>
        </p>
      ) : null}
    </div>
  );
}

function LineEditor({
  line,
  contextId,
  onChange,
  onRemove,
}: {
  line: LineForm;
  contextId: string;
  onChange: (line: LineForm) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const batch = line.item.accounting === "batch";
  const unit = t(`warehouse.unit_short_${line.item.unit}`);
  const item = useApi<{ item: WarehouseItem }>(
    ["warehouse-item", company.public_id, line.item.id],
    `/c/${company.public_id}/warehouse/items/${line.item.id}`,
    batch,
  );
  const lots = useApi<{ lots: LotChoice[] }>(
    ["warehouse-lots", company.public_id, line.item.id, contextId, line.lot_external_number],
    `/c/${company.public_id}/warehouse/items/${line.item.id}/lots?context_id=${contextId}&external_number=${encodeURIComponent(line.lot_external_number)}`,
    batch && Boolean(contextId),
  );
  const packagings = (item.data?.item.packagings ?? []).filter((p) => p.state === "active");
  const pack = packagings.find((p) => p.id === line.packaging_id);
  const set = (key: keyof LineForm) => (e: { target: { value: string } }) => onChange({ ...line, [key]: e.target.value });
  const match = (lots.data?.lots ?? []).find((l) => l.matches);
  const total = pack && line.packs ? Number(pack.quantity) * Number(line.packs) : Number(line.quantity || 0);

  return (
    <div className="line-card">
      <h3>
        {line.item.name} <span className="small muted">{unit}</span>
      </h3>
      <div className="row">
        {batch && packagings.length ? (
          <Field label={t("receipts.form")}>
            <select value={line.packaging_id} onChange={set("packaging_id")}>
              <option value="">{t("receipts.form_loose", { unit })}</option>
              {packagings.map((p) => (
                <option key={p.id} value={p.id}>
                  {t("receipts.form_pack", { size: `${Number(p.quantity)} ${unit}`, name: p.name })}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        {pack ? (
          <Field label={t("receipts.packs")} hint={t("receipts.packs_total", { total: `${total} ${unit}` })}>
            <input inputMode="numeric" value={line.packs} onChange={set("packs")} />
          </Field>
        ) : (
          <Field label={t("receipts.quantity_received", { unit })}>
            <input inputMode="decimal" value={line.quantity} onChange={set("quantity")} />
          </Field>
        )}
        <Field label={t("receipts.expected", { unit })} hint={t("receipts.expected_hint")}>
          <input inputMode="decimal" value={line.expected_quantity} onChange={set("expected_quantity")} />
        </Field>
        <Field label={t("receipts.unit_cost_per", { unit })} hint={t("receipts.unit_cost_hint")}>
          <input inputMode="decimal" value={line.unit_cost} onChange={set("unit_cost")} />
        </Field>
      </div>
      {line.expected_quantity && total !== Number(line.expected_quantity) ? (
        <p className="alert warn">
          {t("receipts.discrepancy", { value: `${total - Number(line.expected_quantity)} ${unit}` })}
        </p>
      ) : null}
      {batch ? (
        <div className="row">
          <Field label={t("receipts.lot")} hint={t("receipts.lot_hint")}>
            <select value={line.lot_id} onChange={set("lot_id")}>
              <option value="">{t("receipts.lot_new")}</option>
              {(lots.data?.lots ?? []).map((l) => (
                <option key={l.id} value={l.id}>
                  {l.public_id}
                  {l.external_number ? ` · ${l.external_number}` : ""}
                  {l.expiry_date ? ` · ${l.expiry_date}` : ""}
                </option>
              ))}
            </select>
          </Field>
          {!line.lot_id ? (
            <>
              <Field label={t("receipts.lot_external_number")}>
                <input value={line.lot_external_number} onChange={set("lot_external_number")} />
              </Field>
              <Field label={t("receipts.lot_production_date")}>
                <input type="date" value={line.lot_production_date} onChange={set("lot_production_date")} />
              </Field>
              <Field label={t("receipts.lot_expiry_date")}>
                <input type="date" value={line.lot_expiry_date} onChange={set("lot_expiry_date")} />
              </Field>
            </>
          ) : null}
        </div>
      ) : (
        <Field label={t("receipts.serial_numbers")} hint={t("receipts.serial_numbers_hint")}>
          <textarea rows={2} value={line.serials} onChange={set("serials")} />
        </Field>
      )}
      {batch && !line.lot_id && match ? (
        <p className="alert warn">
          {t("receipts.lot_match", { lot: match.public_id })}{" "}
          <button type="button" className="link" onClick={() => onChange({ ...line, lot_id: match.id, lot_external_number: "" })}>
            {t("receipts.lot_use")}
          </button>
        </p>
      ) : null}
      <div className="row">
        <Field label={t("receipts.tags")} hint={t("receipts.tags_hint")}>
          <input value={line.tags} onChange={set("tags")} />
        </Field>
        <button type="button" className="secondary" style={{ alignSelf: "end", flex: "0 0 auto" }} onClick={onRemove}>
          {t("receipts.remove_line")}
        </button>
      </div>
    </div>
  );
}

/** A posted receipt: what came, the created assets and lots, discrepancies, and a cost that was
 * not given may be entered later (§ 11.2.6). */
function PostedReceipt({ receipt }: { receipt: Receipt }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [correcting, setCorrecting] = useState<string | null>(null);
  const quantity = useQuantity();
  const money = useMoney();
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/warehouse?tab=receipts")}>← {t("warehouse.tab_receipts")}</Link>
      </p>
      <h1>{t("receipts.title_number", { number: receipt.number })}</h1>
      <Card>
        <p>
          <span className="badge ok">{t("receipts.state_posted")}</span>{" "}
          {receipt.posted_at ? new Date(receipt.posted_at).toLocaleString() : ""}
        </p>
        <p>
          {t("receipts.warehouse")}: {receipt.context.location_name} · {t("receipts.source")}:{" "}
          {t(`receipts.source_${receipt.source}`)}
          {receipt.source === "purchase"
            ? ` · ${t("receipts.supplier")}: ${receipt.supplier?.name ?? t("receipts.supplier_unknown")}`
            : ""}
          {receipt.purchase ? (
            <>
              {" · "}
              <Link to={companyPath(company, `/warehouse/purchases/${receipt.purchase.id}`)}>
                {t("purchases.title_number", { number: receipt.purchase.number })}
              </Link>
            </>
          ) : null}
        </p>
        <p>
          {t("receipts.received_at")}: {new Date(receipt.received_at).toLocaleString()}
          {receipt.comment ? ` · ${receipt.comment}` : ""}
          {receipt.document_id ? (
            <>
              {" · "}
              <a href={`/api/files/${receipt.document_id}`} target="_blank" rel="noreferrer">
                {t("receipts.document_open")}
              </a>
            </>
          ) : null}
        </p>
        <p>
          {t("receipts.total")}: <strong>{money(receipt.total_cost, receipt.currency)}</strong>{" "}
          {receipt.cost_incomplete ? <span className="badge warn">{t("receipts.cost_incomplete")}</span> : null}
        </p>
      </Card>
      <Card title={t("receipts.lines")}>
        <table>
          <thead>
            <tr>
              <th>{t("warehouse.name")}</th>
              <th>{t("receipts.quantity")}</th>
              <th>{t("receipts.expected_short")}</th>
              <th>{t("receipts.unit_cost")}</th>
              <th>{t("receipts.total")}</th>
              <th>{t("receipts.result")}</th>
            </tr>
          </thead>
          <tbody>
            {receipt.lines.map((line) => (
              <Fragment key={line.id}>
              <tr>
                <td>
                  <Link to={companyPath(company, `/warehouse/items/${line.item.id}`)}>{line.item.name}</Link>
                </td>
                <td>
                  {quantity(line.quantity, line.item.unit)}
                  {line.packs ? (
                    <div className="small muted">
                      {t("stock.packs", { packs: line.packs, size: quantity(line.packaging_quantity ?? 0, line.item.unit) })}
                    </div>
                  ) : null}
                </td>
                <td>
                  {line.expected_quantity !== null ? quantity(line.expected_quantity, line.item.unit) : "—"}
                  {line.discrepancy !== null && Number(line.discrepancy) !== 0 ? (
                    <div className="badge warn">{t("receipts.discrepancy", { value: quantity(line.discrepancy, line.item.unit) })}</div>
                  ) : null}
                </td>
                <td>
                  {line.unit_cost === null && can("warehouse.receive") ? (
                    <LateCost receipt={receipt} line={line} />
                  ) : (
                    money(line.unit_cost, receipt.currency)
                  )}
                </td>
                <td>{line.total_cost === null ? "—" : money(line.total_cost, receipt.currency)}</td>
                <td className="small">
                  {line.asset_refs.length
                    ? line.asset_refs
                        .map((a) => (a.state === "in_stock" ? a.public_id : `${a.public_id} (${t(`stock.state_${a.state}`)})`))
                        .join(", ")
                    : null}
                  {line.lot ? line.lot.public_id : null}
                  {line.serial_numbers.some(Boolean) ? (
                    <div className="muted">{line.serial_numbers.filter(Boolean).join(", ")}</div>
                  ) : null}
                  {line.corrections.map((c, i) => (
                    <div key={i} className="muted">
                      {t("receipts.correction_line", {
                        date: new Date(c.created_at).toLocaleDateString(),
                        before: c.quantity_before,
                        after: c.quantity_after,
                        reason: c.reason,
                      })}
                    </div>
                  ))}
                  {can("warehouse.correct") ? (
                    <button type="button" className="link" onClick={() => setCorrecting(correcting === line.id ? null : line.id)}>
                      {t("receipts.correct")}
                    </button>
                  ) : null}
                </td>
              </tr>
              {correcting === line.id ? (
                <tr>
                  <td colSpan={6}>
                    <CorrectLine receipt={receipt} line={line} onDone={() => setCorrecting(null)} />
                  </td>
                </tr>
              ) : null}
              </Fragment>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function LateCost({ receipt, line }: { receipt: Receipt; line: ReceiptLine }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const [value, setValue] = useState("");
  const enter = useCommand<{ unit_cost: string }, unknown>(
    `/c/${company.public_id}/warehouse/receipts/${receipt.id}/lines/${line.id}/cost`,
  );
  return (
    <div>
      <span className="badge warn">{t("receipts.cost_not_given")}</span>
      <div className="row" style={{ marginTop: 4 }}>
        <input inputMode="decimal" value={value} placeholder={receipt.currency} onChange={(e) => setValue(e.target.value)} />
        <button
          type="button"
          disabled={enter.pending || !value}
          onClick={async () => {
            if ((await enter.run({ unit_cost: value })) !== null) {
              await client.invalidateQueries({ queryKey: ["warehouse-receipt", company.public_id, receipt.id] });
              await client.invalidateQueries({ queryKey: ["warehouse-receipts", company.public_id] });
            }
          }}
        >
          {t("receipts.enter_cost")}
        </button>
      </div>
      <ErrorMessage error={enter.error} />
    </div>
  );
}

/** A correction of a posted line with a reason (§ 11.6.2): the stock never goes below zero; for
 * pieces, the units to remove are chosen. */
function CorrectLine({ receipt, line, onDone }: { receipt: Receipt; line: ReceiptLine; onDone: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const [quantity, setQuantity] = useState(line.quantity);
  const [cost, setCost] = useState(line.unit_cost ?? "");
  const [reason, setReason] = useState("");
  const [remove, setRemove] = useState<string[]>([]);
  const correct = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/warehouse/receipts/${receipt.id}/lines/${line.id}/correct`,
  );
  const fewer = line.item.accounting === "individual" && Number(quantity) < Number(line.quantity);
  return (
    <div className="line-card">
      <div className="row">
        <Field label={t("receipts.quantity")}>
          <input inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
        </Field>
        <Field label={t("receipts.unit_cost")}>
          <input inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} />
        </Field>
        <Field label={t("receipts.correction_reason")}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
      {fewer ? (
        <div className="stack">
          <p className="small">{t("receipts.correction_choose_units")}</p>
          {line.asset_refs.filter((a) => a.state === "in_stock").map((a) => (
            <label key={a.id}>
              <input
                type="checkbox"
                checked={remove.includes(a.id)}
                onChange={(e) => setRemove(e.target.checked ? [...remove, a.id] : remove.filter((x) => x !== a.id))}
              />{" "}
              {a.public_id}
            </label>
          ))}
        </div>
      ) : null}
      <ErrorMessage error={correct.error} />
      <div className="actions">
        <button
          type="button"
          disabled={correct.pending || !reason.trim()}
          onClick={async () => {
            const body: Record<string, unknown> = { reason, remove_asset_ids: remove };
            if (quantity !== line.quantity) body.quantity = quantity;
            if (cost !== (line.unit_cost ?? "")) body.unit_cost = cost || null;
            if ((await correct.run(body)) !== null) {
              await client.invalidateQueries({ queryKey: ["warehouse-receipt", company.public_id, receipt.id] });
              await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
              onDone();
            }
          }}
        >
          {t("receipts.correct_save")}
        </button>
        <button type="button" className="secondary" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
      <p className="muted small">{t("receipts.correction_hint")}</p>
    </div>
  );
}
