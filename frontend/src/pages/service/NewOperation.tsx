import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router";

import type { Client, ServiceOperation } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { fromLocalInput } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { ClientFinder } from "../rentals/IssuePage";
import { useLocations } from "../settings/LocationsPage";
import { useVehicles } from "../vehicles/VehiclesPage";
import { serviceLocations, useServicePosts, useServiceWorks, workTerms, zoneOf } from "./hooks";

/** A service operation in an order: the vehicle and the reason first; the client is taken from
 * the order; the works now or after the diagnostics (§ 16.1.6). A new order is made for the
 * client or for the company itself (§ 16.6.8); `?order=` adds to an existing one. */
export function NewServiceOperationPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const orderId = params.get("order");
  const locations = useLocations();
  const vehicles = useVehicles();
  const works = useServiceWorks();
  const posts = useServicePosts();
  const places = serviceLocations(locations.data?.locations);
  const [locationId, setLocationId] = useState("");
  const [vehicleId, setVehicleId] = useState(params.get("vehicle") ?? "");
  const [internal, setInternal] = useState(false);
  const [client, setClient] = useState<Client | null>(null);
  const [reason, setReason] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [postId, setPostId] = useState("");
  const create = useCommand<Record<string, unknown>, { operation: ServiceOperation }>(
    `/c/${company.public_id}/service/operations`,
  );
  // § 16.2.2: a single available service location is set by itself.
  const location = places.find((l) => l.id === locationId) ?? (places.length === 1 ? places[0] : undefined);
  const zone = zoneOf(location, company);
  const cars = (vehicles.data?.vehicles ?? []).filter((v) => v.state === "active" && !v.storage);
  const active = (works.data?.works ?? []).filter((w) => w.state === "active");
  const ready = Boolean(location && vehicleId && reason.trim() && (orderId || internal || client));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!location) return;
    const body: Record<string, unknown> = {
      location_id: location.id,
      vehicle_card_id: vehicleId,
      reason,
      work_ids: chosen,
      post_id: postId || null,
      planned_start: start ? fromLocalInput(start, zone) : null,
      planned_end: end ? fromLocalInput(end, zone) : null,
    };
    if (orderId) body.order_id = orderId;
    else if (internal) body.internal = true;
    else body.client_id = client?.id;
    const result = await create.run(body);
    if (result) {
      await queryClient.invalidateQueries({ queryKey: ["service-operations"] });
      await queryClient.invalidateQueries({ queryKey: ["orders"] });
      navigate(companyPath(company, `/service/operations/${result.operation.id}`));
    }
  }

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/service")}>← {t("nav.service")}</Link>
      </p>
      <h1>{t("service.new_operation")}</h1>
      {locations.isLoading || vehicles.isLoading ? <Loading /> : null}
      <Card>
        <form className="stack" onSubmit={submit}>
          <div className="row">
            <Field label={t("service.location")}>
              <select value={location?.id ?? ""} onChange={(e) => setLocationId(e.target.value)}>
                {places.length !== 1 ? <option value="">—</option> : null}
                {places.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("service.vehicle")}>
              <select value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
                <option value="">—</option>
                {cars.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {orderId ? (
            <p>
              <Link to={companyPath(company, `/orders/${orderId}`)}>{t("service.to_existing_order")}</Link>
            </p>
          ) : (
            <>
              <label className="small">
                <input type="checkbox" checked={internal} onChange={() => setInternal(!internal)} />{" "}
                {t("orders.internal_order")}
              </label>
              {internal ? null : client ? (
                <p>
                  <strong>{clientName(client)}</strong>{" "}
                  <button type="button" className="secondary" onClick={() => setClient(null)}>
                    {t("rentals.change_client")}
                  </button>
                </p>
              ) : (
                <ClientFinder onPick={setClient} />
              )}
            </>
          )}
          <Field label={t("service.reason")}>
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} />
          </Field>
          <Field label={t("service.works")} hint={t("service.works_later")}>
            <div className="toggle-grid">
              {active.map((w) => (
                <label key={w.id} className="small">
                  <input
                    type="checkbox"
                    checked={chosen.includes(w.id)}
                    onChange={() => setChosen(chosen.includes(w.id) ? chosen.filter((x) => x !== w.id) : [...chosen, w.id])}
                  />{" "}
                  {w.name} <span className="muted">{workTerms(w, t)}</span>
                </label>
              ))}
              {active.length === 0 ? <span className="muted">{t("service.no_works")}</span> : null}
            </div>
          </Field>
          <div className="row">
            <Field label={t("service.planned_start")}>
              <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Field label={t("service.planned_end")}>
              <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
            </Field>
            <Field label={t("service.post")} hint={t("service.post_optional")}>
              <select value={postId} onChange={(e) => setPostId(e.target.value)}>
                <option value="">{t("service.general_zone")}</option>
                {(posts.data?.posts ?? [])
                  .filter((p) => p.location_id === location?.id && p.state === "active")
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
              </select>
            </Field>
          </div>
          <ErrorMessage error={create.error} />
          <div className="actions">
            <button type="submit" disabled={create.pending || !ready}>
              {t("service.create_operation")}
            </button>
          </div>
        </form>
      </Card>
    </div>
  );
}
