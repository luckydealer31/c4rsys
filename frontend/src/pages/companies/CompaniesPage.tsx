import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { api } from "../../api/client";
import type { Company, CompanySummary, User } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Avatar, ImagePicker } from "../../components/images";
import { InvitationsSection } from "../account/InvitationsPage";
import { LinkRequests } from "../account/LinkRequests";
import { OwnerRequests } from "../account/OwnerRequests";
import { OwnershipRequestsSection } from "../account/OwnershipRequests";
import { TimeZoneSelect } from "../../components/TimeZoneSelect";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { deviceTimeZone } from "../../time";

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
      <InvitationsSection user={user} />
      {/* § 6.3.8: a person answers in the personal area; it moves to the cabinet in К6. */}
      <LinkRequests base="/profile-links" title={t("links.my_title")} hideEmpty />
      <OwnerRequests base="/owner-requests" title={t("owner_requests.my_title")} hideEmpty />
      <OwnershipRequestsSection />
      <Card>
        {companies.isLoading ? <Loading /> : null}
        {companies.data?.length === 0 ? <p className="muted">{t("companies.none")}</p> : null}
        {(companies.data ?? []).map((company) => (
          <Link
            key={company.public_id}
            className="list-link company-link"
            to={`/c/${company.public_id}`}
          >
            <Avatar file={company.logo} name={company.display_name} size={36} square />
            <span>
              <strong>{company.display_name}</strong>{" "}
              <span className="muted small">{company.public_id}</span>
            </span>
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
  const [logo, setLogo] = useState<string | null>(null);
  // The user's time zone is proposed and must be confirmed or changed (§ 22.1.4).
  const [timezone, setTimezone] = useState(user.timezone || deviceTimeZone());
  const create = useCommand<
    { display_name: string; timezone: string; logo?: string },
    { company: Company }
  >("/companies");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await create.run({
      display_name: name,
      timezone,
      ...(logo ? { logo } : {}),
    });
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
            <TimeZoneSelect value={timezone} onChange={setTimezone} />
          </Field>
        </div>
        <Field label={t("companies.logo")} hint={t("companies.logo_hint")}>
          <ImagePicker
            purpose="company_logo"
            value={logo}
            name={name}
            square
            onChange={setLogo}
          />
        </Field>
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
