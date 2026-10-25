import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { uploadFile } from "../../api/client";
import { useApi } from "../../api/queries";
import type {
  ItemBrief,
  Sources,
  StockOperation,
  Storage,
  WarehouseItem,
  WriteOffReason,
} from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { ItemPicker } from "./Receipts";
import { useMoney, useQuantity } from "./Stock";

const NEW_KINDS = ["issue", "consume", "move", "write_off"] as const;
type NewKind = (typeof NEW_KINDS)[number];
const RIGHT: Record<NewKind, string> = {
  issue: "warehouse.issue",
  consume: "warehouse.issue",
  move: "warehouse.move",
  write_off: "warehouse.write_off",
};

/** A reason shows its own name, or the translated starter name (§ 11.3.4). */
export function useReasonName() {
  const { t } = useTranslation();
  return (reason: Pick<WriteOffReason, "code" | "name">) => reason.name || t(`reasons.${reason.code}`);
}

export function useReasons() {
  const company = useCompany();
  return useApi<{ reasons: WriteOffReason[] }>(
    ["warehouse-reasons", company.public_id],
    `/c/${company.public_id}/warehouse/reasons`,
  );
}

function useWarehouses() {
  const company = useCompany();
  const storages = useApi<{ storages: Storage[] }>(
    ["warehouse-storages", company.public_id],
    `/c/${company.public_id}/warehouse/storages`,
  );
  return (storages.data?.storages ?? []).filter((s) => s.state === "enabled" && !s.location_deactivated);
}

function operationTitle(t: (key: string, options?: Record<string, unknown>) => string, operation: Pick<StockOperation, "kind" | "number">) {
  return t("operations.title", { kind: t(`operations.kind_${operation.kind}`), number: operation.number });
}

/** Operations of the warehouses the employee may see; moves in transit wait for receipt. */
export function OperationsList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const reasonName = useReasonName();
  const [filter, setFilter] = useState<"" | "in_transit" | "discrepancy">("");
  const list = useApi<{ operations: StockOperation[] }>(
    ["warehouse-operations", company.public_id, filter],
    `/c/${company.public_id}/warehouse/operations${filter ? `?state=${filter}` : ""}`,
    can("warehouse.view"),
  );
  if (!can("warehouse.view")) return <p className="muted">{t("warehouse.no_access")}</p>;
  return (
    <>
      <div className="actions" style={{ marginTop: 0, marginBottom: 16 }}>
        {NEW_KINDS.filter((kind) => can(RIGHT[kind])).map((kind) => (
          <Link key={kind} className="button secondary" to={companyPath(company, `/warehouse/operations/new?kind=${kind}`)}>
            {t(`operations.new_${kind}`)}
          </Link>
        ))}
      </div>
      <Card>
        <Field label={t("operations.filter")}>
          <select value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)}>
            <option value="">{t("operations.filter_all")}</option>
            <option value="in_transit">{t("operations.state_in_transit")}</option>
            <option value="discrepancy">{t("operations.state_discrepancy")}</option>
          </select>
        </Field>
        {list.isLoading ? <Loading /> : null}
        {list.data?.operations.length === 0 ? <p className="muted">{t("operations.none")}</p> : null}
        {list.data?.operations.length ? (
          <table>
            <thead>
              <tr>
                <th>{t("receipts.number_short")}</th>
                <th>{t("purchases.created_at")}</th>
                <th>{t("operations.kind")}</th>
                <th>{t("receipts.warehouse")}</th>
                <th>{t("operations.details")}</th>
                <th>{t("receipts.state")}</th>
              </tr>
            </thead>
            <tbody>
              {list.data.operations.map((o) => (
                <tr key={o.id}>
                  <td>
                    <Link to={companyPath(company, `/warehouse/operations/${o.id}`)}>№ {o.number}</Link>
                  </td>
                  <td>{new Date(o.created_at).toLocaleDateString()}</td>
                  <td>{t(`operations.kind_${o.kind}`)}</td>
                  <td>
                    {o.context.location_name}
                    {o.target_context ? ` → ${o.target_context.location_name}` : ""}
                  </td>
                  <td className="small">
                    {o.vehicle?.name ?? (o.reason ? reasonName(o.reason) : "")}{" "}
                    <span className="muted">{o.lines.filter((l) => !l.output).map((l) => l.item.name).join(", ")}</span>
                  </td>
                  <td>
                    <span className={`badge${o.state === "done" || o.state === "received" ? " ok" : " warn"}`}>
                      {t(`operations.state_${o.state}`)}
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

type TakeForm = {
  item: ItemBrief;
  asset_ids: string[];
  lot_id: string;
  form: string; // "pack:<id>", "opened:<id>" or "loose"
  quantity: string;
  confirm_expired: boolean;
};

function takeBody(take: TakeForm): Record<string, unknown> {
  if (take.item.accounting === "individual") return { item_id: take.item.id, asset_ids: take.asset_ids };
  const [form, id] = take.form.split(":");
  return {
    item_id: take.item.id,
    lot_id: take.lot_id || null,
    packaging_id: form === "pack" ? id : null,
    opened_package_id: form === "opened" ? id : null,
    quantity: take.quantity || null,
    confirm_expired: take.confirm_expired,
  };
}

/** A new issue, consumption, move or write-off. */
export function NewOperationPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params] = useSearchParams();
  const kind = (NEW_KINDS as readonly string[]).includes(params.get("kind") ?? "") ? (params.get("kind") as NewKind) : "issue";
  const warehouses = useWarehouses();
  const reasons = useReasons();
  const reasonName = useReasonName();
  const vehicles = useApi<{ vehicles: { id: string; name: string }[] }>(
    ["warehouse-vehicles", company.public_id],
    `/c/${company.public_id}/warehouse/vehicles`,
    (kind === "issue" || kind === "consume") && can("warehouse.issue"),
  );
  const [contextId, setContextId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [vehicleId, setVehicleId] = useState("");
  const [reasonId, setReasonId] = useState("");
  const [comment, setComment] = useState("");
  const [oneStep, setOneStep] = useState(false);
  const [photos, setPhotos] = useState<string[]>([]);
  const [takes, setTakes] = useState<TakeForm[]>([]);
  const path = kind === "write_off" ? "write-off" : kind;
  const create = useCommand<Record<string, unknown>, { operation: StockOperation }>(
    `/c/${company.public_id}/warehouse/operations/${path}`,
  );
  const context = contextId || (warehouses.length === 1 ? (warehouses[0]?.context_id ?? "") : "");

  async function submit() {
    const body: Record<string, unknown> = { context_id: context, comment, lines: takes.map(takeBody) };
    if (kind === "issue" || kind === "consume") body.vehicle_id = vehicleId || null;
    if (kind === "move") Object.assign(body, { target_context_id: targetId || null, one_step: oneStep });
    if (kind === "write_off") Object.assign(body, { reason_id: reasonId || null, photos });
    const result = await create.run(body);
    if (result) {
      await client.invalidateQueries({ queryKey: ["warehouse-operations", company.public_id] });
      await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
      navigate(companyPath(company, `/warehouse/operations/${result.operation.id}`), { replace: true });
    }
  }

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/warehouse?tab=operations")}>← {t("warehouse.tab_operations")}</Link>
      </p>
      <h1>{t(`operations.new_${kind}`)}</h1>
      <p className="muted small">{t(`operations.hint_${kind}`)}</p>
      <Card>
        <div className="row">
          <Field label={kind === "move" ? t("operations.from") : t("receipts.warehouse")}>
            <select value={context} onChange={(e) => { setContextId(e.target.value); setTakes([]); }}>
              <option value="">—</option>
              {warehouses.map((w) => (
                <option key={w.context_id} value={w.context_id}>
                  {w.location_name}
                </option>
              ))}
            </select>
          </Field>
          {kind === "move" ? (
            <Field label={t("operations.to")}>
              <select value={targetId} onChange={(e) => setTargetId(e.target.value)}>
                <option value="">—</option>
                {warehouses.filter((w) => w.context_id !== context).map((w) => (
                  <option key={w.context_id} value={w.context_id}>
                    {w.location_name}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          {kind === "issue" || kind === "consume" ? (
            <Field label={t("operations.vehicle")} hint={t("operations.vehicle_hint")}>
              <select value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
                <option value="">—</option>
                {(vehicles.data?.vehicles ?? []).map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          {kind === "write_off" ? (
            <Field label={t("operations.reason")}>
              <select value={reasonId} onChange={(e) => setReasonId(e.target.value)}>
                <option value="">—</option>
                {(reasons.data?.reasons ?? []).filter((r) => r.state === "active").map((r) => (
                  <option key={r.id} value={r.id}>
                    {reasonName(r)}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
        </div>
        <Field label={t("warehouse.comment")} hint={kind === "write_off" ? t("operations.comment_other") : undefined}>
          <input value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
        {kind === "move" ? (
          <label>
            <input type="checkbox" checked={oneStep} onChange={(e) => setOneStep(e.target.checked)} /> {t("operations.one_step")}
          </label>
        ) : null}
        {kind === "write_off" ? (
          <Field label={t("operations.photos")}>
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) setPhotos([...photos, (await uploadFile("warehouse_document", file)).id]);
              }}
            />
          </Field>
        ) : null}
        {photos.length ? <p className="small">{t("operations.photos_count", { count: photos.length })}</p> : null}
      </Card>
      {context ? (
        <Card title={t("receipts.lines")}>
          {takes.map((take, index) => (
            <TakeEditor
              key={index}
              contextId={context}
              take={take}
              allowOpen={kind !== "move"}
              checkExpiry={kind === "issue" || kind === "consume"}
              onChange={(next) => setTakes(takes.map((x, i) => (i === index ? next : x)))}
              onRemove={() => setTakes(takes.filter((_, i) => i !== index))}
            />
          ))}
          <ItemPicker
            onPick={(item: WarehouseItem) =>
              setTakes([
                ...takes,
                { item, asset_ids: [], lot_id: "", form: "", quantity: "", confirm_expired: false },
              ])
            }
          />
        </Card>
      ) : null}
      <ErrorMessage error={create.error} />
      <div className="actions">
        <button type="button" disabled={create.pending || !takes.length || !context} onClick={() => void submit()}>
          {t(`operations.submit_${kind}`)}
        </button>
      </div>
    </div>
  );
}

function TakeEditor({
  contextId,
  take,
  allowOpen,
  checkExpiry,
  onChange,
  onRemove,
}: {
  contextId: string;
  take: TakeForm;
  allowOpen: boolean;
  checkExpiry: boolean;
  onChange: (take: TakeForm) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const quantity = useQuantity();
  const unit = t(`warehouse.unit_short_${take.item.unit}`);
  const sources = useApi<Sources>(
    ["warehouse-sources", company.public_id, take.item.id, contextId],
    `/c/${company.public_id}/warehouse/items/${take.item.id}/sources?context_id=${contextId}`,
  );
  const data = sources.data;
  const lots = data?.lots ?? [];
  // § 11.3.1: the suggested lot comes first; another one may be chosen.
  const lot = lots.find((l) => l.id === take.lot_id) ?? lots[0];
  const formOptions = lot
    ? [
        ...lot.forms.map((f) => ({
          value: f.packaging_id ? `pack:${f.packaging_id}` : "loose",
          label: f.packaging_id
            ? t("operations.form_packs", {
                packs: Number(f.quantity) / Number(f.packaging_quantity),
                size: quantity(f.packaging_quantity ?? 0, take.item.unit),
              })
            : t("operations.form_loose", { quantity: quantity(f.quantity, take.item.unit) }),
        })),
        ...lot.opened_packages.map((o, i) => ({
          value: `opened:${o.id}`,
          label: t("operations.form_opened", { n: i + 1, remaining: quantity(o.remaining, take.item.unit) }),
        })),
      ]
    : [];
  const form = formOptions.some((o) => o.value === take.form) ? take.form : (formOptions[0]?.value ?? "");
  const current = { ...take, lot_id: lot?.id ?? "", form };
  const stale = current.lot_id !== take.lot_id || current.form !== take.form;
  // Defaults follow the suggested lot and its first form once the sources are loaded.
  useEffect(() => {
    if (stale) onChange(current);
  });
  return (
    <div className="line-card">
      <h3>
        {take.item.name} <span className="small muted">{unit}</span>
      </h3>
      {sources.isLoading ? <Loading /> : null}
      {take.item.accounting === "individual" ? (
        data?.assets.length ? (
          <div className="stack">
            {data.assets.map((a) => (
              <label key={a.id}>
                <input
                  type="checkbox"
                  checked={take.asset_ids.includes(a.id)}
                  onChange={(e) =>
                    onChange({
                      ...take,
                      asset_ids: e.target.checked ? [...take.asset_ids, a.id] : take.asset_ids.filter((x) => x !== a.id),
                    })
                  }
                />{" "}
                {a.public_id} {a.serial_number ? `· ${a.serial_number}` : ""}{" "}
                <span className="small muted">{a.tags.join(", ")}</span>
              </label>
            ))}
          </div>
        ) : (
          <p className="muted">{t("operations.nothing_available")}</p>
        )
      ) : lots.length ? (
        <div className="row">
          <Field label={t("receipts.lot")} hint={t("operations.lot_hint")}>
            <select value={current.lot_id} onChange={(e) => onChange({ ...take, lot_id: e.target.value, form: "" })}>
              {lots.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.public_id}
                  {l.expiry_date ? ` · ${l.expiry_date}` : ""}
                  {l.expired ? ` · ${t("stock.expired")}` : ""}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("operations.form")}>
            <select value={current.form} onChange={(e) => onChange({ ...current, form: e.target.value })}>
              {formOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label={t("operations.quantity_unit", { unit })}
            hint={current.form.startsWith("pack:") ? (allowOpen ? t("operations.open_hint") : t("operations.whole_packs_hint")) : undefined}
          >
            <input inputMode="decimal" value={take.quantity} onChange={(e) => onChange({ ...current, quantity: e.target.value })} />
          </Field>
        </div>
      ) : (
        <p className="muted">{t("operations.nothing_available")}</p>
      )}
      {checkExpiry && lot?.expired ? (
        <label className="alert warn" style={{ display: "block" }}>
          <input
            type="checkbox"
            checked={take.confirm_expired}
            onChange={(e) => onChange({ ...current, confirm_expired: e.target.checked })}
          />{" "}
          {t("operations.confirm_expired", { lot: lot.public_id })}
        </label>
      ) : null}
      <button type="button" className="secondary" onClick={onRemove}>
        {t("receipts.remove_line")}
      </button>
    </div>
  );
}

/** An operation: its lines, costs and the next steps — return or consumption of the issued,
 * receipt of a move and settling of a shortage. */
export function OperationPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const quantity = useQuantity();
  const money = useMoney();
  const reasonName = useReasonName();
  const base = `/c/${company.public_id}/warehouse/operations/${id}`;
  const data = useApi<{ operation: StockOperation }>(["warehouse-operation", company.public_id, id], base);
  const returning = useCommand<Record<string, unknown>, unknown>(`${base}/return`);
  const consuming = useCommand<Record<string, unknown>, unknown>(`${base}/consume`);
  const receiving_ = useCommand<Record<string, unknown>, unknown>(`${base}/receive`);
  const settling = useCommand<Record<string, unknown>, unknown>(`${base}/settle`);
  const commands = { return: returning, consume: consuming, receive: receiving_, settle: settling };
  const pending = Object.values(commands).some((c) => c.pending);
  const error = returning.error ?? consuming.error ?? receiving_.error ?? settling.error;
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const reasons = useReasons();
  const [settleReason, setSettleReason] = useState("");
  if (!data.data) return <div className="page"><Loading /></div>;
  const operation = data.data.operation;

  async function run(path: keyof typeof commands, body: Record<string, unknown>) {
    // The command keeps one Idempotency-Key while the same request is retried.
    if ((await commands[path].run(body)) !== null) {
      setAmounts({});
      await client.invalidateQueries({ queryKey: ["warehouse-operation", company.public_id, id] });
      await client.invalidateQueries({ queryKey: ["warehouse-operations", company.public_id] });
      await client.invalidateQueries({ queryKey: ["warehouse-stock", company.public_id] });
    }
  }

  // Lines with an entered amount: a ticked unit or a quantity of a lot.
  const selected = () =>
    operation.lines
      .filter((l) => amounts[l.id] !== undefined && amounts[l.id] !== "")
      .map((l) => ({ line_id: l.id, quantity: l.asset ? 1 : amounts[l.id] }));

  const issueOpen = operation.kind === "issue" && operation.lines.some((l) => Number(l.outstanding) > 0) && can("warehouse.issue");
  const receiving = operation.kind === "move" && operation.state === "in_transit" && can("warehouse.move");

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/warehouse?tab=operations")}>← {t("warehouse.tab_operations")}</Link>
      </p>
      <h1>
        {operationTitle(t, operation)}{" "}
        <span className={`badge${operation.state === "done" || operation.state === "received" ? " ok" : " warn"}`}>
          {t(`operations.state_${operation.state}`)}
        </span>
      </h1>
      <Card>
        <p>
          {new Date(operation.created_at).toLocaleString()} · {operation.context.location_name}
          {operation.target_context ? ` → ${operation.target_context.location_name}` : ""}
          {operation.vehicle ? ` · ${t("operations.vehicle")}: ${operation.vehicle.name}` : ""}
          {operation.reason ? ` · ${t("operations.reason")}: ${reasonName(operation.reason)}` : ""}
        </p>
        {operation.comment ? <p>{operation.comment}</p> : null}
        {operation.received_at ? <p className="small">{t("operations.received_at")}: {new Date(operation.received_at).toLocaleString()}</p> : null}
        {operation.photos.length ? (
          <p>
            {operation.photos.map((p, i) => (
              <a key={p} href={`/api/files/${p}`} target="_blank" rel="noreferrer" style={{ marginRight: 8 }}>
                {t("operations.photo_n", { n: i + 1 })}
              </a>
            ))}
          </p>
        ) : null}
        {operation.children.map((c) => (
          <p key={c.id} className="small">
            <Link to={companyPath(company, `/warehouse/operations/${c.id}`)}>{operationTitle(t, c)}</Link>
          </p>
        ))}
        {operation.receipt ? (
          <p className="small">
            <Link to={companyPath(company, `/warehouse/receipts/${operation.receipt.id}`)}>
              {t("receipts.title_number", { number: operation.receipt.number })}
            </Link>
          </p>
        ) : null}
        {operation.parent_id ? (
          <p className="small">
            <Link to={companyPath(company, `/warehouse/operations/${operation.parent_id}`)}>{t("operations.parent")}</Link>
          </p>
        ) : null}
      </Card>
      <Card title={t("receipts.lines")}>
        <table>
          <thead>
            <tr>
              <th>{t("warehouse.name")}</th>
              <th>{t("operations.what")}</th>
              <th>{t("operations.quantity")}</th>
              {operation.kind === "issue" ? <th>{t("operations.outstanding")}</th> : null}
              {operation.kind === "move" ? <th>{t("operations.received")}</th> : null}
              <th>{t("operations.cost")}</th>
              {issueOpen || receiving || operation.state === "discrepancy" ? <th /> : null}
            </tr>
          </thead>
          <tbody>
            {operation.lines.map((line) => (
              <tr key={line.id}>
                <td>
                  {line.output ? "→ " : ""}
                  {line.item.name}
                </td>
                <td className="small">
                  {line.asset?.public_id ?? line.lot?.public_id}
                  {line.packaging_quantity ? ` · ${t("operations.pack_of", { size: quantity(line.packaging_quantity, line.item.unit) })}` : ""}
                  {line.opened_package_id ? ` · ${t("operations.opened")}` : ""}
                  {line.target_lot ? ` → ${line.target_lot}` : ""}
                  {line.expired_confirmed ? <span className="badge warn">{t("stock.expired")}</span> : null}
                </td>
                <td>{quantity(line.quantity, line.item.unit)}</td>
                {operation.kind === "issue" ? (
                  <td className="small">
                    {quantity(line.outstanding, line.item.unit)}
                    {Number(line.returned_quantity) ? <div>{t("operations.returned")}: {quantity(line.returned_quantity, line.item.unit)}</div> : null}
                    {Number(line.consumed_quantity) ? <div>{t("operations.consumed")}: {quantity(line.consumed_quantity, line.item.unit)}</div> : null}
                  </td>
                ) : null}
                {operation.kind === "move" ? (
                  <td className="small">
                    {quantity(line.received_quantity, line.item.unit)}
                    {Number(line.shortage) > 0 && operation.state !== "in_transit" ? (
                      <div className="badge warn">{t("operations.shortage", { value: quantity(line.shortage, line.item.unit) })}</div>
                    ) : null}
                  </td>
                ) : null}
                <td>{line.output ? "" : line.cost === null ? "—" : `${money(line.cost, operation.currency)}${line.cost_known ? "" : ` · ${t("receipts.cost_incomplete")}`}`}</td>
                {(issueOpen && Number(line.outstanding) > 0) || receiving ? (
                  <td>
                    {line.asset ? (
                      <input
                        type="checkbox"
                        checked={amounts[line.id] !== undefined}
                        onChange={(e) =>
                          setAmounts(
                            e.target.checked
                              ? { ...amounts, [line.id]: "1" }
                              : Object.fromEntries(Object.entries(amounts).filter(([key]) => key !== line.id)),
                          )
                        }
                      />
                    ) : (
                      <input
                        inputMode="decimal"
                        style={{ width: 90 }}
                        placeholder={receiving ? line.quantity : line.outstanding}
                        value={amounts[line.id] ?? ""}
                        onChange={(e) => setAmounts({ ...amounts, [line.id]: e.target.value })}
                      />
                    )}
                  </td>
                ) : null}
                {operation.state === "discrepancy" && Number(line.shortage) > 0 ? (
                  <td>
                    {can("warehouse.move") ? (
                      <button type="button" className="link" onClick={() => void run("settle", { line_id: line.id, action: "return" })}>
                        {t("operations.settle_return")}
                      </button>
                    ) : null}{" "}
                    {can("warehouse.write_off") ? (
                      <button
                        type="button"
                        className="link"
                        disabled={!settleReason}
                        onClick={() => void run("settle", { line_id: line.id, action: "write_off", reason_id: settleReason })}
                      >
                        {t("operations.settle_write_off")}
                      </button>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
        {operation.state === "discrepancy" && can("warehouse.write_off") ? (
          <Field label={t("operations.reason")} hint={t("operations.settle_hint")}>
            <select value={settleReason} onChange={(e) => setSettleReason(e.target.value)}>
              <option value="">—</option>
              {(reasons.data?.reasons ?? []).filter((r) => r.state === "active" && r.code !== "other").map((r) => (
                <option key={r.id} value={r.id}>
                  {reasonName(r)}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <ErrorMessage error={error} />
        <div className="actions">
          {issueOpen ? (
            <>
              <button type="button" disabled={pending} onClick={() => void run("return", { lines: selected() })}>
                {t("operations.return")}
              </button>
              <button type="button" className="secondary" disabled={pending} onClick={() => void run("consume", { lines: selected() })}>
                {t("operations.consume_issued")}
              </button>
            </>
          ) : null}
          {receiving ? (
            <>
              <button
                type="button"
                disabled={pending}
                onClick={() =>
                  void run("receive", {
                    lines: operation.lines.map((l) => ({ line_id: l.id, quantity: l.asset ? (amounts[l.id] ? 1 : 0) : (amounts[l.id] ?? l.quantity) || "0" })),
                  })
                }
              >
                {t("operations.receive")}
              </button>
            </>
          ) : null}
        </div>
        {issueOpen ? <p className="muted small">{t("operations.issue_actions_hint")}</p> : null}
        {receiving ? <p className="muted small">{t("operations.receive_hint")}</p> : null}
      </Card>
    </div>
  );
}

/** Repacking inside one lot (§ 11.3.3): everything taken is accounted for in packages and an
 * explicit write-off of losses; the rest not taken stays as it was. */
export function RepackPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const itemId = params.get("item") ?? "";
  const lotId = params.get("lot") ?? "";
  const contextId = params.get("context") ?? "";
  const quantity = useQuantity();
  const reasons = useReasons();
  const reasonName = useReasonName();
  const item = useApi<{ item: WarehouseItem }>(["warehouse-item", company.public_id, itemId], `/c/${company.public_id}/warehouse/items/${itemId}`);
  const sources = useApi<Sources>(
    ["warehouse-sources", company.public_id, itemId, contextId],
    `/c/${company.public_id}/warehouse/items/${itemId}/sources?context_id=${contextId}`,
  );
  const [source, setSource] = useState("");
  const [amount, setAmount] = useState("");
  const [outputs, setOutputs] = useState<{ packaging: string; quantity: string }[]>([{ packaging: "", quantity: "" }]);
  const [loss, setLoss] = useState("");
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const create = useCommand<Record<string, unknown>, { operation: StockOperation }>(`/c/${company.public_id}/warehouse/operations/repack`);
  const lot = sources.data?.lots.find((l) => l.id === lotId);
  const unit = item.data ? item.data.item.unit : "liter";
  const packagings = (item.data?.item.packagings ?? []).filter((p) => p.state === "active");
  const sourceOptions = lot
    ? [
        ...lot.forms.map((f) => ({ value: f.packaging_id ? `pack:${f.packaging_id}` : "loose", label: f.packaging_id ? t("operations.form_packs", { packs: Number(f.quantity) / Number(f.packaging_quantity), size: quantity(f.packaging_quantity ?? 0, unit) }) : t("operations.form_loose", { quantity: quantity(f.quantity, unit) }) })),
        ...lot.opened_packages.map((o, i) => ({ value: `opened:${o.id}`, label: t("operations.form_opened", { n: i + 1, remaining: quantity(o.remaining, unit) }) })),
      ]
    : [];
  const chosen = source || sourceOptions[0]?.value || "";
  const accounted = outputs.reduce((sum, o) => sum + Number(o.quantity || 0), 0) + Number(loss || 0);

  async function submit() {
    const [form, id] = chosen.split(":");
    const result = await create.run({
      context_id: contextId,
      lot_id: lotId,
      source: { packaging_id: form === "pack" ? id : null, opened_package_id: form === "opened" ? id : null, quantity: amount },
      outputs: outputs.filter((o) => o.quantity).map((o) => ({ packaging_id: o.packaging || null, quantity: o.quantity })),
      loss: loss || null,
      loss_reason_id: reason || null,
      comment,
    });
    if (result) navigate(companyPath(company, `/warehouse/operations/${result.operation.id}`), { replace: true });
  }

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, `/warehouse/items/${itemId}`)}>← {item.data?.item.name}</Link>
      </p>
      <h1>{t("operations.repack_title", { lot: lot?.public_id ?? "" })}</h1>
      <p className="muted small">{t("operations.hint_repack")}</p>
      <Card>
        <div className="row">
          <Field label={t("operations.repack_source")}>
            <select value={chosen} onChange={(e) => setSource(e.target.value)}>
              {sourceOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("operations.repack_amount", { unit: t(`warehouse.unit_short_${unit}`) })}>
            <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
        </div>
        <h3>{t("operations.repack_outputs")}</h3>
        {outputs.map((o, index) => (
          <div className="row" key={index}>
            <Field label={t("operations.form")}>
              <select value={o.packaging} onChange={(e) => setOutputs(outputs.map((x, i) => (i === index ? { ...x, packaging: e.target.value } : x)))}>
                <option value="">{t("receipts.form_loose", { unit: t(`warehouse.unit_short_${unit}`) })}</option>
                {packagings.map((p) => (
                  <option key={p.id} value={p.id}>
                    {t("receipts.form_pack", { size: quantity(p.quantity, unit), name: p.name })}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("operations.quantity")}>
              <input inputMode="decimal" value={o.quantity} onChange={(e) => setOutputs(outputs.map((x, i) => (i === index ? { ...x, quantity: e.target.value } : x)))} />
            </Field>
          </div>
        ))}
        <button type="button" className="secondary" onClick={() => setOutputs([...outputs, { packaging: "", quantity: "" }])}>
          {t("operations.repack_add_output")}
        </button>
        <div className="row" style={{ marginTop: 12 }}>
          <Field label={t("operations.repack_loss")} hint={t("operations.repack_loss_hint")}>
            <input inputMode="decimal" value={loss} onChange={(e) => setLoss(e.target.value)} />
          </Field>
          <Field label={t("operations.reason")}>
            <select value={reason} onChange={(e) => setReason(e.target.value)}>
              <option value="">—</option>
              {(reasons.data?.reasons ?? []).filter((r) => r.state === "active").map((r) => (
                <option key={r.id} value={r.id}>
                  {reasonName(r)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("warehouse.comment")}>
            <input value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
        </div>
        <p className={Number(amount || 0) === accounted ? "small" : "alert warn"}>
          {t("operations.repack_balance", { source: quantity(amount || 0, unit), accounted: quantity(accounted, unit) })}
        </p>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="button" disabled={create.pending || !amount} onClick={() => void submit()}>
            {t("operations.submit_repack")}
          </button>
        </div>
      </Card>
    </div>
  );
}

/** Settings of the warehouse: the policy for expired lots (§ 11.2.15) and the reasons of a
 * write-off (§ 11.3.4). PROPOSAL: changed with the right of company settings. */
export function WarehouseSettingsCard() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const reasonName = useReasonName();
  const settings = useApi<{ settings: { expired_policy: string; version: number } }>(
    ["warehouse-settings", company.public_id],
    `/c/${company.public_id}/warehouse/settings`,
  );
  const reasons = useReasons();
  const save = useCommand<{ expired_policy: string; expected_version: number }, unknown>(`/c/${company.public_id}/warehouse/settings`, "PUT");
  const add = useCommand<{ name: string }, unknown>(`/c/${company.public_id}/warehouse/reasons`);
  const change = useCommand<{ id: string; name?: string; archived?: boolean }, unknown>(
    (b) => `/c/${company.public_id}/warehouse/reasons/${b.id}`,
    "PATCH",
  );
  const [name, setName] = useState("");
  const manage = can("company.settings");
  const refresh = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["warehouse-settings", company.public_id] }),
      client.invalidateQueries({ queryKey: ["warehouse-reasons", company.public_id] }),
    ]);
  const current = settings.data?.settings;
  return (
    <Card title={t("operations.settings_title")}>
      {current ? (
        <Field label={t("operations.expired_policy")}>
          <select
            value={current.expired_policy}
            disabled={!manage || save.pending}
            onChange={async (e) => {
              if ((await save.run({ expired_policy: e.target.value, expected_version: current.version })) !== null) await refresh();
            }}
          >
            <option value="warn">{t("operations.expired_warn")}</option>
            <option value="forbid">{t("operations.expired_forbid")}</option>
          </select>
        </Field>
      ) : null}
      <h3>{t("operations.reasons")}</h3>
      <ul>
        {(reasons.data?.reasons ?? []).map((r) => (
          <li key={r.id}>
            <span className={r.state === "archived" ? "muted" : ""}>{reasonName(r)}</span>{" "}
            {manage ? (
              <>
                <button
                  type="button"
                  className="link"
                  onClick={async () => {
                    const next = window.prompt(t("operations.rename_reason"), reasonName(r));
                    if (next && (await change.run({ id: r.id, name: next })) !== null) await refresh();
                  }}
                >
                  {t("operations.rename")}
                </button>{" "}
                <button
                  type="button"
                  className="link"
                  onClick={async () => {
                    if ((await change.run({ id: r.id, archived: r.state === "active" })) !== null) await refresh();
                  }}
                >
                  {r.state === "active" ? t("warehouse.archive") : t("warehouse.restore")}
                </button>
              </>
            ) : null}
          </li>
        ))}
      </ul>
      {manage ? (
        <div className="row">
          <Field label={t("operations.new_reason")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <button
            type="button"
            style={{ alignSelf: "end", flex: "0 0 auto" }}
            disabled={!name.trim() || add.pending}
            onClick={async () => {
              if ((await add.run({ name })) !== null) {
                setName("");
                await refresh();
              }
            }}
          >
            {t("articles.add")}
          </button>
        </div>
      ) : null}
      <ErrorMessage error={save.error ?? add.error ?? change.error} />
    </Card>
  );
}
