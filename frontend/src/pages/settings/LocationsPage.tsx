import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { ContextType, Location } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { Icon, type IconName } from "../../components/icons";
import { TimeZoneSelect } from "../../components/TimeZoneSelect";
import { AVAILABLE_CONTEXTS, contextAvailable, moduleWorks } from "../../contexts";

// Only types whose modules work are offered (§ 4.1.9).
const CONTEXT_TYPES = AVAILABLE_CONTEXTS;
const shown = (contexts: Location["contexts"]) => contexts.filter((c) => contextAvailable(c.type));

export function useLocations() {
  const company = useCompany();
  return useApi<{ locations: Location[] }>(
    ["locations", company.public_id],
    `/c/${company.public_id}/locations`,
  );
}

/** Locations with their contexts, state and schedule (§ 4.1–4.4, § 22.1.5–22.1.6). */
export function LocationsEditor({ createOpen = false }: { createOpen?: boolean }) {
  const { t } = useTranslation();
  const can = useCan();
  const locations = useLocations();
  return (
    <>
      {locations.isLoading ? <Loading /> : null}
      <div className="tiles">
        {(locations.data?.locations ?? []).map((location) => (
          <LocationTile key={location.id} location={location} />
        ))}
      </div>
      {locations.data?.locations.length === 0 ? (
        <p className="muted">{t("locations.none")}</p>
      ) : null}
      {can("company.settings") ? <CreateLocation initiallyOpen={createOpen} /> : null}
    </>
  );
}

function useRefresh() {
  const client = useQueryClient();
  const company = useCompany();
  return async () => {
    await client.invalidateQueries({ queryKey: ["locations", company.public_id] });
    // The setup wizard shows readiness from the same data (§ 22.1.12).
    await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
    // Sections of the menu follow the contexts of the locations (decision of 24.10.2026).
    await client.invalidateQueries({ queryKey: ["company", company.public_id] });
  };
}

/** Creation of a location stays folded under a button (decision of 24.10.2026). */
function CreateLocation({ initiallyOpen }: { initiallyOpen: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(initiallyOpen);
  if (!open) {
    return (
      <div className="actions">
        <button type="button" className="secondary" onClick={() => setOpen(true)}>
          <Icon name="plus" size={18} /> {t("locations.add")}
        </button>
      </div>
    );
  }
  return <CreateLocationForm onDone={() => setOpen(initiallyOpen)} />;
}

function CreateLocationForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState(company.timezone ?? "");
  const [address, setAddress] = useState("");
  // Nothing is chosen in advance (decision 23.10.2026); any combination or none (§ 4.1.1, § 4.1.3).
  const [contexts, setContexts] = useState<ContextType[]>([]);
  const create = useCommand<Record<string, unknown>, { location: Location }>(
    `/c/${company.public_id}/locations`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = { name, address, contexts };
    if (timezone) body.timezone = timezone;
    const result = await create.run(body);
    if (result) {
      setName("");
      setAddress("");
      setContexts([]);
      await refresh();
      onDone();
    }
  }

  return (
    <Card title={t("locations.create_title")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("locations.name")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("companies.timezone")} hint={t("locations.timezone_hint")}>
            <TimeZoneSelect value={timezone} onChange={setTimezone} emptyLabel="—" />
          </Field>
          <Field label={t("locations.address")}>
            <input value={address} onChange={(e) => setAddress(e.target.value)} />
          </Field>
        </div>
        <div className="field">
          <span>{t("locations.contexts")}</span>
          {CONTEXT_TYPES.map((type) => (
            <label key={type} style={{ marginRight: 16 }}>
              <input
                type="checkbox"
                checked={contexts.includes(type)}
                onChange={() =>
                  setContexts(contexts.includes(type) ? contexts.filter((c) => c !== type) : [...contexts, type])
                }
              />{" "}
              {t(`contexts.${type}`)}
            </label>
          ))}
          <p className="muted small">{t("locations.contexts_hint")}</p>
        </div>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={!name.trim() || create.pending}>
            {t("locations.create")}
          </button>
          <button type="button" className="secondary" onClick={onDone}>
            {t("common.cancel")}
          </button>
        </div>
      </form>
    </Card>
  );
}

const CONTEXT_ICONS: Record<ContextType, IconName> = {
  rental: "rentals",
  service: "service",
  warehouse: "warehouse",
};

/** A small card of a location (decision of 24.10.2026): name, address, its contexts as icons
 * and «Edit», which opens the location's own page. */
function LocationTile({ location }: { location: Location }) {
  const { t } = useTranslation();
  const company = useCompany();
  return (
    <section className="card tile">
      <div className="tile-head">
        <h3>{location.name}</h3>
        {location.state !== "active" ? (
          <span className="badge warn">{t(`locations.state_${location.state}`)}</span>
        ) : null}
      </div>
      {location.address ? <p className="muted small">{location.address}</p> : null}
      <div className="context-icons">
        {shown(location.contexts).length === 0 ? (
          <span className="muted small">{t("locations.no_contexts")}</span>
        ) : null}
        {shown(location.contexts).map((c) => (
          <span
            key={c.id}
            className={`context-icon${c.state === "enabled" ? "" : " off"}`}
            title={`${t(`contexts.${c.type}`)}: ${t(`locations.context_${c.state}`)}`}
            aria-label={`${t(`contexts.${c.type}`)}: ${t(`locations.context_${c.state}`)}`}
          >
            <Icon name={CONTEXT_ICONS[c.type]} size={22} />
          </span>
        ))}
      </div>
      <div className="actions">
        <Link className="button secondary" to={companyPath(company, `/company/locations/${location.id}`)}>
          {t("common.edit")}
        </Link>
      </div>
    </section>
  );
}

/** The location's own page: everything about it — name, time zone, address, contexts,
 * deactivation and deletion (§ 4.1–4.4). */
export function LocationPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const { id = "" } = useParams();
  const locations = useLocations();
  const location = locations.data?.locations.find((l) => l.id === id);
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/company")}>← {company.display_name}</Link>
      </p>
      {locations.isLoading ? <Loading /> : null}
      {locations.data && !location ? <p className="muted">{t("locations.not_found")}</p> : null}
      {location ? <LocationCard location={location} editable={can("company.settings")} /> : null}
    </div>
  );
}

function LocationCard({ location, editable }: { location: Location; editable: boolean }) {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const refresh = useRefresh();
  const base = `/c/${company.public_id}/locations/${location.id}`;
  const [form, setForm] = useState({
    name: location.name,
    timezone: location.timezone ?? "",
    address: location.address,
  });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const update = useCommand<Record<string, unknown>, unknown>(base, "PATCH");
  const addContext = useCommand<{ type: string }, unknown>(`${base}/contexts`);
  const state = useCommand<{ action: string; expected_version: number }, unknown>(
    (body) => `${base}/${body.action}`,
  );
  const contextState = useCommand<{ id: string; action: string }, unknown>(
    (body) => `${base}/contexts/${body.id}/${body.action}`,
  );
  const remove = useCommand<Record<string, never>, unknown>(base, "DELETE");
  const deactivated = location.state === "deactivated";
  const missingTypes = CONTEXT_TYPES.filter((type) => !location.contexts.some((c) => c.type === type));
  const error =
    update.error ?? addContext.error ?? state.error ?? contextState.error ?? remove.error;

  async function save(event: FormEvent) {
    event.preventDefault();
    const changes: Record<string, unknown> = { expected_version: location.version };
    if (form.name !== location.name) changes.name = form.name;
    if (form.timezone && form.timezone !== location.timezone) changes.timezone = form.timezone;
    if (form.address !== location.address) changes.address = form.address;
    if ((await update.run(changes)) !== null) await refresh();
  }

  async function run<T>(command: { run: (body: T) => Promise<unknown> }, body: T) {
    if ((await command.run(body)) !== null) await refresh();
  }

  const weekly = Object.values(location.schedule.weekly);
  const isAllDay = weekly.every((day) => day.length === 1 && day[0]?.from === "00:00" && day[0]?.to === "24:00");

  return (
    <Card>
      <h2>
        {location.name}{" "}
        <span className={`badge ${location.state === "active" ? "ok" : "warn"}`}>
          {t(`locations.state_${location.state}`)}
        </span>
      </h2>
      <p className="muted small">
        {t("locations.schedule")}: {isAllDay ? t("locations.schedule_24_7") : t("locations.schedule_custom")}
      </p>
      <table>
        <tbody>
          {shown(location.contexts).length === 0 ? (
            <tr>
              <td className="muted">
                {t("locations.contexts")}: {t("locations.no_contexts")}
              </td>
            </tr>
          ) : null}
          {shown(location.contexts).map((c) => (
            <tr key={c.id}>
              <td>{t(`contexts.${c.type}`)}</td>
              <td>{t(`locations.context_${c.state}`)}</td>
              <td>
                {!moduleWorks(c.type)
                  ? t("contexts.state_module_later")
                  : c.state === "enabled"
                    ? c.ready
                      ? t("locations.ready")
                      : t("locations.not_ready")
                    : "—"}
              </td>
              <td>
                {editable && !(deactivated && c.state === "disabled") ? (
                  <button
                    type="button"
                    className="secondary"
                    disabled={contextState.pending}
                    onClick={() =>
                      void run(contextState, { id: c.id, action: c.state === "enabled" ? "disable" : "enable" })
                    }
                  >
                    {c.state === "enabled" ? t("locations.disable_context") : t("locations.enable_context")}
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editable ? (
        <form className="stack" onSubmit={save}>
          <div className="row">
            <Field label={t("locations.name")}>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label={t("companies.timezone")}>
              <TimeZoneSelect
                value={form.timezone}
                onChange={(timezone) => setForm({ ...form, timezone })}
                emptyLabel="—"
              />
            </Field>
            <Field label={t("locations.address")}>
              <input
                value={form.address}
                onChange={(e) => setForm({ ...form, address: e.target.value })}
              />
            </Field>
          </div>
          <Refusal error={error} />
          <div className="actions">
            <button type="submit" disabled={update.pending}>
              {t("common.save")}
            </button>
            {deactivated
              ? null
              : missingTypes.map((type) => (
                  <button key={type} type="button" className="secondary" onClick={() => void run(addContext, { type })}>
                    {t("locations.add_context", { context: t(`contexts.${type}`) })}
                  </button>
                ))}
          </div>
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={state.pending}
              onClick={() =>
                void run(state, { action: deactivated ? "restore" : "deactivate", expected_version: location.version })
              }
            >
              {deactivated ? t("locations.restore") : t("locations.deactivate")}
            </button>
            {confirmDelete ? (
              <>
                <button
                  type="button"
                  className="danger"
                  disabled={remove.pending}
                  onClick={async () => {
                    if ((await remove.run({})) !== null) {
                      await refresh();
                      navigate(companyPath(company, "/company"));
                    }
                  }}
                >
                  {t("locations.delete_confirm")}
                </button>
                <button type="button" className="secondary" onClick={() => setConfirmDelete(false)}>
                  {t("common.cancel")}
                </button>
              </>
            ) : (
              <button type="button" className="secondary" onClick={() => setConfirmDelete(true)}>
                {t("locations.delete")}
              </button>
            )}
          </div>
          <p className="muted small">{t("locations.deactivate_hint")}</p>
        </form>
      ) : null}
    </Card>
  );
}

/** A refusal; for unfinished operations the list of what blocks it (§ 4.4.1–4.4.2). */
function Refusal({ error }: { error: ApiError | null }) {
  const { t } = useTranslation();
  const blockers = error?.params?.blockers;
  if (!error || !Array.isArray(blockers)) return <ErrorMessage error={error} />;
  return (
    <div className="alert error" role="alert">
      {t(`errors.${error.code.toLowerCase()}`)}
      <ul>
        {blockers.map((code) => (
          <li key={String(code)}>{t(`locations.blocker_${String(code)}`)}</li>
        ))}
      </ul>
    </div>
  );
}
