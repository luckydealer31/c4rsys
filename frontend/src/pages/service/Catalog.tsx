import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { Location, ServicePost, ServiceRate, ServiceWork } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { useLocations } from "../settings/LocationsPage";
import {
  serviceLocations,
  useServicePosts,
  useServiceRates,
  useServiceWorks,
  useSystemWorks,
  workTerms,
} from "./hooks";

/** Catalog of service works, hourly rates and posts (§ 16.1.1–16.1.5, § 16.3; decisions С-1,
 * С-2). */
export function ServiceCatalogPage() {
  const { t, i18n } = useTranslation();
  const lang = i18n.language.slice(0, 2);
  const company = useCompany();
  const can = useCan();
  const works = useServiceWorks();
  const locations = useLocations();
  const [editing, setEditing] = useState<ServiceWork | null>(null);
  const places = serviceLocations(locations.data?.locations);
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/service")}>← {t("nav.service")}</Link>
      </p>
      <h1>{t("service.catalog")}</h1>
      <Card>
        {works.isLoading ? <Loading /> : null}
        {works.data?.works.length === 0 ? <p className="muted">{t("service.no_works")}</p> : null}
        {(works.data?.works ?? []).map((work) => (
          <div key={work.id} className="list-link">
            <strong>{work.name}</strong>{" "}
            {work.state === "archived" ? <span className="badge">{t("tariffs.archived")}</span> : null}
            <div className="muted small">
              {workTerms(work, t)}
              {work.system_names ? ` · ${t("service.linked_to", { name: work.system_names[lang] ?? work.system_work })}` : ""}
            </div>
            {can("service.catalog") ? (
              <button type="button" className="secondary small" onClick={() => setEditing(work)}>
                {t("common.edit")}
              </button>
            ) : null}
          </div>
        ))}
      </Card>
      {can("service.catalog") ? (
        <WorkForm key={editing?.id ?? "new"} work={editing ?? undefined} onDone={() => setEditing(null)} />
      ) : null}
      {can("service.catalog") ? <Rates places={places} /> : null}
      {can("company.settings") ? <Posts places={places} /> : null}
    </div>
  );
}

/** A work chosen from the system reference is linked at once (§ 16.1.1); a fixed price or norm
 * hours × the rate; price and hours may stay empty — not known yet (§ 16.1.5). */
function WorkForm({ work, onDone }: { work?: ServiceWork; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const system = useSystemWorks();
  const [name, setName] = useState(work?.name ?? "");
  const [systemWork, setSystemWork] = useState(work?.system_work ?? "");
  const [pricing, setPricing] = useState<ServiceWork["pricing"]>(work?.pricing ?? "fixed");
  const [priceValue, setPrice] = useState(work?.price ? String(Number(work.price)) : "");
  const [hours, setHours] = useState(work?.hours ? String(Number(work.hours)) : "");
  const [rate, setRate] = useState(work?.rate ? String(Number(work.rate)) : "");
  const save = useCommand<Record<string, unknown>, { work: ServiceWork }>(
    work ? `/c/${company.public_id}/service/works/${work.id}` : `/c/${company.public_id}/service/works`,
    work ? "PATCH" : "POST",
  );
  const archive = useCommand<Record<string, unknown>, { work: ServiceWork }>(
    `/c/${company.public_id}/service/works/${work?.id ?? ""}/${work?.state === "archived" ? "restore" : "archive"}`,
  );
  const lang = i18n.language.slice(0, 2);
  const number = (value: string) => value.trim().replace(",", ".") || null;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["service-works", company.public_id] });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = {
      name,
      system_work: systemWork || null,
      pricing,
      price: pricing === "fixed" ? number(priceValue) : null,
      hours: pricing === "hours" ? number(hours) : null,
      rate: pricing === "hours" ? number(rate) : null,
    };
    if (work) body.expected_version = work.version;
    const result = await save.run(body);
    if (result) {
      await refresh();
      if (!work) {
        setName("");
        setSystemWork("");
        setPrice("");
        setHours("");
        setRate("");
      }
      onDone();
    }
  }

  function pick(code: string) {
    setSystemWork(code);
    const found = system.data?.system_works.find((w) => w.code === code);
    if (found && !name.trim()) setName(found.names[lang] ?? found.names.en ?? "");
  }

  return (
    <Card title={work ? t("service.edit_work") : t("service.new_work")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("service.system_work")} hint={t("service.system_work_hint")}>
            <select value={systemWork} onChange={(e) => pick(e.target.value)}>
              <option value="">{t("service.own_work")}</option>
              {(system.data?.system_works ?? []).map((w) => (
                <option key={w.code} value={w.code}>
                  {w.names[lang] ?? w.names.en}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("tariffs.name")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
        </div>
        <div className="row">
          <Field label={t("service.pricing")}>
            <select value={pricing} onChange={(e) => setPricing(e.target.value as ServiceWork["pricing"])}>
              <option value="fixed">{t("service.pricing_fixed")}</option>
              <option value="hours">{t("service.pricing_hours")}</option>
            </select>
          </Field>
          {pricing === "fixed" ? (
            <Field label={`${t("service.price")}, ${company.main_currency ?? ""}`} hint={t("service.price_later")}>
              <input inputMode="decimal" value={priceValue} onChange={(e) => setPrice(e.target.value)} />
            </Field>
          ) : (
            <>
              <Field label={t("service.hours")} hint={t("service.price_later")}>
                <input inputMode="decimal" value={hours} onChange={(e) => setHours(e.target.value)} />
              </Field>
              <Field label={`${t("service.own_rate")}, ${company.main_currency ?? ""}`} hint={t("service.own_rate_hint")}>
                <input inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
              </Field>
            </>
          )}
        </div>
        <ErrorMessage error={save.error ?? archive.error} />
        <div className="actions">
          <button type="submit" disabled={save.pending || !name.trim()}>
            {work ? t("common.save") : t("service.add_work")}
          </button>
          {work ? (
            <>
              <button
                type="button"
                className="secondary"
                disabled={archive.pending}
                onClick={() =>
                  void archive.run({ expected_version: work.version }).then(async (r) => {
                    if (r) {
                      await refresh();
                      onDone();
                    }
                  })
                }
              >
                {work.state === "archived" ? t("service.restore") : t("service.archive")}
              </button>
              <button type="button" className="secondary" onClick={onDone}>
                {t("common.cancel")}
              </button>
            </>
          ) : null}
        </div>
      </form>
    </Card>
  );
}

/** The hourly rate: of a work → of the location → of the company (§ 16.1.2). */
function Rates({ places }: { places: Location[] }) {
  const { t } = useTranslation();
  const rates = useServiceRates();
  const of = (locationId: string | null) => rates.data?.rates.find((r) => r.location_id === locationId);
  return (
    <Card title={t("service.rates")}>
      <p className="muted small">{t("service.rates_hint")}</p>
      {rates.isLoading ? <Loading /> : null}
      {rates.data ? (
        <>
          <RateRow label={t("service.company_rate")} locationId={null} current={of(null)} />
          {places.map((l) => (
            <RateRow key={l.id} label={l.name} locationId={l.id} current={of(l.id)} />
          ))}
        </>
      ) : null}
    </Card>
  );
}

function RateRow({ label, locationId, current }: { label: string; locationId: string | null; current?: ServiceRate }) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const [value, setValue] = useState(current ? String(Number(current.rate)) : "");
  const save = useCommand<Record<string, unknown>, { rates: ServiceRate[] }>(`/c/${company.public_id}/service/rates`, "PUT");
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await save.run({ location_id: locationId, rate: value.trim().replace(",", ".") || null });
    if (result) await queryClient.invalidateQueries({ queryKey: ["service-rates", company.public_id] });
  }
  return (
    <form className="row" onSubmit={submit}>
      <Field label={`${label}, ${company.main_currency ?? ""}`}>
        <input inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />
      </Field>
      <div className="actions">
        <button type="submit" className="secondary" disabled={save.pending}>
          {t("common.save")}
        </button>
      </div>
      <ErrorMessage error={save.error} />
    </form>
  );
}

/** Posts are optional; the general zone exists by itself (§ 16.3.1–16.3.2). */
function Posts({ places }: { places: Location[] }) {
  const { t } = useTranslation();
  const posts = useServicePosts();
  return (
    <Card title={t("service.posts")}>
      <p className="muted small">{t("service.posts_hint")}</p>
      {posts.isLoading ? <Loading /> : null}
      {places.map((l) => (
        <div key={l.id} style={{ marginBottom: 12 }}>
          <h3>{l.name}</h3>
          <ul>
            {(posts.data?.posts ?? [])
              .filter((p) => p.location_id === l.id)
              .map((p) => (
                <PostRow key={p.id} post={p} />
              ))}
          </ul>
          <NewPost locationId={l.id} />
        </div>
      ))}
    </Card>
  );
}

function PostRow({ post }: { post: ServicePost }) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const change = useCommand<Record<string, unknown>, { post: ServicePost }>(
    `/c/${company.public_id}/service/posts/${post.id}`,
    "PATCH",
  );
  const archived = post.state === "archived";
  return (
    <li>
      {post.name} {archived ? <span className="badge">{t("tariffs.archived")}</span> : null}{" "}
      <button
        type="button"
        className="secondary small"
        disabled={change.pending}
        onClick={() =>
          void change.run({ archived: !archived, expected_version: post.version }).then(async (r) => {
            if (r) await queryClient.invalidateQueries({ queryKey: ["service-posts", company.public_id] });
          })
        }
      >
        {archived ? t("service.restore") : t("service.archive")}
      </button>
      <ErrorMessage error={change.error} />
    </li>
  );
}

function NewPost({ locationId }: { locationId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const create = useCommand<Record<string, unknown>, { post: ServicePost }>(`/c/${company.public_id}/service/posts`);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await create.run({ location_id: locationId, name });
    if (result) {
      setName("");
      await queryClient.invalidateQueries({ queryKey: ["service-posts", company.public_id] });
    }
  }
  return (
    <form className="row" onSubmit={submit}>
      <Field label={t("service.post_name")}>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <div className="actions">
        <button type="submit" className="secondary" disabled={create.pending || !name.trim()}>
          {t("service.add_post")}
        </button>
      </div>
      <ErrorMessage error={create.error} />
    </form>
  );
}
