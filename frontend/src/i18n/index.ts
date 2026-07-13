/**
 * i18n/index.ts — i18next initialisation for Synapse (F16 / ADR-0018 §6).
 *
 * Detection order: localStorage(synapse.lang) → navigator.language → fallback "en".
 * Supported languages: "en" (English) and "it" (Italian).
 *
 * Import this module ONCE from main.tsx before rendering the React tree.
 * Every component accesses translations via the react-i18next `useTranslation()` hook.
 * No display string is hardcoded in any new component — all strings are i18n keys (AC-F16-i18n-2).
 */

import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import en from "./locales/en.json";
import it from "./locales/it.json";

function applyDocumentLanguage(language: string): void {
  document.documentElement.lang = language.split("-")[0] || "en";
}

i18n.on("languageChanged", applyDocumentLanguage);

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    // Locale resources
    resources: {
      en: { translation: en },
      it: { translation: it },
    },
    fallbackLng: "en",
    supportedLngs: ["en", "it"],
    // Detection order: localStorage(synapse.lang) → navigator → fallback
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "synapse.lang",
      caches: ["localStorage"],
    },
    interpolation: {
      escapeValue: false, // React already escapes
    },
    // Disable the default namespace prefix — we use flat keys
    defaultNS: "translation",
    ns: ["translation"],
  })
  .then(() => applyDocumentLanguage(i18n.resolvedLanguage ?? i18n.language));

export default i18n;
