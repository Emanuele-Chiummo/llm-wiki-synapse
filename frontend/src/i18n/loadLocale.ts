/**
 * loadLocale.ts — lazy locale loader (FE-BUNDLE-1).
 *
 * Separated from i18n/index.ts so that importing this helper does NOT pull in
 * the i18n initialisation side-effects (the `.use(initReactI18next).init()`
 * chain), which would break test files that partially mock `react-i18next`.
 *
 * Usage:
 *   import { loadLocale } from "../../i18n/loadLocale";
 *   await loadLocale("it");   // no-op if already loaded
 */

import i18n from "i18next";

/**
 * loadLocale — lazy-load a non-default locale and add it to the i18n resource store.
 * Safe to call multiple times; skips the network if the bundle is already loaded.
 * Only "it" is a dynamic import; future locales should follow the same pattern.
 */
export async function loadLocale(lang: string): Promise<void> {
  if (lang === "en") return; // Always available — static import in i18n/index.ts
  // Guard: hasResourceBundle may not be defined when i18next is not initialized (e.g. tests).
  if (typeof i18n.hasResourceBundle === "function" && i18n.hasResourceBundle(lang, "translation")) return; // Already loaded

  if (lang === "it") {
    // Dynamic import produces a separate chunk (FE-BUNDLE-1).
    const mod = await import("./locales/it.json");
    // addResourceBundle(lang, ns, resources, deep, overwrite)
    i18n.addResourceBundle(
      "it",
      "translation",
      // Vite/esbuild wraps JSON as { default: … } in ESM; handle both shapes.
      (mod as { default?: unknown }).default ?? mod,
      true,
      true,
    );
  }
}
