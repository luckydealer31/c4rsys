import { useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { useApi } from "../../api/queries";
import type { Readiness } from "../../api/types";
import type { ApiError } from "../../api/client";
import { useCommand } from "../../api/useCommand";
import { contextAvailable } from "../../contexts";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { CashAccountsEditor } from "../finance/FinancePage";
import { CompanyProfileForm } from "../settings/CompanyProfilePage";
import { LocationsEditor } from "../settings/LocationsPage";

const STEPS = ["profile", "location", "finance", "tariff", "vehicle", "employees", "check"] as const;
type Step = (typeof STEPS)[number];

/** Shortened setup wizard of stage 1 (STAGE1_SCREENS, § 22.1). Steps use the ordinary screens;
 * any step can be skipped and resumed later (§ 22.1.2). */
export function SetupPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const readiness = useApi<Readiness>(
    ["readiness", company.public_id],
    `/c/${company.public_id}/readiness`,
  );
  const [step, setStep] = useState<Step>("profile");

  if (readiness.error) {
    return (
      <div className="page">
        <ErrorMessage error={readiness.error as ApiError} />
      </div>
    );
  }
  if (!readiness.data) return <div className="page"><Loading /></div>;
  const r = readiness.data;
  const rentalContexts = r.locations.flatMap((l) => l.contexts.filter((c) => c.type === "rental"));
  const done: Record<Step, boolean> = {
    profile: Boolean(company.timezone),
    location: r.locations.some((l) => l.missing.length === 0 && l.contexts.some((c) => c.type === "rental")),
    finance: Boolean(company.main_currency),
    tariff: rentalContexts.some((c) => !c.missing.includes("no_tariff")),
    vehicle: rentalContexts.some(
      (c) => !c.missing.includes("no_vehicle") && !c.missing.includes("no_vehicle_with_tariff"),
    ),
    employees: true,
    check: r.first_operation_ready,
  };
  const refresh = () => client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
  const index = STEPS.indexOf(step);

  const content: Record<Step, ReactNode> = {
    profile: <CompanyProfileForm onSaved={refresh} />,
    location: <LocationsEditor createOpen />,
    finance: (
      <>
        <CompanyProfileForm onSaved={refresh} />
        <p className="muted small">{t("setup.cash_optional")}</p>
        <CashAccountsEditor />
      </>
    ),
    tariff: (
      <Card>
        <p>{t("setup.tariff_text")}</p>
        <Link className="button" to={companyPath(company, "/settings/tariffs")}>
          {t("settings.tariffs")}
        </Link>
      </Card>
    ),
    vehicle: (
      <Card>
        <p>{t("setup.vehicle_text")}</p>
        <Link className="button" to={companyPath(company, "/vehicles")}>
          {t("nav.garage")}
        </Link>
      </Card>
    ),
    employees: (
      <Card>
        <p>{t("setup.employees_text")}</p>
        <Link className="button secondary" to={companyPath(company, "/clients?tab=employees")}>
          {t("settings.employees")}
        </Link>
      </Card>
    ),
    check: <ReadinessCheck readiness={r} />,
  };

  return (
    <div className="page">
      <h1>{t("nav.setup")}</h1>
      <nav className="nav" style={{ marginBottom: 16 }}>
        {STEPS.map((key, i) => (
          <a
            key={key}
            href="#"
            className={key === step ? "active" : ""}
            onClick={(event) => {
              event.preventDefault();
              void refresh();
              setStep(key);
            }}
          >
            {i + 1}. {t(`setup.step_${key}`)}{" "}
            <span className={`badge ${done[key] ? "ok" : "warn"}`}>
              {done[key] ? "✓" : "…"}
            </span>
          </a>
        ))}
      </nav>
      <h2>{t(`setup.step_${step}`)}</h2>
      <p className="muted">{t(`setup.hint_${step}`)}</p>
      {content[step]}
      <div className="actions">
        {index > 0 ? (
          <button type="button" className="secondary" onClick={() => setStep(STEPS[index - 1] ?? step)}>
            {t("setup.back")}
          </button>
        ) : null}
        {step === "check" ? <CompleteSetup ready={r.first_operation_ready} /> : null}
        {index < STEPS.length - 1 ? (
          <button
            type="button"
            onClick={() => {
              void refresh();
              setStep(STEPS[index + 1] ?? step);
            }}
          >
            {done[step] ? t("setup.next") : t("setup.skip")}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** «Complete»: allowed when at least one context is ready (§ 22.1.12); from then «Orders»
 * appear in the menu (decision of 24.10.2026). */
function CompleteSetup({ ready }: { ready: boolean }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const navigate = useNavigate();
  const complete = useCommand<Record<string, never>, { setup_completed_at: string }>(
    `/c/${company.public_id}/setup/complete`,
  );
  if (company.setup_completed_at) {
    return <span className="badge ok">{t("setup.completed")}</span>;
  }
  return (
    <>
      <button
        type="button"
        disabled={!ready || complete.pending}
        onClick={async () => {
          if ((await complete.run({})) !== null) {
            await client.invalidateQueries({ queryKey: ["company", company.public_id] });
            navigate(companyPath(company, "/orders"));
          }
        }}
      >
        {t("setup.complete")}
      </button>
      {!ready ? <span className="muted small">{t("setup.complete_needs_ready")}</span> : null}
      <ErrorMessage error={complete.error} />
    </>
  );
}

/** «Ready / Needs setup» for the company, each location and context, with links (§ 22.1.12). */
function ReadinessCheck({ readiness }: { readiness: Readiness }) {
  const { t } = useTranslation();
  const company = useCompany();
  const links: Record<string, string> = {
    timezone: "/settings/company",
    main_currency: "/settings/company",
    display_name: "/settings/company",
    no_timezone: "/company",
    no_context: "/company",
    no_tariff: "/settings/tariffs",
    no_vehicle: "/vehicles",
    no_vehicle_with_tariff: "/settings/tariffs",
  };
  const missingCompany = Object.entries(readiness.company.items).filter(([, ok]) => !ok);
  return (
    <>
      <Card title={t("setup.company")}>
        <Status ready={readiness.company.ready} />
        <ul>
          {missingCompany.map(([key]) => (
            <li key={key}>
              <Link to={companyPath(company, links[key] ?? "/settings")}>{t(`setup.missing_${key}`)}</Link>
            </li>
          ))}
        </ul>
      </Card>
      {readiness.locations.length === 0 ? (
        <Card>
          <Link to={companyPath(company, "/company")}>{t("setup.missing_location")}</Link>
        </Card>
      ) : null}
      {readiness.locations.map((location) => (
        <Card key={location.id} title={location.name}>
          {location.missing.map((code) => (
            <p key={code}>
              <Link to={companyPath(company, links[code] ?? "/settings")}>{t(`setup.missing_${code}`)}</Link>
            </p>
          ))}
          {location.contexts.filter((c) => contextAvailable(c.type)).map((context) => (
            <div key={context.id}>
              <strong>{t(`contexts.${context.type}`)}</strong> <Status ready={context.ready} />
              <ul>
                {context.missing.map((code) => (
                  <li key={code}>
                    {links[code] ? (
                      <Link to={companyPath(company, links[code])}>{t(`setup.missing_${code}`)}</Link>
                    ) : (
                      t(`setup.missing_${code}`)
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </Card>
      ))}
      {readiness.first_operation_ready ? (
        <div className="alert ok">
          {t("setup.ready_for_first")}{" "}
          <Link to={companyPath(company, "/rentals")}>{t("nav.rentals")}</Link>
        </div>
      ) : null}
    </>
  );
}

function Status({ ready }: { ready: boolean }) {
  const { t } = useTranslation();
  return (
    <span className={`badge ${ready ? "ok" : "warn"}`}>
      {ready ? t("setup.ready") : t("setup.needs_setup")}
    </span>
  );
}
