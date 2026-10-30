/** One inspection (ТЗ гл. 10): filled in on a phone while it is a draft or started — readings,
 * photos by the positions of its template version, videos, damages on the scheme of the
 * vehicle — and saved; afterwards it is read-only with its files, damages and history
 * (§ 10.1.6). Previous inspections of the vehicle and their photos are at hand (§ 10.3.7). */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import { api, ApiError, type UploadedFile } from "../../api/client";
import type { DamageDraftEntry, Inspection, InspectionFileItem, Vehicle } from "../../api/types";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { JournalTable } from "../settings/EmployeesPage";
import { useLocations } from "../settings/LocationsPage";
import { useRentals, useVehicles } from "../vehicles/VehiclesPage";
import {
  stateBadge,
  useInspection,
  useInspectionHistory,
  usePositionName,
  useRefreshInspections,
  useVehicleDamages,
  useVehicleInspections,
  vehicleScheme,
} from "./data";
import { DamageList, DamagesEditor } from "./DamagesEditor";
import { DetailsFields, detailsBody, detailsOf, employeeName, type Details } from "./Fields";
import { PhotoInput, PhotoThumb, VideoInput, VideoItem } from "./Media";
import { VehicleScheme, type SchemeId } from "./schemes";
import { useEmployees } from "../settings/EmployeesPage";
import { useServiceOperation } from "../service/hooks";

const FILLING = ["draft", "in_progress"];

type Form = {
  details: Details;
  odometer: string;
  fuel: string;
  cleanliness: string;
  files: InspectionFileItem[];
  damages: DamageDraftEntry[];
};

function formOf(inspection: Inspection): Form {
  return {
    details: detailsOf(inspection),
    odometer: inspection.odometer ? String(Number(inspection.odometer)) : "",
    fuel: inspection.fuel ? String(Number(inspection.fuel)) : "",
    cleanliness: inspection.cleanliness,
    files: inspection.files,
    damages: inspection.damages_draft ?? [],
  };
}

function bodyOf(form: Form, zone: string, planned: boolean): Record<string, unknown> {
  const number = (value: string) => value.trim().replace(",", ".") || null;
  return {
    ...detailsBody(planned ? { ...form.details, performed_at: "" } : form.details, zone),
    odometer: number(form.odometer),
    fuel: number(form.fuel),
    cleanliness: form.cleanliness.trim(),
    files: planned ? [] : form.files.map((f) => ({ file_id: f.file_id, kind: f.kind, position: f.position ?? "" })),
    damages: planned ? [] : form.damages,
  };
}

/** Where the moments of the inspection are shown: the place, else the vehicle's location. */
export function useInspectionZone(inspection: Pick<Inspection, "place"> | null, vehicle: Vehicle | undefined) {
  const company = useCompany();
  const locations = useLocations();
  const byId = (id: string | null | undefined) => locations.data?.locations.find((l) => l.id === id);
  return (
    byId(inspection?.place.location_id)?.timezone ??
    byId(vehicle?.location_id)?.timezone ??
    company.timezone ??
    "UTC"
  );
}

export function InspectionPage() {
  const { id = "" } = useParams();
  const query = useInspection(id);
  const inspection = query.data?.inspection;
  // A video being compressed on the server is shown when it is ready (decision В-8.1).
  const processing = inspection?.files.some((f) => f.state === "processing") ?? false;
  const client = useQueryClient();
  const company = useCompany();
  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(
      () => void client.invalidateQueries({ queryKey: ["inspection", company.public_id, id] }),
      5000,
    );
    return () => window.clearInterval(timer);
  }, [processing, client, company.public_id, id]);
  if (query.isLoading) return <div className="page"><Loading /></div>;
  if (!inspection) return <div className="page"><ErrorMessage error={query.error as ApiError} /></div>;
  return <InspectionView key={inspection.id} inspection={inspection} />;
}

function InspectionView({ inspection }: { inspection: Inspection }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const vehicles = useVehicles();
  const vehicle = vehicles.data?.vehicles.find((v) => v.id === inspection.vehicle_card_id);
  const zone = useInspectionZone(inspection, vehicle);
  const filling = FILLING.includes(inspection.state) || inspection.state === "planned";
  const when = (value: string | null) => formatMoment(value, zone, i18n.language);
  return (
    <div className="page inspection-page">
      <p>
        <Link to={companyPath(company, "/inspections")}>← {t("inspections.title")}</Link>
      </p>
      <h1>
        {t(`inspections.kind_${inspection.kind}`)}{" "}
        <span className={`badge ${stateBadge(inspection.state)}`}>{t(`inspections.state_${inspection.state}`)}</span>
      </h1>
      <Summary inspection={inspection} vehicle={vehicle} when={when} />
      {filling ? (
        <FillForm inspection={inspection} vehicle={vehicle} zone={zone} />
      ) : (
        <SavedContent inspection={inspection} vehicle={vehicle} />
      )}
      <EndActions inspection={inspection} />
      <PreviousInspections inspection={inspection} zone={zone} />
      <History inspection={inspection} zone={zone} />
    </div>
  );
}

function Summary({
  inspection,
  vehicle,
  when,
}: {
  inspection: Inspection;
  vehicle: Vehicle | undefined;
  when: (value: string | null) => string;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const locations = useLocations();
  const employees = useEmployees();
  const performer = employees.data?.employees.find((e) => e.id === inspection.performer_employment_id);
  const location = locations.data?.locations.find((l) => l.id === inspection.place.location_id);
  const place =
    inspection.place.kind === "location"
      ? location?.name ?? "—"
      : [
          inspection.place.note,
          inspection.place.address,
          inspection.place.lat ? `${inspection.place.lat}, ${inspection.place.lng}` : "",
        ]
          .filter(Boolean)
          .join(" · ");
  return (
    <Card>
      <dl className="facts">
        <dt>{t("inspections.vehicle")}</dt>
        <dd>
          <Link to={companyPath(company, `/vehicles/${inspection.vehicle_card_id}`)}>
            {vehicle ? `${vehicle.name} · ${vehicle.vehicle_id}` : "—"}
          </Link>
        </dd>
        <dt>{t("inspections.application")}</dt>
        <dd>
          {t(`inspections.application_${inspection.application}`)}
          {inspection.rental_id ? (
            <>
              {" · "}
              <Link to={companyPath(company, `/rentals/${inspection.rental_id}`)}>{t("inspections.open_rental")}</Link>
            </>
          ) : null}
          {inspection.service_operation_id ? (
            <>
              {" · "}
              <Link to={companyPath(company, `/service/operations/${inspection.service_operation_id}`)}>
                {t("inspections.open_service")}
              </Link>
            </>
          ) : null}
        </dd>
        <dt>{inspection.state === "planned" ? t("inspections.planned_at") : t("inspections.performed_at")}</dt>
        <dd>{when(inspection.state === "planned" ? inspection.planned_at : inspection.performed_at)}</dd>
        <dt>{t("inspections.place")}</dt>
        <dd>{place || "—"}</dd>
        <dt>{t("inspections.performer")}</dt>
        <dd>{employees.data ? employeeName(performer) : "—"}</dd>
        <dt>{t("inspections.entered_at")}</dt>
        <dd>{when(inspection.entered_at)}</dd>
        {inspection.saved_at ? (
          <>
            <dt>{t("inspections.saved_at")}</dt>
            <dd>{when(inspection.saved_at)}</dd>
            <dt>{t("inspections.check")}</dt>
            <dd>{t(`inspections.check_${inspection.check_status}`)}</dd>
          </>
        ) : null}
        {inspection.end_reason ? (
          <>
            <dt>{t("inspections.end_reason")}</dt>
            <dd>{inspection.end_reason}</dd>
          </>
        ) : null}
        {inspection.invalidated_reason ? (
          <>
            <dt>{t("inspections.invalidated_reason")}</dt>
            <dd>
              {inspection.invalidated_reason} · {when(inspection.invalidated_at)}
            </dd>
          </>
        ) : null}
      </dl>
      {inspection.check_required && !inspection.saved_at ? (
        <p className="alert warn">{t("inspections.check_required_hint")}</p>
      ) : null}
      {inspection.origin === "rental" ? <p className="muted small">{t("inspections.origin_rental")}</p> : null}
    </Card>
  );
}

/** The client a participant may be: of the rental, of the service operation or the vehicle's
 * owner card. */
function useLinkedClient(inspection: Inspection, vehicle: Vehicle | undefined): string | null {
  const can = useCan();
  const rentals = useRentals(Boolean(inspection.rental_id) && can("rental.view"));
  const op = useServiceOperation(inspection.service_operation_id ?? undefined);
  const rental = rentals.data?.rentals.find((r) => r.id === inspection.rental_id);
  return rental?.client_id ?? op.data?.operation.client_id ?? vehicle?.owner?.client?.id ?? null;
}

function FillForm({ inspection, vehicle, zone }: { inspection: Inspection; vehicle: Vehicle | undefined; zone: string }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const queryClient = useQueryClient();
  const refresh = useRefreshInspections();
  const positionName = usePositionName();
  const [form, setForm] = useState<Form>(() => formOf(inspection));
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);
  const [conducted, setConducted] = useState<"" | "yes" | "no">("");
  const version = useRef(inspection.version);
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const formRef = useRef(form);
  formRef.current = form;
  const clientId = useLinkedClient(inspection, vehicle);
  const planned = inspection.state === "planned";
  const editable = can("inspection.create") || can("inspection.conduct");
  const damages = useVehicleDamages(inspection.vehicle_card_id);
  const previous = useVehicleInspections(inspection.vehicle_card_id);
  const scheme = vehicleScheme(vehicle);

  const path = `/c/${company.public_id}/inspections/${inspection.id}`;

  function apply(next: Inspection) {
    version.current = next.version;
    queryClient.setQueryData(["inspection", company.public_id, inspection.id], { inspection: next });
  }

  /** Changes are sent one after another, each with the version of the previous answer. */
  function save(next: Form = formRef.current): Promise<Inspection | null> {
    const run = chain.current.then(async () => {
      setSaving(true);
      setError(null);
      try {
        const result = await api<{ inspection: Inspection }>(path, {
          method: "PATCH",
          body: { ...bodyOf(next, zone, planned), expected_version: version.current },
          idempotencyKey: crypto.randomUUID(),
        });
        apply(result.inspection);
        if (formRef.current === next) setDirty(false);
        return result.inspection;
      } catch (caught) {
        setError(caught instanceof ApiError ? caught : new ApiError(0, null));
        return null;
      } finally {
        setSaving(false);
      }
    });
    chain.current = run;
    return run;
  }

  function change(patch: Partial<Form>, persist = false) {
    const next = { ...formRef.current, ...patch };
    formRef.current = next;
    setForm(next);
    setDirty(true);
    if (persist) void save(next);
  }

  function addFile(file: UploadedFile, kind: "photo" | "video", position: string | null) {
    change(
      { files: [...formRef.current.files, { file_id: file.id, kind, position, state: file.state }] },
      true,
    );
  }

  function removeFile(fileId: string) {
    change(
      {
        files: formRef.current.files.filter((f) => f.file_id !== fileId),
        damages: formRef.current.damages.map((d) => ({
          ...d,
          photo_file_ids: d.photo_file_ids.filter((p) => p !== fileId),
        })),
      },
      true,
    );
  }

  async function act(action: string, body: Record<string, unknown> = {}) {
    if (dirty && !(await save())) return;
    await chain.current;
    setError(null);
    try {
      const result = await api<{ inspection: Inspection }>(`${path}/${action}`, {
        method: "POST",
        body: { ...body, expected_version: version.current },
        idempotencyKey: crypto.randomUUID(),
      });
      apply(result.inspection);
      await refresh();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, null));
    }
  }

  const positions = (inspection.template?.positions ?? []).filter((p) => p.enabled);
  const photos = form.files.filter((f) => f.kind === "photo");
  const extra = photos.filter((f) => !f.position || !positions.some((p) => p.code === f.position));
  const videos = form.files.filter((f) => f.kind === "video");
  const earlier = (previous.data?.inspections ?? []).filter((i) => i.id !== inspection.id && i.state === "saved");
  const previousOf = (code: string) => earlier.find((i) => i.files.some((f) => f.position === code));

  if (!editable) {
    return <SavedContent inspection={inspection} vehicle={vehicle} />;
  }

  return (
    <>
      {planned ? (
        <Card title={t("inspections.planned_title")}>
          <p className="muted">{t("inspections.planned_hint")}</p>
          {can("inspection.conduct") ? (
            <div className="actions">
              <button type="button" onClick={() => void act("start")}>
                {t("inspections.start")}
              </button>
            </div>
          ) : null}
        </Card>
      ) : null}

      <Card title={t("inspections.details")}>
        <DetailsFields
          value={form.details}
          onChange={(details) => change({ details })}
          zone={zone}
          initialMoment={new Date(inspection.performed_at)}
          clientId={clientId}
          clientLabel={t("inspections.participant_client")}
          planned={planned}
        />
      </Card>

      <Card title={t("inspections.readings")}>
        <div className="row">
          <Field label={t("inspections.odometer", { unit: inspection.odometer_unit })}>
            <input inputMode="decimal" value={form.odometer} onChange={(e) => change({ odometer: e.target.value })} />
          </Field>
          <Field label={t("inspections.fuel")}>
            <input inputMode="decimal" value={form.fuel} onChange={(e) => change({ fuel: e.target.value })} />
          </Field>
        </div>
        <Field label={t("inspections.cleanliness")} hint={t("inspections.cleanliness_hint")}>
          <input value={form.cleanliness} maxLength={200} onChange={(e) => change({ cleanliness: e.target.value })} />
        </Field>
      </Card>

      {planned ? null : (
        <>
          <Card title={t("inspections.photos")}>
            {positions.length === 0 ? <p className="muted">{t("inspections.no_positions")}</p> : null}
            {positions.map((p) => {
              const taken = photos.filter((f) => f.position === p.code);
              const before = previousOf(p.code);
              return (
                <div key={p.code} className="position">
                  <div className="position-head">
                    <strong>{positionName(p)}</strong>
                    <span className={`badge ${taken.length >= p.count ? "ok" : p.required ? "warn" : ""}`}>
                      {taken.length} / {p.count}
                    </span>
                    {p.required ? <span className="muted small">{t("inspections.position_required")}</span> : null}
                  </div>
                  <div className="media-row">
                    {taken.map((f) => (
                      <PhotoThumb key={f.file_id} fileId={f.file_id} onRemove={() => removeFile(f.file_id)} />
                    ))}
                  </div>
                  <PhotoInput compact onUploaded={(file) => addFile(file, "photo", p.code)} />
                  {before ? (
                    <details className="previous-angle">
                      <summary className="muted small">
                        {t("inspections.previous_angle", {
                          when: formatMoment(before.performed_at, zone, i18n.language),
                        })}
                      </summary>
                      <div className="media-row">
                        {before.files
                          .filter((f) => f.position === p.code)
                          .map((f) => (
                            <PhotoThumb key={f.file_id} fileId={f.file_id} size={72} />
                          ))}
                      </div>
                    </details>
                  ) : null}
                </div>
              );
            })}
            <div className="position">
              <div className="position-head">
                <strong>{t("inspections.extra_photos")}</strong>
              </div>
              <div className="media-row">
                {extra.map((f) => (
                  <PhotoThumb key={f.file_id} fileId={f.file_id} onRemove={() => removeFile(f.file_id)} />
                ))}
              </div>
              <PhotoInput compact onUploaded={(file) => addFile(file, "photo", null)} />
            </div>
          </Card>

          <Card title={t("inspections.videos")}>
            <p className="muted small">{t("inspections.videos_hint")}</p>
            {videos.map((f) => (
              <VideoItem
                key={f.file_id}
                fileId={f.file_id}
                state={inspection.files.find((x) => x.file_id === f.file_id)?.state ?? f.state}
                onRemove={() => removeFile(f.file_id)}
              />
            ))}
            <VideoInput onUploaded={(file) => addFile(file, "video", null)} />
          </Card>

          <Card title={t("inspections.damages")}>
            <DamagesEditor
              scheme={scheme}
              entries={form.damages}
              onChange={(entries) => change({ damages: entries })}
              photos={photos}
              known={damages.data?.damages ?? []}
              rental={inspection.application === "rental"}
            />
          </Card>
        </>
      )}

      <div className="sticky-actions">
        <ErrorMessage error={error} />
        <div className="actions" style={{ marginTop: 0 }}>
          <button type="button" className="secondary" disabled={saving || !dirty} onClick={() => void save()}>
            {saving ? t("inspections.saving") : dirty ? t("inspections.save_draft") : t("inspections.draft_saved")}
          </button>
        </div>
      </div>

      {!planned && can("inspection.conduct") ? (
        <Card title={t("inspections.complete_title")}>
          <p className="muted small">{t("inspections.complete_hint")}</p>
          <fieldset className="choice">
            <legend>{t("inspections.conducted_question")}</legend>
            <label>
              <input type="radio" name="conducted" checked={conducted === "yes"} onChange={() => setConducted("yes")} />{" "}
              {t("inspections.conducted_yes")}
            </label>
            <label>
              <input type="radio" name="conducted" checked={conducted === "no"} onChange={() => setConducted("no")} />{" "}
              {t("inspections.conducted_no")}
            </label>
          </fieldset>
          <div className="actions">
            <button
              type="button"
              disabled={!conducted || saving}
              onClick={() => void act("complete", { conducted: conducted === "yes" })}
            >
              {t("inspections.complete")}
            </button>
          </div>
        </Card>
      ) : null}
    </>
  );
}

/** The saved record: nothing changes any more (§ 10.1.6). */
function SavedContent({ inspection, vehicle }: { inspection: Inspection; vehicle: Vehicle | undefined }) {
  const { t } = useTranslation();
  const positionName = usePositionName();
  const positions = inspection.template?.positions ?? [];
  const photos = inspection.files.filter((f) => f.kind === "photo");
  const videos = inspection.files.filter((f) => f.kind === "video");
  const codes = [...new Set(photos.map((f) => f.position ?? ""))];
  const scheme = vehicleScheme(vehicle);
  const recorded = inspection.damages ?? [];
  const drafted = recorded.length === 0 ? inspection.damages_draft ?? [] : [];
  return (
    <>
      <Card title={t("inspections.readings")}>
        <dl className="facts">
          <dt>{t("inspections.odometer", { unit: inspection.odometer_unit })}</dt>
          <dd>{inspection.odometer ? Number(inspection.odometer) : "—"}</dd>
          <dt>{t("inspections.fuel")}</dt>
          <dd>{inspection.fuel ? Number(inspection.fuel) : "—"}</dd>
          <dt>{t("inspections.cleanliness")}</dt>
          <dd>{inspection.cleanliness || "—"}</dd>
        </dl>
      </Card>
      <Card title={t("inspections.photos")}>
        {photos.length === 0 ? <p className="muted">{t("inspections.no_photos")}</p> : null}
        {codes.map((code) => {
          const position = positions.find((p) => p.code === code);
          return (
            <div key={code || "extra"} className="position">
              <div className="position-head">
                <strong>{code ? positionName(position ?? code) : t("inspections.extra_photos")}</strong>
              </div>
              <div className="media-row">
                {photos
                  .filter((f) => (f.position ?? "") === code)
                  .map((f) => (
                    <PhotoThumb key={f.file_id} fileId={f.file_id} />
                  ))}
              </div>
            </div>
          );
        })}
      </Card>
      {videos.length ? (
        <Card title={t("inspections.videos")}>
          {videos.map((f) => (
            <VideoItem key={f.file_id} fileId={f.file_id} state={f.state} />
          ))}
        </Card>
      ) : null}
      <Card title={t("inspections.damages")}>
        {scheme && recorded.some((d) => d.zone_scheme) ? (
          <VehicleScheme
            id={scheme as SchemeId}
            marks={recorded
              .map((d, index) => ({ d, index }))
              .filter(({ d }) => d.zone_scheme && d.zone_x !== null)
              .map(({ d, index }) => ({ x: Number(d.zone_x), y: Number(d.zone_y), view: null, label: String(index + 1) }))}
          />
        ) : null}
        {recorded.length ? <DamageList damages={recorded} numbered /> : null}
        {drafted.length ? (
          <>
            <p className="muted small">{t("inspections.draft_damages_not_result")}</p>
            <ul>
              {drafted.map((d, index) => (
                <li key={index}>{d.description || d.zone_text || t("inspections.damage_on_scheme")}</li>
              ))}
            </ul>
          </>
        ) : null}
        {recorded.length === 0 && drafted.length === 0 ? <p className="muted">{t("inspections.no_damages")}</p> : null}
      </Card>
    </>
  );
}

function ReasonForm({
  label,
  button,
  danger,
  onSubmit,
}: {
  label: string;
  button: string;
  danger?: boolean;
  onSubmit: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setPending(true);
        void onSubmit(reason.trim()).finally(() => setPending(false));
      }}
    >
      <Field label={label}>
        <textarea rows={2} value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <div className="actions">
        <button type="submit" className={danger ? "danger" : undefined} disabled={pending || !reason.trim()}>
          {button}
        </button>
      </div>
    </form>
  );
}

/** Cancel a planned one, terminate a started one, delete an unused draft, recognise a saved one
 * invalid — each by its own right (§ 10.6.3, § 23.4.1–23.4.2). */
function EndActions({ inspection }: { inspection: Inspection }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const refresh = useRefreshInspections();
  const [open, setOpen] = useState<"" | "cancel" | "terminate" | "invalidate">("");
  const [error, setError] = useState<ApiError | null>(null);
  const path = `/c/${company.public_id}/inspections/${inspection.id}`;
  const options: [typeof open, boolean][] = [
    ["cancel", inspection.state === "planned" && can("inspection.cancel")],
    ["terminate", FILLING.includes(inspection.state) && can("inspection.terminate")],
    ["invalidate", inspection.state === "saved" && can("inspection.invalidate")],
  ];
  const deletable = inspection.state === "draft" && inspection.origin === "form" && can("inspection.create");
  const visible = options.filter(([, shown]) => shown);
  const again = new URLSearchParams({
    vehicle: inspection.vehicle_card_id,
    kind: inspection.kind,
    application: inspection.application,
    ...(inspection.rental_id ? { rental: inspection.rental_id } : {}),
    ...(inspection.service_operation_id ? { service: inspection.service_operation_id } : {}),
  });

  async function run(action: string, method: "POST" | "DELETE", body: Record<string, unknown>) {
    setError(null);
    try {
      await api(method === "DELETE" ? path : `${path}/${action}`, {
        method,
        body: { ...body, expected_version: inspection.version },
        idempotencyKey: crypto.randomUUID(),
      });
      await refresh();
      setOpen("");
      if (method === "DELETE") navigate(companyPath(company, "/inspections"));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, null));
    }
  }

  const finished = ["invalid", "cancelled", "terminated"].includes(inspection.state);
  if (!visible.length && !deletable && !finished) return null;
  return (
    <Card title={t("inspections.other_actions")}>
      <div className="actions" style={{ marginTop: 0 }}>
        {visible.map(([action]) => (
          <button key={action} type="button" className="secondary" onClick={() => setOpen(open === action ? "" : action)}>
            {t(`inspections.${action}`)}
          </button>
        ))}
        {deletable ? (
          <button
            type="button"
            className="secondary"
            onClick={() => {
              if (window.confirm(t("inspections.delete_confirm"))) void run("", "DELETE", {});
            }}
          >
            {t("inspections.delete_draft")}
          </button>
        ) : null}
        {finished && can("inspection.create") ? (
          <Link className="button" to={companyPath(company, `/inspections/new?${again.toString()}`)}>
            {t("inspections.new_again")}
          </Link>
        ) : null}
      </div>
      {open ? (
        <>
          <p className="muted small">{t(`inspections.${open}_hint`)}</p>
          <ReasonForm
            label={t("inspections.reason")}
            button={t(`inspections.${open}`)}
            danger={open === "invalidate"}
            onSubmit={(reason) => run(open, "POST", { reason })}
          />
        </>
      ) : null}
      <ErrorMessage error={error} />
    </Card>
  );
}

/** § 10.3.7: previous inspections of the same vehicle with their photos, of earlier rentals and
 * services too, within the rights to see them. */
function PreviousInspections({ inspection, zone }: { inspection: Inspection; zone: string }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const previous = useVehicleInspections(inspection.vehicle_card_id);
  const items = (previous.data?.inspections ?? []).filter((i) => i.id !== inspection.id);
  return (
    <Card title={t("inspections.previous")}>
      {previous.isLoading ? <Loading /> : null}
      {!previous.isLoading && items.length === 0 ? <p className="muted">{t("inspections.no_previous")}</p> : null}
      {items.map((item) => (
        <details key={item.id} className="previous-item">
          <summary>
            {formatMoment(item.performed_at, zone, i18n.language)} · {t(`inspections.kind_${item.kind}`)} ·{" "}
            <span className={`badge ${stateBadge(item.state)}`}>{t(`inspections.state_${item.state}`)}</span>{" "}
            <span className="muted small">{t("inspections.photo_count", { count: item.files.filter((f) => f.kind === "photo").length })}</span>
          </summary>
          <PreviousPhotos item={item} />
          <p>
            <Link to={companyPath(company, `/inspections/${item.id}`)}>{t("inspections.open")}</Link>
          </p>
        </details>
      ))}
    </Card>
  );
}

function PreviousPhotos({ item }: { item: Inspection }) {
  const { t } = useTranslation();
  const positionName = usePositionName();
  const photos = item.files.filter((f) => f.kind === "photo");
  const codes = [...new Set(photos.map((f) => f.position ?? ""))];
  return (
    <>
      {codes.map((code) => (
        <div key={code || "extra"} className="position">
          <div className="muted small">{code ? positionName(code) : t("inspections.extra_photos")}</div>
          <div className="media-row">
            {photos
              .filter((f) => (f.position ?? "") === code)
              .map((f) => (
                <PhotoThumb key={f.file_id} fileId={f.file_id} size={72} />
              ))}
          </div>
        </div>
      ))}
    </>
  );
}

function History({ inspection, zone }: { inspection: Inspection; zone: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const history = useInspectionHistory(inspection.id, open);
  return (
    <>
      <div className="actions">
        <button type="button" className="secondary" onClick={() => setOpen(!open)}>
          {t("inspections.history")}
        </button>
      </div>
      {open ? (
        <Card title={t("inspections.history")}>
          {history.isLoading ? <Loading /> : null}
          <ErrorMessage error={history.error as ApiError} />
          <div className="table-scroll">
            <JournalTable entries={history.data?.history ?? []} timeZone={zone} />
          </div>
        </Card>
      ) : null}
    </>
  );
}
