import { useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { Client, ExtraService, Order, Rental, ServiceChoice, ServiceLine } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { currencies, formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { useLocations } from "../settings/LocationsPage";
import { unitShort, useTariffs } from "../tariffs/TariffsPage";
import { useVehicles } from "../vehicles/VehiclesPage";

type Priced = Pick<ExtraService, "pricing" | "unit" | "price" | "currency">;

/** «100 THB once», «50 THB per piece», «100 THB per day» (decision Т7). */
export function serviceTerms(service: Priced, t: TFunction) {
  const values = { price: Number(service.price), currency: service.currency, unit: unitShort(service.unit, t) };
  return t(`services.terms_${service.pricing}`, values);
}

function useServices() {
  const company = useCompany();
  return useApi<{ services: ExtraService[] }>(["extra-services", company.public_id], `/c/${company.public_id}/extra-services`);
}

/** Catalog of additional services of the rental (§ 13.4.5–13.4.7) with the tariff rights (§ 23.4.4). */
export function ExtraServicesPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const services = useServices();
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/rentals")}>← {t("nav.rentals")}</Link>
      </p>
      <h1>{t("services.catalog")}</h1>
      {can("order.create") ? (
        <div className="actions" style={{ marginBottom: 16, marginTop: 0 }}>
          <Link className="button secondary" to={companyPath(company, "/rentals/services/order")}>
            {t("services.new_order")}
          </Link>
        </div>
      ) : null}
      <Card>
        {services.isLoading ? <Loading /> : null}
        {services.data?.services.length === 0 ? <p className="muted">{t("services.none")}</p> : null}
        {(services.data?.services ?? []).map((service) => (
          <Link key={service.id} className="list-link" to={companyPath(company, `/rentals/services/${service.id}`)}>
            <strong>{service.name}</strong>{" "}
            {service.state === "archived" ? <span className="badge">{t("tariffs.archived")}</span> : null}
            <div className="muted small">{serviceTerms(service, t)}</div>
          </Link>
        ))}
      </Card>
      {can("tariff.create") ? <ServiceForm /> : null}
    </div>
  );
}

function toggle(list: string[], id: string) {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

/** Name, pricing, price and currency, applicability: the whole company or locations, and an
 * optional limit by vehicles and tariffs (decision У3). Pricing, unit and currency stay fixed. */
function ServiceForm({ service }: { service?: ExtraService }) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const locations = useLocations();
  const vehicles = useVehicles();
  const tariffs = useTariffs();
  const [name, setName] = useState(service?.name ?? "");
  const [pricing, setPricing] = useState<ExtraService["pricing"]>(service?.pricing ?? "once");
  const [unit, setUnit] = useState<string>(service?.unit ?? "day");
  const [price, setPrice] = useState(service ? String(Number(service.price)) : "");
  const [currency, setCurrency] = useState(service?.currency ?? company.main_currency ?? "");
  const [scope, setScope] = useState<string[] | "all">(service && !service.all_locations ? service.locations : "all");
  const [vehicleIds, setVehicleIds] = useState<string[]>(service?.vehicles ?? []);
  const [tariffIds, setTariffIds] = useState<string[]>(service?.tariffs ?? []);
  const [saved, setSaved] = useState(false);
  const save = useCommand<Record<string, unknown>, { service: ExtraService }>(
    service ? `/c/${company.public_id}/extra-services/${service.id}` : `/c/${company.public_id}/extra-services`,
    service ? "PATCH" : "POST",
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    const body: Record<string, unknown> = {
      name,
      price: price.trim().replace(",", "."),
      locations: scope,
      vehicles: vehicleIds,
      tariffs: tariffIds,
    };
    if (service) body.expected_version = service.version;
    else {
      body.pricing = pricing;
      if (pricing === "time") body.unit = unit;
      if (currency) body.currency = currency;
    }
    const result = await save.run(body);
    if (result) {
      setSaved(true);
      if (!service) {
        setName("");
        setPrice("");
      }
      await queryClient.invalidateQueries({ queryKey: ["extra-services", company.public_id] });
      await queryClient.invalidateQueries({ queryKey: ["extra-service", company.public_id] });
    }
  }

  return (
    <Card title={service ? t("services.edit_title") : t("services.create_title")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("tariffs.name")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("services.pricing")} hint={service ? t("services.pricing_fixed") : undefined}>
            <select value={pricing} disabled={Boolean(service)} onChange={(e) => setPricing(e.target.value as ExtraService["pricing"])}>
              {(["once", "quantity", "time"] as const).map((p) => (
                <option key={p} value={p}>
                  {t(`services.pricing_${p}`)}
                </option>
              ))}
            </select>
          </Field>
          {pricing === "time" ? (
            <Field label={t("tariffs.unit")}>
              <select value={unit} disabled={Boolean(service)} onChange={(e) => setUnit(e.target.value)}>
                {(["minute", "hour", "day", "month"] as const).map((u) => (
                  <option key={u} value={u}>
                    {t(`services.unit_${u}`)}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          <Field label={t(`services.price_${pricing}`)}>
            <input value={price} inputMode="decimal" onChange={(e) => setPrice(e.target.value)} />
          </Field>
          <Field label={t("cash.currency")} hint={service ? t("tariffs.currency_fixed") : t("services.currency_hint")}>
            <select value={currency} disabled={Boolean(service)} onChange={(e) => setCurrency(e.target.value)}>
              <option value="">—</option>
              {currencies().map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="small">
          {t("tariffs.availability")}:{" "}
          <label>
            <input type="checkbox" checked={scope === "all"} onChange={() => setScope(scope === "all" ? [] : "all")} />{" "}
            {t("rights.whole_company")}
          </label>
          {scope !== "all"
            ? (locations.data?.locations ?? []).map((location) => (
                <label key={location.id} style={{ marginLeft: 12 }}>
                  <input type="checkbox" checked={scope.includes(location.id)} onChange={() => setScope(toggle(scope, location.id))} />{" "}
                  {location.name}
                </label>
              ))
            : null}
        </div>
        <div className="small">
          {t("services.only_vehicles")}:{" "}
          {(vehicles.data?.vehicles ?? []).map((v) => (
            <label key={v.id} style={{ marginLeft: 12 }}>
              <input type="checkbox" checked={vehicleIds.includes(v.id)} onChange={() => setVehicleIds(toggle(vehicleIds, v.id))} />{" "}
              {v.name}
            </label>
          ))}
        </div>
        <div className="small">
          {t("services.only_tariffs")}:{" "}
          {(tariffs.data?.tariffs ?? []).map((x) => (
            <label key={x.id} style={{ marginLeft: 12 }}>
              <input type="checkbox" checked={tariffIds.includes(x.id)} onChange={() => setTariffIds(toggle(tariffIds, x.id))} />{" "}
              {x.name}
            </label>
          ))}
        </div>
        <p className="muted small">{t("services.scope_hint")}</p>
        <ErrorMessage error={save.error} />
        {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
        <div className="actions">
          <button type="submit" disabled={!name.trim() || !price.trim() || save.pending}>
            {service ? t("common.save") : t("services.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** One service of the catalog: change, archive or restore. */
export function ExtraServicePage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const service = useApi<{ service: ExtraService }>(
    ["extra-service", company.public_id, id],
    `/c/${company.public_id}/extra-services/${id}`,
  );
  const archive = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/extra-services/${id}/archive`);
  const restore = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/extra-services/${id}/restore`);
  const data = service.data?.service;
  if (!data) {
    return <div className="page">{service.isLoading ? <Loading /> : <ErrorMessage error={service.error as ApiError} />}</div>;
  }
  const command = data.state === "active" ? archive : restore;

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/rentals/services")}>← {t("services.catalog")}</Link>
      </p>
      <h1>
        {data.name} {data.state === "archived" ? <span className="badge">{t("tariffs.archived")}</span> : null}
      </h1>
      <p className="muted">{serviceTerms(data, t)}</p>
      {can("tariff.edit") ? <ServiceForm key={data.version} service={data} /> : null}
      {can("tariff.archive") ? (
        <div className="actions">
          <ErrorMessage error={command.error} />
          <button
            type="button"
            className="secondary"
            disabled={command.pending}
            onClick={async () => {
              if (await command.run({ expected_version: data.version })) {
                await queryClient.invalidateQueries({ queryKey: ["extra-service", company.public_id] });
                await queryClient.invalidateQueries({ queryKey: ["extra-services", company.public_id] });
              }
            }}
          >
            {data.state === "active" ? t("tariffs.archive") : t("tariffs.restore")}
          </button>
        </div>
      ) : null}
    </div>
  );
}

// --- Lines ----------------------------------------------------------------------------------------

type LineProps = { line: ServiceLine; onDone: () => Promise<void> };

/** The amount and how it is counted: units of the period after free time for a time service. */
function lineAmount(line: ServiceLine, t: TFunction) {
  const c = line.calculation;
  if (!c) return "—";
  const parts = [`${c.amount} ${c.currency}`];
  if (line.pricing === "time" && c.units !== null) parts.push(`${c.units} ${unitShort(line.unit, t)}`);
  if (c.free_minutes) parts.push(t("charges.free_minutes", { value: c.free_minutes }));
  if (c.kind !== "fixed" && c.kind !== "cancelled") parts.push(t(`services.kind_${c.kind}`));
  return parts.join(" · ");
}

function useLineCommand(line: ServiceLine, action: string) {
  const company = useCompany();
  return useCommand<Record<string, unknown>, { line: ServiceLine }>(`/c/${company.public_id}/service-lines/${line.id}/${action}`);
}

/** Settings of one line: quantity; the own period, start, completion and cancellation of a
 * standalone service (§ 13.4.11); unlinking (§ 13.4.9); free time of a time service (§ 13.4.6). */
function LineSettings({ line, onDone }: LineProps) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const zone = line.time_zone;
  const linked = Boolean(line.rental_id);
  const editable = (linked ? can("rental.issue") : can("order.edit")) && !line.finished;
  const closed = line.state === "completed" || line.state === "cancelled";
  const [quantity, setQuantity] = useState(String(line.quantity ?? ""));
  const [start, setStart] = useState(line.period?.start ? toLocalInput(new Date(line.period.start), zone) : "");
  const [end, setEnd] = useState(line.period?.end ? toLocalInput(new Date(line.period.end), zone) : "");
  const [reason, setReason] = useState("");
  const change = useCommand<Record<string, unknown>, { line: ServiceLine }>(
    `/c/${company.public_id}/service-lines/${line.id}`,
    "PATCH",
  );
  const startCmd = useLineCommand(line, "start");
  const completeCmd = useLineCommand(line, "complete");
  const cancelCmd = useLineCommand(line, "cancel");
  const unlinkCmd = useLineCommand(line, "unlink");
  if (!editable) return null;

  async function run(command: ReturnType<typeof useLineCommand>, body: Record<string, unknown> = {}) {
    if (await command.run({ expected_version: line.version, ...body })) await onDone();
  }

  const canChange = linked ? line.state !== "cancelled" : !closed;
  const own = !linked && line.pricing === "time" && !closed;
  return (
    <div className="stack">
      {(line.pricing === "quantity" && canChange) || own ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const body: Record<string, unknown> = {};
            if (line.pricing === "quantity") body.quantity = Number(quantity);
            if (own) {
              body.period_start = fromLocalInput(start, zone);
              body.period_end = fromLocalInput(end, zone);
            }
            void run(change, body);
          }}
        >
          <div className="row">
            {line.pricing === "quantity" ? (
              <Field label={t("services.quantity")}>
                <input value={quantity} inputMode="numeric" onChange={(e) => setQuantity(e.target.value.replace(/\D/g, ""))} />
              </Field>
            ) : null}
            {own ? (
              <>
                <Field label={t("services.period_start")} hint={zone}>
                  <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
                </Field>
                <Field label={t("services.period_end")} hint={zone}>
                  <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
                </Field>
              </>
            ) : null}
          </div>
          <ErrorMessage error={change.error} />
          <div className="actions">
            <button type="submit" disabled={change.pending}>
              {t("common.save")}
            </button>
          </div>
        </form>
      ) : null}
      <div className="actions">
        {!linked && line.state === "planned" ? (
          <button type="button" disabled={startCmd.pending} onClick={() => void run(startCmd)}>
            {t("services.start")}
          </button>
        ) : null}
        {!linked && line.state === "started" ? (
          <button type="button" disabled={completeCmd.pending} onClick={() => void run(completeCmd)}>
            {t("services.complete")}
          </button>
        ) : null}
        {linked && line.state !== "cancelled" ? (
          <button type="button" className="secondary" disabled={unlinkCmd.pending} onClick={() => void run(unlinkCmd)}>
            {t("services.unlink")}
          </button>
        ) : null}
      </div>
      {linked ? <p className="muted small">{t("services.unlink_hint")}</p> : null}
      <ErrorMessage error={startCmd.error ?? completeCmd.error ?? unlinkCmd.error} />
      {line.state === "planned" ? (
        <div className="row" style={{ alignItems: "flex-end" }}>
          <Field label={t("charges.cancel_reason")}>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <button type="button" className="danger" disabled={cancelCmd.pending || !reason.trim()} onClick={() => void run(cancelCmd, { reason })}>
            {t("services.cancel")}
          </button>
        </div>
      ) : null}
      <ErrorMessage error={cancelCmd.error} />
      {line.pricing === "time" && canChange && (!linked || line.state !== "planned") ? <LineFreeTime line={line} onDone={onDone} /> : null}
    </div>
  );
}

/** Free time of a time service, entered by hand by the rules of the rental's (§ 13.4.6). */
function LineFreeTime({ line, onDone }: LineProps) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const zone = line.time_zone;
  const when = (value: string) => formatMoment(value, zone, i18n.language);
  const base = `/c/${company.public_id}/service-lines/${line.id}/free-time`;
  const [start, setStart] = useState(line.period?.start ? toLocalInput(new Date(line.period.start), zone) : "");
  const [units, setUnits] = useState("1");
  const add = useCommand<Record<string, unknown>, { line: ServiceLine }>(base);
  const remove = useCommand<{ id: string; expected_version: number }, { line: ServiceLine }>((body) => `${base}/${body.id}/remove`);
  const unit = unitShort(line.unit, t);
  return (
    <div className="stack">
      <h3>{t("charges.free_title")}</h3>
      {line.free_intervals.length === 0 ? <p className="muted small">{t("charges.no_free_time")}</p> : null}
      {line.free_intervals.map((item) => (
        <div key={item.id} className="row" style={{ alignItems: "center" }}>
          <span>
            {when(item.start)} — {when(item.end)}
            {item.comment ? ` · ${item.comment}` : ""}
          </span>
          <button
            type="button"
            className="secondary"
            disabled={remove.pending}
            onClick={() => void remove.run({ id: item.id, expected_version: line.version }).then((r) => (r ? onDone() : undefined))}
          >
            {t("charges.remove")}
          </button>
        </div>
      ))}
      <ErrorMessage error={remove.error} />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void add
            .run({ start: fromLocalInput(start, zone), units: Number(units), comment: "", expected_version: line.version })
            .then((r) => (r ? onDone() : undefined));
        }}
      >
        <div className="row">
          <Field label={t("charges.free_start")} hint={zone}>
            <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
          </Field>
          <Field label={t("charges.free_units", { unit })}>
            <input value={units} inputMode="numeric" onChange={(e) => setUnits(e.target.value.replace(/\D/g, ""))} />
          </Field>
        </div>
        <ErrorMessage error={add.error} />
        <div className="actions">
          <button type="submit" className="secondary" disabled={add.pending || !start || !units}>
            {t("charges.add_free")}
          </button>
        </div>
      </form>
    </div>
  );
}

/** One line: name, terms, quantity, period, state and amount; settings unfold on request. */
function LineView({ line, onDone, open, link }: LineProps & { open: boolean; link?: string }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const [shown, setShown] = useState(open);
  useEffect(() => setShown(open), [open]);
  const zone = line.time_zone;
  const when = (value: string | null) => formatMoment(value, zone, i18n.language);
  return (
    <div id={`service-${line.id}`} className="stack" style={{ borderTop: "1px solid var(--border, #ddd)", paddingTop: 8 }}>
      <div className="row" style={{ alignItems: "center" }}>
        <span style={line.state === "cancelled" ? { textDecoration: "line-through" } : undefined}>
          {link ? <Link to={link}>{line.name}</Link> : <strong>{line.name}</strong>} · {serviceTerms(line, t)}
          {line.quantity ? ` · ${t("services.quantity_value", { value: line.quantity })}` : ""} ·{" "}
          {t(`services.state_${line.state}`)}
        </span>
        <span>
          <strong>{lineAmount(line, t)}</strong>
        </span>
        <button type="button" className="secondary" onClick={() => setShown(!shown)}>
          {shown ? t("services.hide") : t("services.settings")}
        </button>
      </div>
      <div className="muted small">
        {line.rental_id ? (
          <Link to={companyPath(company, `/rentals/${line.rental_id}#service-${line.id}`)}>{t("services.linked")}</Link>
        ) : (
          t("services.standalone")
        )}
        {line.origin_rental_id && !line.rental_id ? (
          <>
            {" · "}
            <Link to={companyPath(company, `/rentals/${line.origin_rental_id}`)}>{t("services.was_linked")}</Link>
          </>
        ) : null}
        {line.period ? ` · ${when(line.period.start)} — ${line.period.end ? when(line.period.end) : t("services.open_end")}` : ""}
        {line.actual_start && !line.rental_id ? ` · ${t("services.started_at", { when: when(line.actual_start) })}` : ""}
        {line.actual_end && !line.rental_id ? ` · ${t("services.completed_at", { when: when(line.actual_end) })}` : ""}
        {line.cancelled_at ? ` · ${t("charges.cancelled", { reason: line.cancel_reason })}` : ""}
      </div>
      {/* Keyed by the version: the fields start from the saved values after each change. */}
      {shown ? <LineSettings key={line.version} line={line} onDone={onDone} /> : null}
    </div>
  );
}

/** Services of the rental card (§ 13.4.5): linked ones and those unlinked from it; an order line
 * opens this card on the settings of its service. */
export function RentalServices({ rental, onDone }: { rental: Rental; onDone: (next?: Rental) => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const hash = useLocation().hash;
  const lines = rental.services ?? [];
  const open = ["booked", "active", "returned"].includes(rental.state) && !rental.formalization.completed_at;
  return (
    <div className="stack">
      {lines.length === 0 ? <p className="muted">{t("services.no_lines")}</p> : null}
      {lines.map((line) => (
        <LineView
          key={line.id}
          line={line}
          onDone={() => onDone()}
          open={hash === `#service-${line.id}`}
          link={line.rental_id ? undefined : companyPath(company, `/rentals/service-lines/${line.id}`)}
        />
      ))}
      {open && can("rental.issue") ? <AddLinked rental={rental} onDone={onDone} /> : null}
    </div>
  );
}

function AddLinked({ rental, onDone }: { rental: Rental; onDone: (next?: Rental) => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const choices = useApi<{ services: ServiceChoice[] }>(
    ["service-choices", company.public_id, rental.id, rental.version],
    `/c/${company.public_id}/extra-services/applicable?rental_id=${rental.id}`,
  );
  const [serviceId, setServiceId] = useState("");
  const [quantity, setQuantity] = useState("1");
  const add = useCommand<Record<string, unknown>, { rental: Rental }>(`/c/${company.public_id}/rentals/${rental.id}/services`);
  const chosen = choices.data?.services.find((s) => s.id === serviceId);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = { service_id: serviceId, expected_version: rental.version };
    if (chosen?.pricing === "quantity") body.quantity = Number(quantity);
    const result = await add.run(body);
    if (result) {
      setServiceId("");
      await onDone(result.rental);
    }
  }

  if (choices.data && choices.data.services.length === 0) return <p className="muted small">{t("services.none_applicable")}</p>;
  return (
    <form onSubmit={submit}>
      <div className="row">
        <Field label={t("services.service")}>
          <select value={serviceId} onChange={(e) => setServiceId(e.target.value)}>
            <option value="">—</option>
            {(choices.data?.services ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} · {serviceTerms(s, t)}
              </option>
            ))}
          </select>
        </Field>
        {chosen?.pricing === "quantity" ? (
          <Field label={t("services.quantity")}>
            <input value={quantity} inputMode="numeric" onChange={(e) => setQuantity(e.target.value.replace(/\D/g, ""))} />
          </Field>
        ) : null}
      </div>
      <p className="muted small">{t("services.linked_hint")}</p>
      <ErrorMessage error={add.error} />
      <div className="actions">
        <button type="submit" disabled={add.pending || !serviceId}>
          {t("services.add")}
        </button>
      </div>
    </form>
  );
}

/** Fields of a standalone service: location of the service, the service, its quantity or period. */
function StandaloneFields({
  onSubmit,
  pending,
  error,
  disabled,
}: {
  onSubmit: (body: Record<string, unknown>) => Promise<void>;
  pending: boolean;
  error: ApiError | null;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const locations = useLocations();
  const [locationId, setLocationId] = useState("");
  const location = (locations.data?.locations ?? []).find((l) => l.id === locationId);
  const zone = location?.timezone ?? company.timezone ?? "UTC";
  const choices = useApi<{ services: ServiceChoice[] }>(
    ["service-choices", company.public_id, locationId],
    `/c/${company.public_id}/extra-services/applicable?location_id=${locationId}`,
    Boolean(locationId),
  );
  const [serviceId, setServiceId] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [start, setStart] = useState(() => toLocalInput(new Date(), zone));
  const [end, setEnd] = useState("");
  const chosen = choices.data?.services.find((s) => s.id === serviceId);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const body: Record<string, unknown> = { service_id: serviceId, location_id: locationId };
        if (chosen?.pricing === "quantity") body.quantity = Number(quantity);
        if (chosen?.pricing === "time") {
          body.period_start = fromLocalInput(start, zone);
          body.period_end = fromLocalInput(end, zone);
        }
        void onSubmit(body);
      }}
    >
      <div className="row">
        <Field label={t("services.location")} hint={t("services.location_hint")}>
          <select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
            <option value="">—</option>
            {(locations.data?.locations ?? []).map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("services.service")}>
          <select value={serviceId} disabled={!locationId} onChange={(e) => setServiceId(e.target.value)}>
            <option value="">—</option>
            {(choices.data?.services ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} · {serviceTerms(s, t)}
              </option>
            ))}
          </select>
        </Field>
        {chosen?.pricing === "quantity" ? (
          <Field label={t("services.quantity")}>
            <input value={quantity} inputMode="numeric" onChange={(e) => setQuantity(e.target.value.replace(/\D/g, ""))} />
          </Field>
        ) : null}
        {chosen?.pricing === "time" ? (
          <>
            <Field label={t("services.period_start")} hint={zone}>
              <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Field label={t("services.period_end")} hint={zone}>
              <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
            </Field>
          </>
        ) : null}
      </div>
      <ErrorMessage error={error} />
      <div className="actions">
        <button type="submit" disabled={pending || disabled || !serviceId || (chosen?.pricing === "time" && !end)}>
          {t("services.add")}
        </button>
      </div>
    </form>
  );
}

/** Services of an order: each its own line; a linked one opens the rental card on its settings,
 * a standalone one its own card (§ 13.4.5). New standalone services go to an open client order. */
export function OrderServices({ order, onChange }: { order: Order; onChange: () => Promise<void> }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const key = ["order-services", company.public_id, order.id];
  const lines = useApi<{ lines: ServiceLine[] }>(key, `/c/${company.public_id}/orders/${order.id}/services`);
  const add = useCommand<Record<string, unknown>, { line: ServiceLine }>(`/c/${company.public_id}/service-lines`);
  const open = order.state === "open" || order.state === "ready_to_close";

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: key });
    await onChange();
  }

  const items = lines.data?.lines ?? [];
  if (!items.length && !(open && order.client_id && can("order.edit"))) return null;
  return (
    <Card title={t("services.title")}>
      {lines.isLoading ? <Loading /> : null}
      {items.map((line) => (
        <LineView
          key={line.id}
          line={line}
          onDone={refresh}
          open={false}
          link={companyPath(company, line.rental_id ? `/rentals/${line.rental_id}#service-${line.id}` : `/rentals/service-lines/${line.id}`)}
        />
      ))}
      {open && order.client_id && can("order.edit") ? (
        <>
          <h3>{t("services.add_standalone")}</h3>
          <StandaloneFields
            pending={add.pending}
            error={add.error}
            onSubmit={async (body) => {
              if (await add.run({ ...body, order_id: order.id })) await refresh();
            }}
          />
        </>
      ) : null}
    </Card>
  );
}

/** Card of a standalone service (§ 13.4.5, § 13.4.11). */
export function ServiceLinePage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const queryClient = useQueryClient();
  const key = ["service-line", company.public_id, id];
  const line = useApi<{ line: ServiceLine }>(key, `/c/${company.public_id}/service-lines/${id}`);
  const data = line.data?.line;
  if (!data) {
    return <div className="page">{line.isLoading ? <Loading /> : <ErrorMessage error={line.error as ApiError} />}</div>;
  }
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, `/orders/${data.order_id}`)}>← {t("rentals.open_order")}</Link>
      </p>
      <h1>
        {data.name} · {t(`services.state_${data.state}`)}
      </h1>
      <Card>
        <LineView
          line={data}
          open
          onDone={async () => {
            await queryClient.invalidateQueries({ queryKey: key });
            await queryClient.invalidateQueries({ queryKey: ["order", company.public_id, data.order_id] });
          }}
        />
      </Card>
    </div>
  );
}

/** A new order of a client with only a standalone service (§ 12.1.4; decision У1). */
export function NewServiceOrderPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [client, setClient] = useState<Client | null>(null);
  const clients = useApi<{ clients: Client[] }>(
    ["clients", company.public_id, query],
    `/c/${company.public_id}/clients?q=${encodeURIComponent(query)}`,
  );
  const add = useCommand<Record<string, unknown>, { line: ServiceLine }>(`/c/${company.public_id}/service-lines`);
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/rentals/services")}>← {t("services.catalog")}</Link>
      </p>
      <h1>{t("services.new_order")}</h1>
      <Card title={t("rentals.client")}>
        {client ? (
          <p>
            <strong>{clientName(client)}</strong>{" "}
            <button type="button" className="link" onClick={() => setClient(null)}>
              {t("services.change_client")}
            </button>
          </p>
        ) : (
          <>
            <Field label={t("clients.search")}>
              <input value={query} onChange={(e) => setQuery(e.target.value)} />
            </Field>
            <ul>
              {(clients.data?.clients ?? []).slice(0, 20).map((c) => (
                <li key={c.id}>
                  <button type="button" className="link" onClick={() => setClient(c)}>
                    {clientName(c)}
                  </button>{" "}
                  <span className="small muted">{c.contacts.map((x) => x.value).join(", ")}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
      <Card title={t("services.service")}>
        <StandaloneFields
          pending={add.pending}
          error={add.error}
          disabled={!client}
          onSubmit={async (body) => {
            const result = await add.run({ ...body, client_id: client?.id });
            if (result) navigate(companyPath(company, `/orders/${result.line.order_id}`));
          }}
        />
      </Card>
    </div>
  );
}
