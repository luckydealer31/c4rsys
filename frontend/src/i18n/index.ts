import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import ru from "./locales/ru.json";
import th from "./locales/th.json";

// ТЗ § 2.7.1: MVP languages; device language is used when a translation is connected,
// otherwise English. A language saved in the profile takes priority after login (§ 2.7.4, T02).
export const SUPPORTED_LANGUAGES = ["en", "ru", "th"] as const;

const deviceLanguage = (navigator.language || "en").slice(0, 2);
const initial = (SUPPORTED_LANGUAGES as readonly string[]).includes(deviceLanguage)
  ? deviceLanguage
  : "en";

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, ru: { translation: ru }, th: { translation: th } },
  lng: initial,
  fallbackLng: "en",
  supportedLngs: [...SUPPORTED_LANGUAGES],
  interpolation: { escapeValue: false },
});

export default i18n;
