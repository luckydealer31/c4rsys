import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { ContextType, Location } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { timeZones } from "../../time";

const CONTEXT_TYPES: ContextType[] = ["rental", "service", "warehouse"];

export function useLocations() {
  const company = useCompany();
  return useApi<{ locations: Location[] }>(
    ["locations", company.public_id],
    `/c/${company.public_id}/locations`,
  );
}

/** Locations with their contexts, state and schedule (§ 4.1–4.4, § 22.1.5–22.1.6). */
export function LocationsPage() {
  const { t } = useTranslation();
  return (
    <div className="page">
      <h1>{t("settings.locations")}</h1>
      <LocationsEditor />
    </div>
  );
}

export function LocationsEditor() {
  const { t } = useTranslation();
  const can = useCan();
  const locations = useLocations();
  return (
    <>
      {locations.isLoading ? <Loading /> : null}
      {(locations.data?.locations ?? []).map((location) => (
        <LocationCard key={location.id} location={location} editable={can("company.settings")} />
      ))}
      {locations.data?.locations.length === 0 ? (
        <p className="muted">{t("locations.none")}</p>
      ) : null}
      {can("company.settings") ? <CreateLocation /> : null}
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
  };
}

function CreateLocation() {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState(company.timezone ?? "");
  const [address, setAddress] = useState("");
  const create = useCommand<Record<string, string>, { location: Location }>(
    `/c/${company.public_id}/locations`,
  );
  const addContext = useCommand<{ id: string; type: string }, unknown>(
    (body) => `/c/${company.public_id}/locations/${body.id}/contexts`,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, string> = { name, address };
    if (timezone) body.timezone = timezone;
    const result = await create.run(body);
    if (result) {
      // At the initial setup one rental context is assigned (§ 4.2.3, STAGE1_SCREENS step 2).
      await addContext.run({ id: result.location.id, type: "rental" });
      setName("");
      setAddress("");
      await refresh();
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
            <select value={timezone} onChange={(e) => setTimezone(e.target.value)}>
              <option value="">—</option>
              {timeZones().map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("locations.address")}>
            <input value={address} onChange={(e) => setAddress(e.target.value)} />
          </Field>
        </div>
        <p className="muted small">{t("locations.rental_context_added")}</p>
        <ErrorMessage error={create.error ?? addContext.error} />
        <div className="actions">
          <button type="submit" disabled={!name.trim() || create.pending}>
            {t("locations.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}

function LocationCard({ location, editable }: { location: Location; editable: boolean }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefresh();
  const [form, setForm] = useState({
    name: location.name,
    timezone: location.timezone ?? "",
    address: location.address,
  });
  const update = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/locations/${location.id}`,
    "PATCH",
  );
  const addContext = useCommand<{ type: string }, unknown>(
    `/c/${company.public_id}/locations/${location.id}/contexts`,
  );
  const missingTypes = CONTEXT_TYPES.filter((type) => !location.contexts.some((c) => c.type === type));

  async function save(event: FormEvent) {
    event.preventDefault();
    const changes: Record<string, unknown> = { expected_version: location.version };
    if (form.name !== location.name) changes.name = form.name;
    if (form.timezone && form.timezone !== location.timezone) changes.timezone = form.timezone;
    if (form.address !== location.address) changes.address = form.address;
    if ((await update.run(changes)) !== null) await refresh();
  }

  async function add(type: string) {
    if ((await addContext.run({ type })) !== null) await refresh();
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
        {t("locations.contexts")}:{" "}
        {location.contexts.length === 0
          ? t("locations.no_contexts")
          : location.contexts
              .map(
                (c) =>
                  `${t(`contexts.${c.type}`)} — ${c.ready ? t("locations.ready") : t("locations.not_ready")}`,
              )
              .join("; ")}
        {" · "}
        {t("locations.schedule")}: {isAllDay ? t("locations.schedule_24_7") : t("locations.schedule_custom")}
      </p>
      {editable ? (
        <form className="stack" onSubmit={save}>
          <div className="row">
            <Field label={t("locations.name")}>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label={t("companies.timezone")}>
              <select
                value={form.timezone}
                onChange={(e) => setForm({ ...form, timezone: e.target.value })}
              >
                <option value="">—</option>
                {timeZones().map((zone) => (
                  <option key={zone} value={zone}>
                    {zone}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("locations.address")}>
              <input
                value={form.address}
                onChange={(e) => setForm({ ...form, address: e.target.value })}
              />
            </Field>
          </div>
          <ErrorMessage error={update.error ?? addContext.error} />
          <div className="actions">
            <button type="submit" disabled={update.pending}>
              {t("common.save")}
            </button>
            {missingTypes.map((type) => (
              <button key={type} type="button" className="secondary" onClick={() => void add(type)}>
                {t("locations.add_context", { context: t(`contexts.${type}`) })}
              </button>
            ))}
          </div>
        </form>
      ) : null}
    </Card>
  );
}
