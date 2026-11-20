import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { Counterparty, ItemBrief, WarehouseItem, WarehouseOptions } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";

type ReadLine = {
  name: string;
  article: string;
  quantity: string | null;
  unit_cost: string | null;
  item: ItemBrief | null;
  candidates: ItemBrief[];
};
type ReadDocument = {
  supplier_name: string;
  document_date: string | null;
  document_number: string;
  currency: string;
  lines: ReadLine[];
};

/** A document of a supplier read by an outer service (customer's decision of 20.11.2026): the
 * supplier, the date and the lines are offers. A line whose item is found is added to the
 * receipt by a press; for the rest a likely item is chosen or a new one is made. Nothing gets
 * into the receipt by itself. Shown only when a service is connected. */
export function DocumentReading({
  documentId,
  units,
  suppliers,
  supplierId,
  onSupplier,
  onNewSupplier,
  onDate,
  onAdd,
}: {
  documentId: string;
  units: WarehouseOptions["units"];
  suppliers: Counterparty[];
  supplierId: string;
  onSupplier: (id: string) => void;
  /** Opens the window of a new supplier with the name read; absent — no right to make one. */
  onNewSupplier?: (name: string) => void;
  onDate: (date: string) => void;
  onAdd: (item: ItemBrief, quantity: string, unitCost: string) => void;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const read = useCommand<{ document_id: string }, ReadDocument>(
    `/c/${company.public_id}/warehouse/receipts/recognize`,
  );
  const create = useCommand<Record<string, unknown>, { item: WarehouseItem }>(
    `/c/${company.public_id}/warehouse/items`,
  );
  const [found, setFound] = useState<{ of: string; document: ReadDocument } | null>(null);
  // What the employee has done with every line: the item chosen among the likely ones, the unit
  // of a new item, and whether the line is already in the receipt.
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const [unit, setUnit] = useState<Record<number, string>>({});
  const [added, setAdded] = useState<Set<number>>(new Set());
  // Another file is another reading.
  const document = found?.of === documentId ? found.document : null;

  async function run() {
    const result = await read.run({ document_id: documentId });
    if (!result) return;
    setFound({ of: documentId, document: result });
    setChosen({});
    setAdded(new Set());
  }
  const add = (index: number, item: ItemBrief, line: ReadLine) => {
    onAdd(item, line.quantity ?? "", line.unit_cost ?? "");
    setAdded((was) => new Set(was).add(index));
  };
  async function make(index: number, line: ReadLine) {
    const result = await create.run({ name: line.name, article: line.article, unit: unit[index] ?? "piece" });
    if (result) add(index, result.item, line);
  }

  if (!document) {
    return (
      <p>
        <button type="button" className="secondary" disabled={read.pending} onClick={() => void run()}>
          {t(read.pending ? "recognition.running" : "recognition.run")}
        </button>
        <ErrorMessage error={read.error} />
      </p>
    );
  }
  const name = document.supplier_name.toLowerCase();
  // The supplier is a counterparty (customer's decision of 20.11.2026): the one of the same name is
  // offered, else the ones alike — a name inside the other or a common word — or a new one.
  const supplier = name ? suppliers.find((s) => s.name.trim().toLowerCase() === name) : undefined;
  const current = suppliers.find((s) => s.id === supplierId);
  const words = name.split(/[^\p{L}\p{N}]+/u).filter((w) => w.length >= 3);
  const alike = suppliers.filter((s) => {
    const other = s.name.trim().toLowerCase();
    return other.includes(name) || name.includes(other) || words.some((w) => other.includes(w));
  });
  const ready = document.lines.flatMap((line, index) => (line.item && !added.has(index) ? [{ line, index }] : []));
  return (
    <Card title={t("recognition.title")}>
      <p className="small">
        {document.supplier_name ? (
          <>
            {t("receipts.supplier")}: <strong>{document.supplier_name}</strong>{" "}
            {current ? (
              <span className="muted">
                {t("recognition.supplier_chosen", { name: current.name })}
              </span>
            ) : supplier ? (
              <button type="button" className="link" onClick={() => onSupplier(supplier.id)}>
                {t("recognition.supplier_pick")}
              </button>
            ) : (
              <>
                <span className="muted">{t("recognition.supplier_absent")}</span>{" "}
                {alike.length ? (
                  <select
                    value=""
                    className="read-supplier"
                    aria-label={t("recognition.supplier_alike")}
                    onChange={(e) => onSupplier(e.target.value)}
                  >
                    <option value="">{t("recognition.supplier_alike")}</option>
                    {alike.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                ) : null}{" "}
                {onNewSupplier ? (
                  <button type="button" className="link" onClick={() => onNewSupplier(document.supplier_name)}>
                    {t("recognition.supplier_create")}
                  </button>
                ) : null}
              </>
            )}
            {" · "}
          </>
        ) : null}
        {document.document_date ? (
          <>
            {t("recognition.date")}: <strong>{document.document_date.split("-").reverse().join(".")}</strong>{" "}
            <button type="button" className="link" onClick={() => onDate(document.document_date ?? "")}>
              {t("recognition.date_use")}
            </button>
            {" · "}
          </>
        ) : null}
        {document.document_number ? `${t("recognition.number")}: ${document.document_number}` : null}
      </p>
      {document.lines.length === 0 ? <p className="muted">{t("recognition.empty")}</p> : null}
      {document.lines.length ? (
        <div className="table-scroll">
          <table className="read-lines">
            <thead>
              <tr>
                <th>{t("warehouse.name")}</th>
                <th>{t("warehouse.article")}</th>
                <th>{t("recognition.quantity")}</th>
                <th>
                  {t("recognition.price")}
                  {document.currency ? `, ${document.currency}` : ""}
                </th>
                <th>{t("recognition.item")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {document.lines.map((line, index) => {
                const picked = line.item ?? line.candidates.find((c) => c.id === chosen[index]);
                return (
                  <tr key={index}>
                    <td>{line.name}</td>
                    <td>{line.article || "—"}</td>
                    <td className="nowrap">{line.quantity ? Number(line.quantity) : "—"}</td>
                    <td className="nowrap">{line.unit_cost ? Number(line.unit_cost) : "—"}</td>
                    <td>
                      {line.item ? (
                        line.item.name
                      ) : (
                        <>
                          <span className="muted small">{t("recognition.not_found")}</span>
                          {line.candidates.length ? (
                            <select
                              value={chosen[index] ?? ""}
                              aria-label={t("recognition.choose")}
                              disabled={added.has(index)}
                              onChange={(e) => setChosen({ ...chosen, [index]: e.target.value })}
                            >
                              <option value="">{t("recognition.choose")}</option>
                              {line.candidates.map((c) => (
                                <option key={c.id} value={c.id}>
                                  {[c.name, c.article].filter(Boolean).join(" · ")}
                                </option>
                              ))}
                            </select>
                          ) : null}
                        </>
                      )}
                    </td>
                    <td className="nowrap">
                      {added.has(index) ? (
                        <span className="muted">{t("recognition.added")}</span>
                      ) : picked ? (
                        <button type="button" className="secondary" onClick={() => add(index, picked, line)}>
                          {t("recognition.add")}
                        </button>
                      ) : (
                        <>
                          <select
                            value={unit[index] ?? "piece"}
                            aria-label={t("warehouse.unit")}
                            onChange={(e) => setUnit({ ...unit, [index]: e.target.value })}
                          >
                            {units.map((u) => (
                              <option key={u.code} value={u.code}>
                                {t(`warehouse.unit_short_${u.code}`)}
                              </option>
                            ))}
                          </select>{" "}
                          <button
                            type="button"
                            className="secondary"
                            disabled={create.pending}
                            onClick={() => void make(index, line)}
                          >
                            {t("recognition.create")}
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      <ErrorMessage error={create.error} />
      <div className="actions">
        {ready.length > 1 ? (
          <button
            type="button"
            onClick={() => ready.forEach(({ line, index }) => line.item && add(index, line.item, line))}
          >
            {t("recognition.add_all")}
          </button>
        ) : null}
        <button type="button" className="link" disabled={read.pending} onClick={() => void run()}>
          {t("recognition.again")}
        </button>
      </div>
    </Card>
  );
}
