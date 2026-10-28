import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import type { ManualCharge, Rental } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Field } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { unitShort } from "../tariffs/TariffsPage";

type Props = { rental: Rental; onDone: (next?: Rental) => Promise<void> };

/** Rentals whose charges may still change: issued and not formalized (§ 13.6.5, decision Т3). */
function open(rental: Rental) {
  return (rental.state === "active" || rental.state === "returned") && !rental.formalization.completed_at;
}

/** Free time of the rental (§ 13.3): the start and either the end or a number of tariff units.
 * The vehicle stays occupied; only the billable time is reduced. */
export function FreeTime({ rental, onDone }: Props) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const zone = rental.calc_timezone;
  const when = (value: string) => formatMoment(value, zone, i18n.language);
  const base = `/c/${company.public_id}/rentals/${rental.id}/free-time`;
  const [start, setStart] = useState(() => (rental.actual_start ? toLocalInput(new Date(rental.actual_start), zone) : ""));
  const [by, setBy] = useState<"units" | "end">("units");
  const [units, setUnits] = useState("1");
  const [end, setEnd] = useState("");
  const [comment, setComment] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  // § 13.4.6: the same free period may be applied to linked time services by explicit choice.
  const timeServices = (rental.services ?? []).filter((s) => s.rental_id === rental.id && s.pricing === "time");
  const add = useCommand<Record<string, unknown>, { rental: Rental }>(base);
  const remove = useCommand<{ id: string; expected_version: number }, { rental: Rental }>(
    (body) => `${base}/${body.id}/remove`,
  );
  const items = rental.free_intervals ?? [];
  const editable = open(rental) && (can("rental.issue") || can("rental.accept_return"));
  const unit = unitShort(rental.calculation?.unit, t);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = {
      start: fromLocalInput(start, zone),
      comment,
      service_line_ids: lines,
      expected_version: rental.version,
    };
    if (by === "units") body.units = Number(units);
    else body.end = fromLocalInput(end, zone);
    const result = await add.run(body);
    if (result) {
      setComment("");
      setLines([]);
      await onDone(result.rental);
    }
  }

  return (
    <div className="stack">
      {items.length === 0 ? <p className="muted">{t("charges.no_free_time")}</p> : null}
      {items.map((item) => (
        <div key={item.id} className="row" style={{ alignItems: "center" }}>
          <span>
            {when(item.start)} — {when(item.end)}
            {item.units ? ` · ${item.units} ${unit}` : ""}
            {item.comment ? ` · ${item.comment}` : ""}
          </span>
          {editable ? (
            <button
              type="button"
              className="secondary"
              disabled={remove.pending}
              onClick={() =>
                void remove
                  .run({ id: item.id, expected_version: rental.version })
                  .then((r) => (r ? onDone(r.rental) : undefined))
              }
            >
              {t("charges.remove")}
            </button>
          ) : null}
        </div>
      ))}
      <ErrorMessage error={remove.error} />
      {editable ? (
        <form onSubmit={submit}>
          <div className="row">
            <Field label={t("charges.free_start")} hint={zone}>
              <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Field label={t("charges.free_by")}>
              <select value={by} onChange={(e) => setBy(e.target.value as "units" | "end")}>
                <option value="units">{t("charges.free_by_units")}</option>
                <option value="end">{t("charges.free_by_end")}</option>
              </select>
            </Field>
            {by === "units" ? (
              <Field label={t("charges.free_units", { unit })}>
                <input value={units} inputMode="numeric" onChange={(e) => setUnits(e.target.value.replace(/\D/g, ""))} />
              </Field>
            ) : (
              <Field label={t("charges.free_end")} hint={zone}>
                <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
              </Field>
            )}
            <Field label={t("bookings.comment")}>
              <input value={comment} onChange={(e) => setComment(e.target.value)} />
            </Field>
          </div>
          {timeServices.length ? (
            <div className="small">
              {t("services.apply_free")}:{" "}
              {timeServices.map((s) => (
                <label key={s.id} style={{ marginLeft: 12 }}>
                  <input
                    type="checkbox"
                    checked={lines.includes(s.id)}
                    onChange={() => setLines(lines.includes(s.id) ? lines.filter((x) => x !== s.id) : [...lines, s.id])}
                  />{" "}
                  {s.name}
                </label>
              ))}
            </div>
          ) : null}
          <p className="muted small">{t("charges.free_hint")}</p>
          <ErrorMessage error={add.error} />
          <div className="actions">
            <button type="submit" disabled={add.pending || !start || (by === "units" ? !units : !end)}>
              {t("charges.add_free")}
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

/** Mileage of the whole rental against the included limit; the overage is its own charge
 * (§ 13.4.1–13.4.4). */
export function MileageView({ rental }: { rental: Rental }) {
  const { t } = useTranslation();
  const m = rental.mileage;
  if (!m) return null;
  if (m.mode === "unlimited") {
    return (
      <p>
        {t("terms.mileage_unlimited")}
        {m.distance !== null ? ` · ${t("charges.distance", { value: Number(m.distance), unit: m.unit })}` : ""}
      </p>
    );
  }
  if (m.distance === null) return <p className="muted">{t("charges.mileage_after_return")}</p>;
  return (
    <dl className="facts">
      <dt>{t("charges.distance_label")}</dt>
      <dd>
        {Number(m.distance)} {m.unit}
      </dd>
      <dt>{t("charges.included")}</dt>
      <dd>
        {Number(m.included ?? 0)} {m.unit}
      </dd>
      <dt>{t("charges.overage")}</dt>
      <dd>
        {Number(m.overage ?? 0)} {m.unit} · <strong>{m.amount} {m.currency}</strong>
      </dd>
    </dl>
  );
}

/** Manual charges (§ 13.5.1): fuel or charge, or another one, with a reason; a mistaken one is
 * cancelled with a reason (decision Т6). */
// Decision П10: an early return or a delay is charged only by hand, with its reason.
const MANUAL_KINDS: ManualCharge["kind"][] = ["fuel", "early_return", "overdue", "other"];

export function ManualCharges({ rental, onDone }: Props) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const base = `/c/${company.public_id}/rentals/${rental.id}/manual-charges`;
  const [kind, setKind] = useState<ManualCharge["kind"]>("fuel");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [cancelling, setCancelling] = useState("");
  const [cancelReason, setCancelReason] = useState("");
  const add = useCommand<Record<string, unknown>, { rental: Rental }>(base);
  const cancel = useCommand<{ id: string; reason: string; expected_version: number }, { rental: Rental }>(
    (body) => `${base}/${body.id}/cancel`,
  );
  const items = rental.manual_charges ?? [];
  const editable = open(rental) && can("rental.manual_charge");
  const currency = rental.tariff_snapshot?.currency ?? "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await add.run({ kind, amount: amount.replace(",", "."), reason, expected_version: rental.version });
    if (result) {
      setAmount("");
      setReason("");
      await onDone(result.rental);
    }
  }

  return (
    <div className="stack">
      {rental.inspections.fuel_differs ? <div className="alert warn">{t("charges.fuel_differs_hint")}</div> : null}
      {items.length === 0 ? <p className="muted">{t("charges.no_manual")}</p> : null}
      {items.map((item) => (
        <div key={item.id} className="stack">
          <div className="row" style={{ alignItems: "center" }}>
            <span style={item.cancelled_at ? { textDecoration: "line-through" } : undefined}>
              {t(`charges.kind_${item.kind}`)} · {Number(item.amount)} {item.currency} · {item.reason} ·{" "}
              {formatMoment(item.created_at, rental.calc_timezone, i18n.language)}
            </span>
            {item.cancelled_at ? (
              <span className="muted small">{t("charges.cancelled", { reason: item.cancel_reason })}</span>
            ) : editable ? (
              <button type="button" className="secondary" onClick={() => setCancelling(cancelling === item.id ? "" : item.id)}>
                {t("charges.cancel")}
              </button>
            ) : null}
          </div>
          {cancelling === item.id ? (
            <div className="row" style={{ alignItems: "flex-end" }}>
              <Field label={t("charges.cancel_reason")}>
                <input value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} />
              </Field>
              <button
                type="button"
                className="danger"
                disabled={cancel.pending || !cancelReason.trim()}
                onClick={() =>
                  void cancel
                    .run({ id: item.id, reason: cancelReason, expected_version: rental.version })
                    .then((r) => {
                      if (!r) return undefined;
                      setCancelling("");
                      setCancelReason("");
                      return onDone(r.rental);
                    })
                }
              >
                {t("charges.cancel")}
              </button>
            </div>
          ) : null}
        </div>
      ))}
      <ErrorMessage error={cancel.error} />
      {editable ? (
        <form onSubmit={submit}>
          <div className="row">
            <Field label={t("charges.kind")}>
              <select value={kind} onChange={(e) => setKind(e.target.value as ManualCharge["kind"])}>
                {MANUAL_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {t(`charges.kind_${k}`)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("charges.amount", { currency })}>
              <input value={amount} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} />
            </Field>
            <Field label={t("issue_rules.reason")}>
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
          </div>
          <ErrorMessage error={add.error} />
          <div className="actions">
            <button type="submit" disabled={add.pending || !amount || !reason.trim()}>
              {t("charges.add_manual")}
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
