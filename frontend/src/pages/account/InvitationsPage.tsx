import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { api } from "../../api/client";
import type { Invitation, User } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Loading } from "../../components/ui";

/** Invitations to work in a company (§ 5.1.3). Before accepting, the user sees what data the
 * company gets: first and last name always, middle name and email by choice (§ 5.1.7). */
export function InvitationsPage({ user }: { user: User }) {
  const { t } = useTranslation();
  const invitations = useQuery({
    queryKey: ["invitations"],
    queryFn: async () => (await api<{ invitations: Invitation[] }>("/invitations")).invitations,
  });
  return (
    <div className="page">
      <h1>{t("invitations.title")}</h1>
      {invitations.isLoading ? <Loading /> : null}
      {invitations.data?.length === 0 ? <p className="muted">{t("invitations.none")}</p> : null}
      {!user.has_full_name ? (
        <div className="alert warn">
          {t("profile.name_required")} <Link to="/profile">{t("companies.to_profile")}</Link>
        </div>
      ) : null}
      {(invitations.data ?? []).map((invitation) => (
        <InvitationCard key={invitation.id} invitation={invitation} />
      ))}
    </div>
  );
}

function InvitationCard({ invitation }: { invitation: Invitation }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [shareMiddle, setShareMiddle] = useState(false);
  const [shareEmail, setShareEmail] = useState(false);
  const accept = useCommand<Record<string, boolean>, unknown>(
    `/invitations/${invitation.id}/accept`,
  );
  const decline = useCommand<Record<string, never>, unknown>(
    `/invitations/${invitation.id}/decline`,
  );

  async function done() {
    await client.invalidateQueries({ queryKey: ["invitations"] });
    await client.invalidateQueries({ queryKey: ["companies"] });
  }

  return (
    <Card title={invitation.company?.display_name ?? ""}>
      <p>
        {t("employees.position")}:{" "}
        {invitation.position.name ||
          (invitation.position.code ? t(`positions.${invitation.position.code}`) : "—")}
      </p>
      <p className="small">{t("invitations.data_required")}</p>
      <label className="small">
        <input type="checkbox" checked={shareMiddle} onChange={() => setShareMiddle(!shareMiddle)} />{" "}
        {t("invitations.share_middle_name")}
      </label>
      <br />
      <label className="small">
        <input type="checkbox" checked={shareEmail} onChange={() => setShareEmail(!shareEmail)} />{" "}
        {t("invitations.share_email")}
      </label>
      <p className="muted small">{t("invitations.consent")}</p>
      <ErrorMessage error={accept.error ?? decline.error} />
      <div className="actions">
        <button
          type="button"
          onClick={async () => {
            if (
              (await accept.run({ share_middle_name: shareMiddle, share_email: shareEmail })) !==
              null
            )
              await done();
          }}
        >
          {t("invitations.accept")}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={async () => {
            if ((await decline.run({})) !== null) await done();
          }}
        >
          {t("invitations.decline")}
        </button>
      </div>
    </Card>
  );
}
