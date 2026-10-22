import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { api } from "../../api/client";
import type { Company, CompanySummary, User } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { deviceTimeZone, timeZones } from "../../time";

/** Companies of the user (§ 2.1.8) and creation of a new one (§ 3.1.1, § 22.1.4). */
export function CompaniesPage({ user }: { user: User }) {
  const { t } = useTranslation();
  const companies = useQuery({
    queryKey: ["companies"],
    queryFn: async () => (await api<{ companies: CompanySummary[] }>("/companies")).companies,
  });

  return (
    <div className="page">
      <h1>{t("companies.title")}</h1>
      <Card>
        {companies.isLoading ? <Loading /> : null}
        {companies.data?.length === 0 ? <p className="muted">{t("companies.none")}</p> : null}
        {(companies.data ?? []).map((company) => (
          <Link key={company.public_id} className="list-link" to={`/c/${company.public_id}`}>
            <strong>{company.display_name}</strong>{" "}
            <span className="muted small">{company.public_id}</span>
          </Link>
        ))}
      </Card>
      <CreateCompany user={user} />
    </div>
  );
}

function CreateCompany({ user }: { user: User }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [name, setName] = useState("");
  // The user's time zone is proposed and must be confirmed or changed (§ 22.1.4).
  const [timezone, setTimezone] = useState(deviceTimeZone());
  const create = useCommand<{ display_name: string; timezone: string }, { company: Company }>(
    "/companies",
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await create.run({ display_name: name, timezone });
    if (result) {
      void client.invalidateQueries({ queryKey: ["companies"] });
      navigate(`/c/${result.company.public_id}/setup`);
    }
  }

  return (
    <Card title={t("companies.create_title")}>
      {!user.has_full_name ? (
        <div className="alert warn">
          {t("companies.name_required")} <Link to="/profile">{t("companies.to_profile")}</Link>
        </div>
      ) : null}
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("companies.display_name")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("companies.timezone")} hint={t("companies.timezone_hint")}>
            <select value={timezone} onChange={(e) => setTimezone(e.target.value)}>
              {timeZones().map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={!user.has_full_name || !name.trim() || create.pending}>
            {t("companies.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}
