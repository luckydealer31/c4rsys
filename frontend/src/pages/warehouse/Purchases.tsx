import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/queries";
import type { Counterparty, ItemBrief, Purchase } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { CounterpartyForm } from "../clients/Counterparties";
import { ItemPicker } from "./Receipts";
import { useMoney, useQuantity } from "./Stock";

/** Purchases made in advance and received by receipts (§ 11.2.11–11.2.12). Payments to the
 * supplier come with the finance stage (decision of 25.10.2026). */
export function PurchasesList() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const allowed = can("warehouse.purchase") || can("warehouse.receive");
  const list = useApi<{ purchases: Purchase[] }>(
    ["warehouse-purchases", company.public_id],
    `/c/${company.public_id}/warehouse/purchases`,
    allowed,
  );
  if (!allowed) return <p className="muted">{t("warehouse.no_access")}</p>;
  return (
    <>
      {can("warehouse.purchase") ? (
        <div className="actions" style={{ marginTop: 0, marginBottom: 16 }}>
          <Link className="button" to={companyPath(company, "/warehouse/purchases/new")}>
            {t("purchases.create")}
          </Link>
        </div>
      ) : null}
      <Card>
        <p className="muted small">{t("purchases.hint")}</p>
        {list.isLoading ? <Loading /> : null}
        {list.data?.purchases.length === 0 ? <p className="muted">{t("purchases.none")}</p> : null}
        {list.data?.purchases.length ? (
          <table>
            <thead>
              <tr>
                <th>{t("receipts.number_short")}</th>
                <th>{t("purchases.created_at")}</th>
                <th>{t("receipts.supplier")}</th>
                <th>{t("purchases.items")}</th>
                <th>{t("receipts.state")}</th>
              </tr>
            </thead>
            <tbody>
              {list.data.purchases.map((p) => (
                <tr key={p.id}>
                  <td>
                    <Link to={companyPath(company, `/warehouse/purchases/${p.id}`)}>№ {p.number}</Link>
                  </td>
                  <td>{new Date(p.created_at).toLocaleDateString()}</td>
                  <td>{p.supplier?.name ?? t("receipts.supplier_unknown")}</td>
                  <td className="small">{p.lines.map((l) => l.item.name).join(", ")}</td>
                  <td>
                    <span className={`badge${p.state === "completed" ? " ok" : ""}`}>{t(`purchases.state_${p.state}`)}</span>
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

type Line = { item: ItemBrief; quantity: string; unit_cost: string };

export function NewPurchasePage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const client = useQueryClient();
  const suppliers = useApi<{ counterparties: Counterparty[] }>(
    ["counterparties", company.public_id, "", false],
    `/c/${company.public_id}/counterparties?q=&state=active`,
    can("counterparty.view"),
  );
  const [supplier, setSupplier] = useState("");
  const [comment, setComment] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [newSupplier, setNewSupplier] = useState(false);
  const create = useCommand<Record<string, unknown>, { purchase: Purchase }>(
    `/c/${company.public_id}/warehouse/purchases`,
  );

  async function submit() {
    const result = await create.run({
      supplier_id: supplier || null,
      comment,
      lines: lines.map((l) => ({ item_id: l.item.id, quantity: l.quantity, unit_cost: l.unit_cost || null })),
    });
    if (result) {
      await client.invalidateQueries({ queryKey: ["warehouse-purchases", company.public_id] });
      navigate(companyPath(company, `/warehouse/purchases/${result.purchase.id}`), { replace: true });
    }
  }

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/warehouse?tab=purchases")}>← {t("warehouse.tab_purchases")}</Link>
      </p>
      <h1>{t("purchases.new_title")}</h1>
      <Card>
        <div className="row">
          <Field label={t("receipts.supplier")} hint={t("receipts.supplier_hint")}>
            <select value={supplier} onChange={(e) => setSupplier(e.target.value)}>
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
          <Field label={t("warehouse.comment")}>
            <input value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
        </div>
      </Card>
      {newSupplier ? (
        <CounterpartyForm
          onCreated={(card) => {
            setSupplier(card.id);
            setNewSupplier(false);
          }}
        />
      ) : null}
      <Card title={t("receipts.lines")}>
        {lines.map((line, index) => {
          const unit = t(`warehouse.unit_short_${line.item.unit}`);
          const set = (key: "quantity" | "unit_cost") => (e: { target: { value: string } }) =>
            setLines(lines.map((l, i) => (i === index ? { ...l, [key]: e.target.value } : l)));
          return (
            <div key={index} className="line-card">
              <h3>{line.item.name}</h3>
              <div className="row">
                <Field label={t("purchases.ordered", { unit })}>
                  <input inputMode="decimal" value={line.quantity} onChange={set("quantity")} />
                </Field>
                <Field label={t("receipts.unit_cost_per", { unit })} hint={t("receipts.unit_cost_hint")}>
                  <input inputMode="decimal" value={line.unit_cost} onChange={set("unit_cost")} />
                </Field>
                <button
                  type="button"
                  className="secondary"
                  style={{ alignSelf: "end", flex: "0 0 auto" }}
                  onClick={() => setLines(lines.filter((_, i) => i !== index))}
                >
                  {t("receipts.remove_line")}
                </button>
              </div>
            </div>
          );
        })}
        <ItemPicker onPick={(item) => setLines([...lines, { item, quantity: "", unit_cost: "" }])} />
      </Card>
      <ErrorMessage error={create.error} />
      <div className="actions">
        <button type="button" disabled={create.pending || !lines.length} onClick={() => void submit()}>
          {t("purchases.create")}
        </button>
      </div>
    </div>
  );
}

/** A purchase: ordered, received and still expected per line; a receipt is made by it and the
 * undelivered rest is cancelled when it is no longer expected (§ 11.2.10–11.2.11). */
export function PurchasePage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const quantity = useQuantity();
  const money = useMoney();
  const base = `/c/${company.public_id}/warehouse/purchases/${id}`;
  const data = useApi<{ purchase: Purchase }>(["warehouse-purchase", company.public_id, id], base);
  const cancel = useCommand<{ line_ids: string[] | null }, unknown>(`${base}/cancel-rest`);
  if (!data.data) return <div className="page"><Loading /></div>;
  const purchase = data.data.purchase;

  async function cancelRest(lineIds: string[] | null) {
    if ((await cancel.run({ line_ids: lineIds })) !== null) {
      await client.invalidateQueries({ queryKey: ["warehouse-purchase", company.public_id, id] });
      await client.invalidateQueries({ queryKey: ["warehouse-purchases", company.public_id] });
    }
  }

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/warehouse?tab=purchases")}>← {t("warehouse.tab_purchases")}</Link>
      </p>
      <h1>
        {t("purchases.title_number", { number: purchase.number })}{" "}
        <span className={`badge${purchase.state === "completed" ? " ok" : ""}`}>{t(`purchases.state_${purchase.state}`)}</span>
      </h1>
      <Card>
        <p>
          {t("receipts.supplier")}: {purchase.supplier?.name ?? t("receipts.supplier_unknown")} ·{" "}
          {new Date(purchase.created_at).toLocaleDateString()}
          {purchase.comment ? ` · ${purchase.comment}` : ""}
        </p>
        <p className="muted small">{t("purchases.payments_later")}</p>
      </Card>
      <Card title={t("receipts.lines")}>
        <table>
          <thead>
            <tr>
              <th>{t("warehouse.name")}</th>
              <th>{t("purchases.ordered_short")}</th>
              <th>{t("purchases.received")}</th>
              <th>{t("purchases.cancelled")}</th>
              <th>{t("purchases.expected")}</th>
              <th>{t("receipts.unit_cost")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {purchase.lines.map((line) => (
              <tr key={line.id}>
                <td>{line.item.name}</td>
                <td>{quantity(line.ordered_quantity, line.item.unit)}</td>
                <td>{quantity(line.received_quantity, line.item.unit)}</td>
                <td>{Number(line.cancelled_quantity) ? quantity(line.cancelled_quantity, line.item.unit) : "—"}</td>
                <td>{quantity(line.expected_quantity, line.item.unit)}</td>
                <td>{money(line.unit_cost, purchase.currency)}</td>
                <td>
                  {Number(line.expected_quantity) > 0 && can("warehouse.purchase") ? (
                    <button type="button" className="link" disabled={cancel.pending} onClick={() => void cancelRest([line.id])}>
                      {t("purchases.cancel_line")}
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <ErrorMessage error={cancel.error} />
        <div className="actions">
          {purchase.state === "open" && can("warehouse.receive") ? (
            <Link className="button" to={companyPath(company, `/warehouse/receipts/new?purchase=${purchase.id}`)}>
              {t("purchases.receive")}
            </Link>
          ) : null}
          {purchase.state === "open" && can("warehouse.purchase") ? (
            <button type="button" className="secondary" disabled={cancel.pending} onClick={() => void cancelRest(null)}>
              {t("purchases.cancel_rest")}
            </button>
          ) : null}
        </div>
        <p className="muted small">{t("purchases.cancel_hint")}</p>
      </Card>
      {purchase.receipts.length ? (
        <Card title={t("purchases.receipts")}>
          <ul>
            {purchase.receipts.map((r) => (
              <li key={r.id}>
                <Link to={companyPath(company, `/warehouse/receipts/${r.id}`)}>№ {r.number}</Link>{" "}
                <span className="small">{t(`receipts.state_${r.state}`)}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}
