import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { SUPPORTED_LANGUAGES } from "./i18n";

type Health = { status: string; database: string };

async function fetchHealth(): Promise<Health> {
  const response = await fetch("/api/health");
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return (await response.json()) as Health;
}

export function App() {
  const { t, i18n } = useTranslation();
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth });

  let statusText = t("health.checking");
  if (health.isError) statusText = t("health.unavailable");
  if (health.data) statusText = t("health.ok");

  return (
    <main>
      <h1>c4rsys</h1>
      <p>{statusText}</p>
      <label>
        {t("settings.language")}{" "}
        <select
          value={i18n.resolvedLanguage}
          onChange={(event) => void i18n.changeLanguage(event.target.value)}
        >
          {SUPPORTED_LANGUAGES.map((code) => (
            <option key={code} value={code}>
              {t(`languages.${code}`)}
            </option>
          ))}
        </select>
      </label>
    </main>
  );
}
