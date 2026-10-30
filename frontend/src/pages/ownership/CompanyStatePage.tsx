import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { CompanyState, StateRequest } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { CodeConfirm, partyLabel, useMoment } from "./common";

/** Deactivation and restoration of the company (§ 3.6): blockers, the owners' approval by the
 * policy and the decisions counted for the current owners and shares. */
export function CompanyStatePage() {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const moment = useMoment();
  const key = ["company-state", company.public_id];
  const state = useApi<CompanyState>(key, `/c/${company.public_id}/state`);
  const deactivate = useCommand<{ flow: string; code: string }, CompanyState>(
    `/c/${company.public_id}/deactivation`,
  );
  const restore = useCommand<Record<string, never>, CompanyState>(
    `/c/${company.public_id}/restoration`,
  );

  async function refresh() {
    await client.invalidateQueries({ queryKey: key });
    await client.invalidateQueries({ queryKey: ["company", company.public_id] });
  }

  if (state.isLoading) return <div className="page"><Loading /></div>;
  const data = state.data;
  return (
    <div className="page">
      <h1>{t("company_state.title")}</h1>
      <ErrorMessage error={state.error as ApiError | null} />
      {data ? (
        <>
          <Card>
            <p>
              {t("company_state.state")}:{" "}
              <span className={`badge ${data.deactivated_at ? "warn" : "ok"}`}>
                {data.deactivated_at ? t("company_state.deactivated") : t("company_state.active")}
              </span>
              {data.deactivated_at ? <span className="muted small"> · {moment(data.deactivated_at)}</span> : null}
            </p>
            <p className="small">
              {t("company_state.policy")}: {t(`ownership.mode_${data.policy.mode}`)}
              {data.policy.percent ? ` ${Number(data.policy.percent)}%` : ""}
              {data.policy.configured === "unset" ? ` (${t("company_state.policy_default")})` : ""}
            </p>
            <p className="muted small">{t(data.deactivated_at ? "company_state.after_hint" : "company_state.before_hint")}</p>
          </Card>
          {!data.deactivated_at && data.blockers.length ? (
            <Card title={t("company_state.blockers")}>
              <ul>
                {data.blockers.map((b) => (
                  <li key={b}>{t(`company_state.blocker_${b}`)}</li>
                ))}
              </ul>
            </Card>
          ) : null}
          {data.request ? (
            <RequestCard request={data.request} canManage={data.can_manage} onChange={refresh} />
          ) : data.can_manage ? (
            <Card title={data.deactivated_at ? t("company_state.restore") : t("company_state.deactivate")}>
              {data.deactivated_at ? (
                <>
                  <ErrorMessage error={restore.error} />
                  <div className="actions">
                    <button
                      type="button"
                      disabled={restore.pending}
                      onClick={async () => {
                        if ((await restore.run({})) !== null) await refresh();
                      }}
                    >
                      {t("company_state.restore")}
                    </button>
                  </div>
                </>
              ) : (
                <CodeConfirm
                  action="deactivate_company"
                  label={t("company_state.deactivate")}
                  danger
                  disabled={data.blockers.length > 0}
                  error={deactivate.error}
                  onConfirm={async (flow, code) => {
                    const result = await deactivate.run({ flow, code });
                    if (result) await refresh();
                    return result !== null;
                  }}
                />
              )}
            </Card>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function RequestCard({
  request,
  canManage,
  onChange,
}: {
  request: StateRequest;
  canManage: boolean;
  onChange: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const moment = useMoment();
  const cancel = useCommand<Record<string, never>, unknown>(
    `/c/${company.public_id}/state-requests/${request.id}/cancel`,
  );
  const complete = useCommand<Record<string, never>, unknown>(
    `/c/${company.public_id}/state-requests/${request.id}/complete`,
  );
  return (
    <Card title={t(`company_state.request_${request.kind}`)}>
      <p className="small">
        {t("company_state.started", { at: moment(request.created_at) })} ·{" "}
        {t(`ownership.mode_${request.mode}`)}
        {request.percent ? ` ${Number(request.percent)}%` : ""}
      </p>
      <table>
        <thead>
          <tr>
            <th>{t("ownership.owner")}</th>
            <th>{t("ownership.percent")}</th>
            <th>{t("company_state.decision")}</th>
          </tr>
        </thead>
        <tbody>
          {request.owners.map((o) => (
            <tr key={o.owner.public_id}>
              <td>{partyLabel(o.owner)}</td>
              <td>{Number(o.percent)}%</td>
              <td>{o.decision ? t(`company_state.decision_${o.decision}`) : t("company_state.waiting")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {request.history.some((h) => !h.counted) ? (
        <p className="muted small">{t("company_state.stale_hint")}</p>
      ) : null}
      <p className="muted small">{t("company_state.answer_hint")}</p>
      <ErrorMessage error={cancel.error ?? complete.error} />
      {canManage ? (
        <div className="actions">
          {request.satisfied ? (
            <button
              type="button"
              onClick={async () => {
                if ((await complete.run({})) !== null) await onChange();
              }}
            >
              {t("company_state.complete")}
            </button>
          ) : null}
          <button
            type="button"
            className="secondary"
            onClick={async () => {
              if ((await cancel.run({})) !== null) await onChange();
            }}
          >
            {t("company_state.cancel")}
          </button>
        </div>
      ) : null}
    </Card>
  );
}
