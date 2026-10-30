import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { Holding, RightSpec } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";
import { partyLabel } from "./common";
import { TransferWizard } from "./OwnersPage";

function rightLabel(spec: RightSpec, t: (key: string) => string): string {
  const scope = spec.locations === "all" ? "" : ` (${spec.locations.length})`;
  if (spec.type === "set") return `${t(`sets.${spec.set ?? ""}`)}${scope}`;
  return `${t(`permissions.${(spec.permission ?? "").replaceAll(".", "_")}`)}${scope}`;
}

/** Shares this company holds in other companies (§ 3.4.1): the owners of each, the dedicated
 * position of the representative with its limit, the appointment or replacement of the
 * representative within the limit, and the transfer of the company's share. For the top
 * authority and employees with full rights. */
export function HoldingsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const holdings = useApi<{ holdings: Holding[] }>(
    ["holdings", company.public_id],
    `/c/${company.public_id}/holdings`,
  );
  return (
    <div className="page">
      <h1>{t("ownership.holdings")}</h1>
      <p className="muted">{t("ownership.holdings_hint")}</p>
      {holdings.isLoading ? <Loading /> : null}
      <ErrorMessage error={holdings.error as ApiError | null} />
      {holdings.data && !holdings.data.holdings.length ? (
        <p className="muted">{t("ownership.no_holdings")}</p>
      ) : null}
      {(holdings.data?.holdings ?? []).map((h) => (
        <HoldingCard key={h.seat.id} holding={h} />
      ))}
    </div>
  );
}

function HoldingCard({ holding }: { holding: Holding }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const seat = holding.seat;
  const [target, setTarget] = useState("");
  const [chosen, setChosen] = useState<boolean[]>(seat.limit.map(() => true));
  const assign = useCommand<{ target: string; rights: RightSpec[] }, unknown>(
    `/c/${company.public_id}/holdings/${seat.id}/representative`,
  );
  const remove = useCommand<Record<string, never>, unknown>(
    `/c/${company.public_id}/holdings/${seat.id}/representative/remove`,
  );
  const refresh = () => client.invalidateQueries({ queryKey: ["holdings", company.public_id] });

  return (
    <Card title={partyLabel(holding.company)}>
      <p>
        {t("ownership.our_share")}: <strong>{Number(holding.percent ?? 0)}%</strong>
        {holding.is_top_authority ? <> · <span className="badge ok">{t("ownership.top_authority")}</span></> : null}
      </p>
      <h3>{t("ownership.owners")}</h3>
      <ul className="small">
        {holding.owners.map((o) => (
          <li key={o.owner.public_id}>
            {partyLabel(o.owner)} — {Number(o.percent)}%
          </li>
        ))}
      </ul>
      <h3>{t("ownership.representative")}</h3>
      <p>
        {seat.holder
          ? `${seat.holder.first_name} ${seat.holder.last_name} · ${seat.holder.public_id ?? ""}`
          : t("ownership.vacant")}
      </p>
      {seat.holder?.rights.length ? (
        <p className="small muted">{seat.holder.rights.map((r) => rightLabel(r, t)).join(", ")}</p>
      ) : null}
      {seat.pending_invitation ? (
        <div className="alert warn">
          {t("ownership.representative_invited", { who: partyLabel(seat.pending_invitation.target) })}
        </div>
      ) : null}
      <h3>{t("ownership.limit")}</h3>
      {!seat.limit.length ? <p className="muted small">{t("ownership.limit_empty")}</p> : null}
      {seat.limit.map((spec, index) => (
        <label key={index} className="small" style={{ display: "block" }}>
          <input
            type="checkbox"
            checked={chosen[index] ?? false}
            onChange={() => setChosen(chosen.map((v, i) => (i === index ? !v : v)))}
          />{" "}
          {rightLabel(spec, t)}
        </label>
      ))}
      <Field label={t("ownership.representative_target")} hint={t("ownership.representative_target_hint")}>
        <input value={target} onChange={(e) => setTarget(e.target.value)} />
      </Field>
      <ErrorMessage error={assign.error ?? remove.error} />
      <div className="actions">
        <button
          type="button"
          disabled={!target.trim() || assign.pending}
          onClick={async () => {
            const rights = seat.limit.filter((_, i) => chosen[i]);
            if ((await assign.run({ target: target.trim(), rights })) !== null) {
              setTarget("");
              await refresh();
            }
          }}
        >
          {seat.holder ? t("ownership.representative_replace") : t("ownership.representative_assign")}
        </button>
        {seat.holder && !holding.is_top_authority ? (
          <button
            type="button"
            className="secondary"
            onClick={async () => {
              if ((await remove.run({})) !== null) await refresh();
            }}
          >
            {t("ownership.representative_remove")}
          </button>
        ) : null}
      </div>
      {holding.percent ? (
        <TransferWizard
          path={`/c/${company.public_id}/holdings/${seat.id}/transfers`}
          ownPercent={holding.percent}
          isTop={holding.is_top_authority}
          owners={holding.owners.map((o) => o.owner.public_id).filter((id) => id !== company.public_id)}
          invalidate={["holdings", company.public_id]}
        />
      ) : null}
    </Card>
  );
}
