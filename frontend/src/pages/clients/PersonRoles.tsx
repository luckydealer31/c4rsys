import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { useApi } from "../../api/queries";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Info, Modal } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { cardPath, type CardType } from "./cardLists";

type Role = { role: CardType; id: string; state: string; current: boolean; comment: string };
type Offered = { role: CardType; id: string; name: string; matched: string[] };
type Roles = {
  roles: Role[];
  hidden: number;
  can_add: CardType[];
  may_join: boolean;
  offered: Offered[];
  employee: { id: string } | null;
};

/** One person or company behind the cards of its roles (customer's decisions of 28.11.2026): a
 * client, an investor and a counterparty are one being. The line shows the roles — each opens
 * its card —, the same person as an employee, the comments of the other roles, and lets add a
 * role or say that another card is of the same being. */
export function PersonRoles({ cardType, id }: { cardType: CardType; id: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const client = useQueryClient();
  const key = ["person-roles", company.public_id, cardType, id];
  const found = useApi<Roles>(key, `/c/${company.public_id}/persons?card_type=${cardType}&card_id=${id}`);
  const add = useCommand<Record<string, unknown>, { role: CardType; id: string }>(`/c/${company.public_id}/persons/add-role`);
  const join = useCommand<Record<string, unknown>, Roles>(`/c/${company.public_id}/persons/join`);
  const leave = useCommand<Record<string, unknown>, Roles>(`/c/${company.public_id}/persons/leave`);
  const [joining, setJoining] = useState(false);
  const data = found.data;
  if (!data) return null;
  const here = { card_type: cardType, card_id: id };
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["person-roles", company.public_id] });
    await client.invalidateQueries({ queryKey: ["tag-links", company.public_id] });
  };
  const others = data.roles.filter((r) => !r.current);
  const several = data.roles.length + data.hidden > 1;
  return (
    <div className="person-roles" data-testid="person-roles">
      <span className="muted small">{t("persons.roles")}:</span>
      {data.roles.map((r) =>
        r.current ? (
          <span key={r.role} className="badge" aria-current="true">
            {t(`persons.role_${r.role}`)}
          </span>
        ) : (
          <Link key={r.role} className="badge link" to={companyPath(company, cardPath(r.role, r.id))}>
            {t(`persons.role_${r.role}`)}
            {r.state === "archived" ? ` (${t("persons.archived")})` : ""}
          </Link>
        ),
      )}
      {data.employee ? (
        <Link className="badge link" to={companyPath(company, `/clients/employees/${data.employee.id}`)}>
          {t("persons.role_employee")}
        </Link>
      ) : null}
      {data.hidden ? <span className="muted small">{t("persons.hidden", { count: data.hidden })}</span> : null}
      {data.can_add.map((role) => (
        <button
          key={role}
          type="button"
          className="secondary small-button"
          disabled={add.pending}
          onClick={async () => {
            const made = await add.run({ ...here, role });
            if (made !== null) {
              await refresh();
              navigate(companyPath(company, cardPath(made.role, made.id)));
            }
          }}
        >
          + {t(`persons.role_${role}`)}
        </button>
      ))}
      {data.may_join ? (
        <button type="button" className="secondary small-button" onClick={() => setJoining(true)}>
          {t("persons.join")}
        </button>
      ) : null}
      <Info text={t("persons.hint")} />
      <ErrorMessage error={add.error} />
      {others
        .filter((r) => r.comment)
        .map((r) => (
          <p key={r.role} className="small person-comment">
            <span className="muted">
              {t("persons.comment_of", { role: t(`persons.role_${r.role}`) })}:
            </span>{" "}
            {r.comment}
          </p>
        ))}
      {joining ? (
        <Modal title={t("persons.join")} onClose={() => setJoining(false)}>
          <p className="muted small">{t("persons.join_hint")}</p>
          {data.offered.length === 0 ? <p>{t("persons.join_none")}</p> : null}
          {data.offered.map((o) => (
            <div key={`${o.role}:${o.id}`} className="row-actions" style={{ justifyContent: "flex-start" }}>
              <span>
                {t(`persons.role_${o.role}`)}: <Link to={companyPath(company, cardPath(o.role, o.id))}>{o.name}</Link>{" "}
                <span className="muted small">({o.matched.map((m) => t(`clients.matched_${m}`)).join(", ")})</span>
              </span>
              <button
                type="button"
                className="secondary small-button"
                disabled={join.pending}
                onClick={async () => {
                  if ((await join.run({ ...here, other_type: o.role, other_id: o.id })) !== null) {
                    setJoining(false);
                    await refresh();
                  }
                }}
              >
                {t("persons.join_this")}
              </button>
            </div>
          ))}
          <ErrorMessage error={join.error ?? leave.error} />
          {several ? (
            <div className="actions">
              <button
                type="button"
                className="secondary small-button"
                disabled={leave.pending}
                onClick={async () => {
                  if ((await leave.run(here)) !== null) {
                    setJoining(false);
                    await refresh();
                  }
                }}
              >
                {t("persons.leave")}
              </button>
            </div>
          ) : null}
        </Modal>
      ) : null}
    </div>
  );
}

/** The cards of an employee as a client, an investor or a counterparty: he is one person. */
export function EmployeeCards({ employmentId }: { employmentId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const found = useApi<{ cards: { role: CardType; id: string; name: string }[] }>(
    ["person-of-employee", company.public_id, employmentId],
    `/c/${company.public_id}/persons/of-employee/${employmentId}`,
  );
  const cards = found.data?.cards ?? [];
  if (!cards.length) return null;
  return (
    <p className="small" data-testid="employee-cards">
      <span className="muted">{t("persons.also")}: </span>
      {cards.map((c, i) => (
        <span key={`${c.role}:${c.id}`}>
          {i ? ", " : ""}
          <Link to={companyPath(company, cardPath(c.role, c.id))}>{t(`persons.role_${c.role}`)}</Link>
        </span>
      ))}
    </p>
  );
}
