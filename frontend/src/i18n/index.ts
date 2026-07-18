/**
 * i18n/index.ts — i18next initialisation for Synapse (F16 / ADR-0018 §6).
 *
 * Detection order: localStorage(synapse.lang) → navigator.language → fallback "en".
 * Supported languages: "en" (English) and "it" (Italian).
 *
 * FE-BUNDLE-1: only the default locale (en) is bundled eagerly. The Italian locale
 * is a separate dynamic chunk, loaded on demand:
 *   • At startup: if the detected language is "it", it.json is loaded via dynamic
 *     import() immediately after init completes — the brief flash before the async
 *     load is the accepted trade-off of this approach (per FE-BUNDLE-1 spec).
 *   • At runtime: the language-switch handler calls `loadLocale(lang)` before
 *     calling i18n.changeLanguage(), so Italian resources are available by the
 *     time react-i18next re-renders with the new language.
 *
 * Import this module ONCE from main.tsx before rendering the React tree.
 * Every component accesses translations via the react-i18next `useTranslation()` hook.
 * No display string is hardcoded in any new component — all strings are i18n keys (AC-F16-i18n-2).
 */

import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

// Only the default locale is a static (eager) import. It ships in the main chunk
// and is always available synchronously at module load time.
import en from "./locales/en.json";
// loadLocale lives in its own file so callers can import it without pulling in
// this module's i18n init side-effects (which would break test mocks of react-i18next).
import { loadLocale } from "./loadLocale";
export { loadLocale } from "./loadLocale";

function applyDocumentLanguage(language: string): void {
  document.documentElement.lang = language.split("-")[0] || "en";
}

i18n.on("languageChanged", applyDocumentLanguage);

const initPromise = i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    // Only "en" resources are provided at init time (FE-BUNDLE-1).
    resources: {
      en: { translation: en },
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
  });

// After init: apply the document language and, if the detected language is not
// "en", lazily load its bundle and then call changeLanguage to trigger a re-render.
void initPromise.then(async () => {
  applyDocumentLanguage(i18n.resolvedLanguage ?? i18n.language);
  const lang = (i18n.resolvedLanguage ?? i18n.language).split("-")[0] ?? "en";
  if (lang !== "en") {
    await loadLocale(lang);
    // Re-apply the language now that resources are available. This is a no-op if
    // i18next is already set to this language (changeLanguage is idempotent).
    await i18n.changeLanguage(lang);
  }
});

export default i18n;
