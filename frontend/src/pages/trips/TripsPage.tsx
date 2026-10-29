import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { Delivery, DeliveryPoint, Location } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { useOrders } from "../orders/OrdersPage";
import { useLocations } from "../settings/LocationsPage";
import { LinkedDocuments } from "../documents/DocumentsPage";
import { useVehicles } from "../vehicles/VehiclesPage";
import { PriceFields, TripExpenses, TripPrice, useDeliveryServices } from "./DeliveryMoney";

const FINAL: Delivery["state"][] = ["completed", "cancelled", "terminated", "annulled"];
const RESULTS = ["handed_to_client", "taken_from_client", "accepted_in_location", "not_handed", "other"] as const;
type Result = { delivery: Delivery };

function useTrips(query: string, enabled = true) {
  const company = useCompany();
  return useApi<{ deliveries: Delivery[] }>(
    ["deliveries", company.public_id, query],
    `/c/${company.public_id}/deliveries${query ? `?${query}` : ""}`,
    enabled,
  );
}

function useDrivers(enabled: boolean) {
  const company = useCompany();
  return useApi<{ drivers: { id: string; name: string }[] }>(
    ["delivery-drivers", company.public_id],
    `/c/${company.public_id}/deliveries/drivers`,
    enabled,
  );
}

function useRefresh() {
  const queryClient = useQueryClient();
  return async () => {
    for (const key of ["deliveries", "delivery", "vehicles", "vehicle", "orders", "order"]) {
      await queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
}

export function pointText(point: DeliveryPoint, locations: Location[] | undefined): string {
  const name = locations?.find((l) => l.id === point.location)?.name ?? "—";
  if (point.kind === "location") return name;
  return `${name} / ${point.name || point.address || point.comment || "…"}`;
}

/** Trips: deliveries and internal moves; free ones to take and the driver's own (§ 15.2.4;
 * decision Д-10). */
export function TripsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [all, setAll] = useState(false);
  const mine = useTrips("mine=1&active=1", can("delivery.drive") || can("delivery.take"));
  const free = useTrips("free=1", can("delivery.take"));
  const list = useTrips(all ? "" : "active=1", can("delivery.view"));
  return (
    <div className="page">
      <h1>{t("nav.trips")}</h1>
      <div className="actions" style={{ marginBottom: 16, marginTop: 0 }}>
        {can("delivery.create") ? (
          <Link className="button" to={companyPath(company, "/trips/new")}>
            {t("trips.new")}
          </Link>
        ) : null}
        {can("delivery.catalog") || can("delivery.view") ? (
          <Link className="button secondary" to={companyPath(company, "/trips/services")}>
            {t("trips.catalog")}
          </Link>
        ) : null}
      </div>
      {can("delivery.drive") || can("delivery.take") ? (
        <Card title={t("trips.mine")}>
          <TripTable items={mine.data?.deliveries ?? []} loading={mine.isLoading} />
        </Card>
      ) : null}
      {can("delivery.take") ? (
        <Card title={t("trips.free")}>
          <TripTable items={free.data?.deliveries ?? []} loading={free.isLoading} />
        </Card>
      ) : null}
      {can("delivery.view") ? (
        <Card title={t("trips.all")}>
          <label className="small">
            <input type="checkbox" checked={all} onChange={() => setAll(!all)} /> {t("trips.show_finished")}
          </label>
          <TripTable items={list.data?.deliveries ?? []} loading={list.isLoading} />
        </Card>
      ) : null}
    </div>
  );
}

function TripTable({ items, loading }: { items: Delivery[]; loading: boolean }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const locations = useLocations();
  const zone = company.timezone ?? deviceTimeZone();
  if (loading) return <Loading />;
  if (items.length === 0) return <p className="muted">{t("trips.none")}</p>;
  return (
    <table>
      <tbody>
        {items.map((d) => (
          <tr key={d.id}>
            <td>
              <Link to={companyPath(company, `/trips/${d.id}`)}>№ {d.number}</Link>
            </td>
            <td>{d.vehicle_name ?? "—"}</td>
            <td className="small">
              {pointText(d.from, locations.data?.locations)} → {pointText(d.to, locations.data?.locations)}
            </td>
            <td className="small">{d.planned_start ? formatMoment(d.planned_start, zone, i18n.language) : "—"}</td>
            <td>{d.driver_name ?? t("trips.no_driver")}</td>
            <td>
              <span className={`badge ${FINAL.includes(d.state) ? "" : "warn"}`}>{t(`trips.state_${d.state}`)}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PointFields({
  kind,
  setKind,
  locationId,
  setLocationId,
  address,
  setAddress,
  places,
}: {
  kind: "location" | "other";
  setKind: (v: "location" | "other") => void;
  locationId: string;
  setLocationId: (v: string) => void;
  address: string;
  setAddress: (v: string) => void;
  places: Location[];
}) {
  const { t } = useTranslation();
  return (
    <div className="row">
      <Field label={t("trips.to")}>
        <select value={kind} onChange={(e) => setKind(e.target.value as "location" | "other")}>
          <option value="location">{t("trips.to_location")}</option>
          <option value="other">{t("trips.to_other")}</option>
        </select>
      </Field>
      {kind === "location" ? (
        <Field label={t("trips.location")}>
          <select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
            <option value="">—</option>
            {places.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </Field>
      ) : (
        <Field label={t("trips.address")} hint={t("trips.other_hint")}>
          <input value={address} onChange={(e) => setAddress(e.target.value)} />
        </Field>
      )}
    </div>
  );
}

/** A planned delivery or move: the vehicle, where to, the planned time; the driver may come later
 * (§ 15.1.3). From an order, the delivery is in that order; otherwise in an internal one. */
export function NewTripPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const refresh = useRefresh();
  const [params] = useSearchParams();
  const orderId = params.get("order");
  const vehicles = useVehicles();
  const locations = useLocations();
  const drivers = useDrivers(can("delivery.assign"));
  const [vehicleId, setVehicleId] = useState(params.get("vehicle") ?? "");
  const [kind, setKind] = useState<"location" | "other">(orderId ? "other" : "location");
  const [locationId, setLocationId] = useState("");
  const [address, setAddress] = useState("");
  const [comment, setComment] = useState("");
  const [driverId, setDriverId] = useState("");
  const services = useDeliveryServices(Boolean(orderId));
  const [serviceId, setServiceId] = useState("");
  const [price, setPrice] = useState("");
  const zone = company.timezone ?? deviceTimeZone();
  const [start, setStart] = useState(toLocalInput(new Date(Date.now() + 3600_000), zone));
  const [end, setEnd] = useState(toLocalInput(new Date(Date.now() + 2 * 3600_000), zone));
  const create = useCommand<Record<string, unknown>, Result>(`/c/${company.public_id}/deliveries`);
  const vehicle = vehicles.data?.vehicles.find((v) => v.id === vehicleId);
  const places = (locations.data?.locations ?? []).filter((l) => l.state !== "deactivated" && l.id !== vehicle?.location_id);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await create.run({
      vehicle_card_id: vehicleId,
      to: kind === "location" ? { kind, location_id: locationId } : { kind, address, comment },
      planned_start: fromLocalInput(start, zone),
      planned_end: fromLocalInput(end, zone),
      driver_id: driverId || null,
      order_id: orderId,
      service_id: orderId ? serviceId || null : null,
      price: orderId ? price.trim().replace(",", ".") || null : null,
    });
    if (result) {
      await refresh();
      navigate(companyPath(company, `/trips/${result.delivery.id}`));
    }
  }

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/trips")}>← {t("nav.trips")}</Link>
      </p>
      <h1>{t("trips.new")}</h1>
      <Card>
        <form className="stack" onSubmit={submit}>
          {orderId ? (
            <p>
              <Link to={companyPath(company, `/orders/${orderId}`)}>{t("trips.in_order")}</Link>
            </p>
          ) : (
            <p className="muted small">{t("trips.internal_hint")}</p>
          )}
          <Field label={t("trips.vehicle")}>
            <select value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
              <option value="">—</option>
              {(vehicles.data?.vehicles ?? [])
                .filter((v) => v.state === "active" && !v.storage)
                .map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
            </select>
          </Field>
          {vehicle ? (
            <p className="muted small">
              {t("trips.from", { place: locations.data?.locations.find((l) => l.id === vehicle.location_id)?.name ?? "—" })}
            </p>
          ) : null}
          <PointFields
            kind={kind}
            setKind={setKind}
            locationId={locationId}
            setLocationId={setLocationId}
            address={address}
            setAddress={setAddress}
            places={places}
          />
          {kind === "other" ? (
            <Field label={t("trips.comment")}>
              <input value={comment} onChange={(e) => setComment(e.target.value)} />
            </Field>
          ) : null}
          <div className="row">
            <Field label={t("trips.planned_start")} hint={zone}>
              <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Field label={t("trips.planned_end")} hint={zone}>
              <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
            </Field>
            {can("delivery.assign") ? (
              <Field label={t("trips.driver")} hint={t("trips.driver_later")}>
                <select value={driverId} onChange={(e) => setDriverId(e.target.value)}>
                  <option value="">—</option>
                  {(drivers.data?.drivers ?? []).map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </Field>
            ) : null}
          </div>
          {orderId ? (
            <PriceFields
              services={services.data?.services ?? []}
              serviceId={serviceId}
              setServiceId={setServiceId}
              price={price}
              setPrice={setPrice}
            />
          ) : null}
          <ErrorMessage error={create.error} />
          <div className="actions">
            <button type="submit" disabled={create.pending || !vehicleId || (kind === "location" ? !locationId : !address.trim())}>
              {t("trips.create")}
            </button>
          </div>
        </form>
      </Card>
    </div>
  );
}

/** A trip: its route, plan, driver and the actions by its state (§ 15.3.9). */
export function TripPage() {
  const { t, i18n } = useTranslation();
  const { id } = useParams();
  const company = useCompany();
  const can = useCan();
  const locations = useLocations();
  const query = useApi<Result>(["delivery", company.public_id, id], `/c/${company.public_id}/deliveries/${id}`, Boolean(id));
  const d = query.data?.delivery;
  if (query.isLoading) return <Loading />;
  if (!d) return <ErrorMessage error={query.error instanceof ApiError ? query.error : null} />;
  const zone = company.timezone ?? deviceTimeZone();
  const when = (value: string | null) => formatMoment(value, zone, i18n.language);
  const own = d.driver_id !== null && d.driver_id === company.employment?.id;
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/trips")}>← {t("nav.trips")}</Link>
      </p>
      <h1>
        {t("trips.title", { number: d.number })}{" "}
        <span className={`badge ${FINAL.includes(d.state) ? "" : "warn"}`}>{t(`trips.state_${d.state}`)}</span>
      </h1>
      <Card>
        <dl className="facts">
          <dt>{t("trips.vehicle")}</dt>
          <dd>{d.vehicle_name ?? "—"}</dd>
          <dt>{t("trips.route")}</dt>
          <dd>
            {pointText(d.from, locations.data?.locations)} → {pointText(d.to, locations.data?.locations)}
            {d.to.comment ? <div className="muted small">{d.to.comment}</div> : null}
          </dd>
          {can("order.view") ? (
            <>
              <dt>{t("trips.order")}</dt>
              <dd>
                <Link to={companyPath(company, `/orders/${d.order_id}`)}>№ {d.order_number}</Link>
                {d.is_internal ? ` · ${t("orders.internal_order")}` : ""}
              </dd>
            </>
          ) : null}
          <dt>{t("trips.plan")}</dt>
          <dd>{d.planned_start ? `${when(d.planned_start)} — ${when(d.planned_end)}` : "—"}</dd>
          <dt>{t("trips.driver")}</dt>
          <dd>{d.driver_name ?? t("trips.no_driver")}</dd>
          {d.actual_start ? (
            <>
              <dt>{t("trips.actual")}</dt>
              <dd>
                {when(d.actual_start)} — {d.actual_end ? when(d.actual_end) : "…"}
              </dd>
            </>
          ) : null}
          {d.result ? (
            <>
              <dt>{t("trips.result")}</dt>
              <dd>
                {t(`trips.result_${d.result}`)}
                {d.result_comment ? ` · ${d.result_comment}` : ""}
              </dd>
            </>
          ) : null}
          {d.end_reason ? (
            <>
              <dt>{t("trips.reason")}</dt>
              <dd>{d.end_reason}</dd>
            </>
          ) : null}
        </dl>
        {(d.driver_overlaps ?? []).length ? (
          <p className="alert warn">
            {t("trips.overlaps", { numbers: (d.driver_overlaps ?? []).map((o) => `№ ${o.number}`).join(", ") })}
          </p>
        ) : null}
      </Card>
      {!d.is_internal && d.price !== undefined ? <TripPrice d={d} /> : null}
      {d.expenses !== undefined ? <TripExpenses d={d} /> : null}
      {d.state === "planned" ? <PlannedActions d={d} own={own} zone={zone} /> : null}
      {can("delivery.view") ? <LinkedDocuments objectType="delivery" objectId={d.id} /> : null}
      {d.state === "in_transit" ? <RunningActions d={d} own={own} zone={zone} /> : null}
      {(d.state === "completed" || d.state === "terminated") && can("delivery.correct") ? (
        <Corrections d={d} zone={zone} />
      ) : null}
    </div>
  );
}

function PlannedActions({ d, own, zone }: { d: Delivery; own: boolean; zone: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh();
  const drivers = useDrivers(can("delivery.assign"));
  const orders = useOrders();
  const [driverId, setDriverId] = useState(d.driver_id ?? "");
  const [at, setAt] = useState(toLocalInput(new Date(), zone));
  const [reason, setReason] = useState("");
  const [orderId, setOrderId] = useState("");
  const base = `/c/${company.public_id}/deliveries/${d.id}`;
  const assign = useCommand<Record<string, unknown>, Result>(`${base}/assign`);
  const take = useCommand<Record<string, unknown>, Result>(`${base}/take`);
  const start = useCommand<Record<string, unknown>, Result>(`${base}/start`);
  const cancel = useCommand<Record<string, unknown>, Result>(`${base}/cancel`);
  const attach = useCommand<Record<string, unknown>, Result>(`${base}/attach`);
  const run = (command: typeof assign, body: Record<string, unknown>) =>
    void command.run({ expected_version: d.version, ...body }).then((r) => (r ? refresh() : undefined));
  const clientOrders = (orders.data?.orders ?? []).filter((o) => !o.is_internal && o.client_id && (o.state === "open" || o.state === "ready_to_close"));
  return (
    <>
      {can("delivery.assign") ? (
        <Card title={t("trips.assign")}>
          <div className="row">
            <Field label={t("trips.driver")}>
              <select value={driverId} onChange={(e) => setDriverId(e.target.value)}>
                <option value="">—</option>
                {(drivers.data?.drivers ?? []).map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </Field>
            <div className="actions">
              <button type="button" className="secondary" disabled={assign.pending} onClick={() => run(assign, { driver_id: driverId || null })}>
                {t("common.save")}
              </button>
            </div>
          </div>
          <ErrorMessage error={assign.error} />
        </Card>
      ) : null}
      {!d.driver_id && can("delivery.take") ? (
        <div className="actions">
          <button type="button" disabled={take.pending} onClick={() => run(take, {})}>
            {t("trips.take")}
          </button>
          <ErrorMessage error={take.error} />
        </div>
      ) : null}
      {d.driver_id && (own || can("delivery.start")) ? (
        <Card title={t("trips.start")}>
          <div className="row">
            <Field label={t("trips.actual_start")} hint={zone}>
              <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
            </Field>
            <div className="actions">
              <button type="button" disabled={start.pending} onClick={() => run(start, { actual_start: fromLocalInput(at, zone) })}>
                {t("trips.start_button")}
              </button>
            </div>
          </div>
          <ErrorMessage error={start.error} />
        </Card>
      ) : null}
      {d.is_internal && can("delivery.edit") && can("order.edit") ? (
        <Card title={t("trips.attach")}>
          <div className="row">
            <Field label={t("trips.order")}>
              <select value={orderId} onChange={(e) => setOrderId(e.target.value)}>
                <option value="">—</option>
                {clientOrders.map((o) => (
                  <option key={o.id} value={o.id}>
                    № {o.number}
                  </option>
                ))}
              </select>
            </Field>
            <div className="actions">
              <button type="button" className="secondary" disabled={attach.pending || !orderId} onClick={() => run(attach, { order_id: orderId })}>
                {t("trips.attach_button")}
              </button>
            </div>
          </div>
          <ErrorMessage error={attach.error} />
        </Card>
      ) : null}
      {can("delivery.cancel") ? (
        <Card title={t("trips.cancel")}>
          <div className="row">
            <Field label={t("trips.reason")}>
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
            <div className="actions">
              <button type="button" className="danger" disabled={cancel.pending || !reason.trim()} onClick={() => run(cancel, { reason })}>
                {t("trips.cancel_button")}
              </button>
            </div>
          </div>
          <ErrorMessage error={cancel.error} />
        </Card>
      ) : null}
    </>
  );
}

function RunningActions({ d, own, zone }: { d: Delivery; own: boolean; zone: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh();
  const [at, setAt] = useState(toLocalInput(new Date(), zone));
  const [result, setResult] = useState<string>(d.to.kind === "location" ? "accepted_in_location" : "handed_to_client");
  const [comment, setComment] = useState("");
  const [reason, setReason] = useState("");
  const base = `/c/${company.public_id}/deliveries/${d.id}`;
  const finish = useCommand<Record<string, unknown>, Result>(`${base}/finish`);
  const terminate = useCommand<Record<string, unknown>, Result>(`${base}/terminate`);
  if (!(own || can("delivery.finish"))) return null;
  return (
    <>
      <Card title={t("trips.finish")}>
        <div className="row">
          <Field label={t("trips.actual_end")} hint={zone}>
            <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
          </Field>
          <Field label={t("trips.result")}>
            <select value={result} onChange={(e) => setResult(e.target.value)}>
              {RESULTS.filter((r) => r !== "accepted_in_location" || d.to.kind === "location").map((r) => (
                <option key={r} value={r}>
                  {t(`trips.result_${r}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("trips.comment")}>
            <input value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
          <div className="actions">
            <button
              type="button"
              disabled={finish.pending}
              onClick={() =>
                void finish
                  .run({ expected_version: d.version, actual_end: fromLocalInput(at, zone), result, comment })
                  .then((r) => (r ? refresh() : undefined))
              }
            >
              {t("trips.finish_button")}
            </button>
          </div>
        </div>
        <ErrorMessage error={finish.error} />
      </Card>
      <Card title={t("trips.terminate")}>
        <div className="row">
          <Field label={t("trips.reason")}>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <div className="actions">
            <button
              type="button"
              className="danger"
              disabled={terminate.pending || !reason.trim()}
              onClick={() =>
                void terminate
                  .run({ expected_version: d.version, actual_end: fromLocalInput(at, zone), reason })
                  .then((r) => (r ? refresh() : undefined))
              }
            >
              {t("trips.terminate_button")}
            </button>
          </div>
        </div>
        <ErrorMessage error={terminate.error} />
      </Card>
    </>
  );
}

/** A finished move is corrected or annulled with a reason while nothing began after it; the
 * linked charge is shown for checking (§ 15.3.4–15.3.5; decision Д-11). */
function Corrections({ d, zone }: { d: Delivery; zone: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [start, setStart] = useState(d.actual_start ? toLocalInput(new Date(d.actual_start), zone) : "");
  const [end, setEnd] = useState(d.actual_end ? toLocalInput(new Date(d.actual_end), zone) : "");
  const [result, setResult] = useState(d.result ?? "");
  const [comment, setComment] = useState(d.result_comment);
  const [reason, setReason] = useState("");
  const base = `/c/${company.public_id}/deliveries/${d.id}`;
  const correct = useCommand<Record<string, unknown>, Result>(`${base}/correct`);
  const annul = useCommand<Record<string, unknown>, Result>(`${base}/annul`);
  const later = d.later_operations ?? [];
  const run = (command: typeof correct, body: Record<string, unknown>) =>
    void command.run({ expected_version: d.version, reason, ...body }).then((r) => {
      if (r) {
        setReason("");
        return refresh();
      }
      return undefined;
    });
  return (
    <Card title={t("trips.correction")}>
      {later.length ? (
        <p className="alert warn">
          {t("trips.later_operations")}{" "}
          {later.map((o) => (
            <span key={o.id} style={{ marginRight: 8 }}>
              {o.kind === "rental" ? t("orders.line_rental") : o.kind === "service" ? t("orders.line_service_work") : t("orders.line_delivery")}
              {o.number ? ` № ${o.number}` : ""}
            </span>
          ))}
        </p>
      ) : null}
      {d.charged !== undefined && d.charged !== null ? <p className="small">{t("trips.charged", { amount: Number(d.charged), currency: d.currency ?? "" })}</p> : null}
      <div className="row">
        <Field label={t("trips.actual_start")} hint={zone}>
          <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label={t("trips.actual_end")} hint={zone}>
          <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        {d.state === "completed" ? (
          <Field label={t("trips.result")}>
            <select value={result} onChange={(e) => setResult(e.target.value)}>
              {RESULTS.filter((r) => r !== "accepted_in_location" || d.to.kind === "location").map((r) => (
                <option key={r} value={r}>
                  {t(`trips.result_${r}`)}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field label={t("trips.comment")}>
          <input value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
      </div>
      <div className="row">
        <Field label={t("trips.reason")}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <div className="actions">
          <button
            type="button"
            className="secondary"
            disabled={correct.pending || !reason.trim()}
            onClick={() =>
              run(correct, {
                actual_start: start ? fromLocalInput(start, zone) : null,
                actual_end: end ? fromLocalInput(end, zone) : null,
                result: d.state === "completed" ? result : null,
                comment,
              })
            }
          >
            {t("trips.correct_button")}
          </button>
          <button type="button" className="danger" disabled={annul.pending || !reason.trim()} onClick={() => run(annul, {})}>
            {t("trips.annul_button")}
          </button>
        </div>
      </div>
      <ErrorMessage error={correct.error ?? annul.error} />
    </Card>
  );
}
