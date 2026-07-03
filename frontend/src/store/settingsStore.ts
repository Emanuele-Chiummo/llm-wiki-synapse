/**
 * settingsStore.ts — Zustand store for user settings (ADR-0018 §5 / F14 + F16-rest).
 *
 * Persists: language (synapse.lang) + contextWindowTokens (synapse.settings) to localStorage.
 * Also persists: serverUrl (synapse.serverUrl) for Tauri desktop runtime (ADR-0047 §2.1).
 * Also persists: theme (synapse.theme) "light"|"dark"|"system" (ADR-0048 §T1).
 * INVARIANT I3: separate from graphStore so settings changes never cause the graph to re-render.
 *
 * Theme applier (ADR-0048 §T1):
 *   - Runs on module load to apply the persisted theme before first paint.
 *   - Runs on setTheme to immediately reflect the change.
 *   - Listens to matchMedia("(prefers-color-scheme: dark)") when theme === "system"
 *     so the OS toggle re-resolves live.
 *   - Writes the RESOLVED value ("light"|"dark") to document.documentElement.dataset.theme,
 *     never the literal "system" string.
 *   - Guards the write with a .theme-switching class that suppresses CSS transitions for
 *     one frame (see theme.css .theme-switching rule), preventing colour-smear on swap.
 */

import { create } from "zustand";
import { getServerUrl, setServerUrl as baseSetServerUrl, clearServerUrl as baseClearServerUrl } from "../api/base";

// ─── Constants ────────────────────────────────────────────────────────────────

/** Available context window sizes in tokens (F14 / ADR-0018 §5). */
export const CONTEXT_WINDOW_OPTIONS = [
  4_096, 8_192, 16_384, 32_768, 65_536, 131_072, 262_144, 524_288, 1_048_576,
] as const;

export type ContextWindowTokens = (typeof CONTEXT_WINDOW_OPTIONS)[number];

/** Default context window: 32K. */
export const DEFAULT_CONTEXT_WINDOW: ContextWindowTokens = 32_768;

/**
 * Budget split (F14): 60/20/5/15 of the context window.
 * history/retrieved/system/generation.
 */
export function computeBudgetSplit(tokens: number): {
  history: number;
  retrieved: number;
  system: number;
  generation: number;
} {
  return {
    history: Math.round(tokens * 0.6),
    retrieved: Math.round(tokens * 0.2),
    system: Math.round(tokens * 0.05),
    generation: Math.round(tokens * 0.15),
  };
}

/**
 * Pretty-print a token count using 1024-based divisors so powers-of-two
 * render cleanly: 4096 → "4K", 32768 → "32K", 1048576 → "1M", etc.
 */
export function formatTokenCount(n: number): string {
  if (n >= 1_048_576) return `${+(n / 1_048_576).toPrecision(3)}M`;
  if (n >= 1_024) return `${+(n / 1_024).toPrecision(3)}K`;
  return `${n}`;
}

// ─── Persistence keys ─────────────────────────────────────────────────────────

/** Available conversation history window sizes (number of messages). */
export const CONV_HISTORY_OPTIONS = [2, 4, 6, 8, 10, 20] as const;
export type ConvHistoryLength = (typeof CONV_HISTORY_OPTIONS)[number];
export const DEFAULT_CONV_HISTORY: ConvHistoryLength = 10;

const LS_LANG = "synapse.lang";
const LS_SETTINGS = "synapse.settings";
const LS_THEME = "synapse.theme";

// ─── Theme types ──────────────────────────────────────────────────────────────

export type Theme = "light" | "dark" | "system";
export const DEFAULT_THEME: Theme = "system";

/**
 * Resolve the effective ("light"|"dark") value for a given theme setting.
 * "system" is resolved via matchMedia("(prefers-color-scheme: dark)").
 */
export function resolveTheme(theme: Theme): "light" | "dark" {
  if (theme === "light") return "light";
  if (theme === "dark") return "dark";
  // system: ask the OS
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

/**
 * Apply the resolved theme to document.documentElement.dataset.theme.
 * Guards with .theme-switching class to suppress CSS transition smear (ADR-0048 §T3).
 */
function applyThemeToDom(resolved: "light" | "dark"): void {
  try {
    const root = document.documentElement;
    root.classList.add("theme-switching");
    root.dataset["theme"] = resolved;
    // Remove the switching guard after one paint so transitions resume
    requestAnimationFrame(() => {
      root.classList.remove("theme-switching");
    });
  } catch {
    // SSR / test env without DOM — ignore
  }
}

// ─── System theme listener ────────────────────────────────────────────────────
// A single MediaQueryList listener is registered when theme === "system" and
// removed when theme changes to a concrete value.

let _systemListener: ((e: MediaQueryListEvent) => void) | null = null;
let _mql: MediaQueryList | null = null;

function installSystemListener(getTheme: () => Theme): void {
  try {
    _mql = window.matchMedia("(prefers-color-scheme: dark)");
    _systemListener = (e: MediaQueryListEvent) => {
      // Only act when current setting is still "system"
      if (getTheme() === "system") {
        applyThemeToDom(e.matches ? "dark" : "light");
      }
    };
    _mql.addEventListener("change", _systemListener);
  } catch {
    // ignore in non-browser envs
  }
}

function removeSystemListener(): void {
  try {
    if (_mql && _systemListener) {
      _mql.removeEventListener("change", _systemListener);
      _systemListener = null;
    }
  } catch {
    // ignore
  }
}

// ─── Persistence helpers ──────────────────────────────────────────────────────

interface PersistedSettings {
  contextWindowTokens: number;
  conversationHistoryLength?: number;
}

function loadSettings(): {
  language: string;
  contextWindowTokens: ContextWindowTokens;
  conversationHistoryLength: ConvHistoryLength;
  theme: Theme;
} {
  let language = "en";
  let contextWindowTokens: ContextWindowTokens = DEFAULT_CONTEXT_WINDOW;
  let conversationHistoryLength: ConvHistoryLength = DEFAULT_CONV_HISTORY;
  let theme: Theme = DEFAULT_THEME;

  try {
    const storedLang = localStorage.getItem(LS_LANG);
    if (storedLang === "en" || storedLang === "it") language = storedLang;
  } catch {
    // ignore
  }

  try {
    const raw = localStorage.getItem(LS_SETTINGS);
    if (raw) {
      const parsed = JSON.parse(raw) as PersistedSettings;
      const v = parsed.contextWindowTokens;
      if (CONTEXT_WINDOW_OPTIONS.includes(v as ContextWindowTokens)) {
        contextWindowTokens = v as ContextWindowTokens;
      }
      const h = parsed.conversationHistoryLength;
      if (h !== undefined && CONV_HISTORY_OPTIONS.includes(h as ConvHistoryLength)) {
        conversationHistoryLength = h as ConvHistoryLength;
      }
    }
  } catch {
    // ignore
  }

  try {
    const storedTheme = localStorage.getItem(LS_THEME);
    if (storedTheme === "light" || storedTheme === "dark" || storedTheme === "system") {
      theme = storedTheme;
    }
  } catch {
    // ignore
  }

  return { language, contextWindowTokens, conversationHistoryLength, theme };
}

function saveSettings(contextWindowTokens: number, conversationHistoryLength: number): void {
  try {
    localStorage.setItem(LS_SETTINGS, JSON.stringify({ contextWindowTokens, conversationHistoryLength }));
  } catch {
    // ignore
  }
}

// ─── State / Actions ─────────────────────────────────────────────────────────

interface SettingsState {
  language: string;
  contextWindowTokens: ContextWindowTokens;
  conversationHistoryLength: ConvHistoryLength;
  /** Desktop server URL (Tauri runtime, ADR-0047 §2.1). Null in web/PWA mode. */
  serverUrl: string | null;
  /**
   * Theme preference: "light" | "dark" | "system" (ADR-0048 §T1).
   * Persisted to localStorage["synapse.theme"]. Default: "system".
   */
  theme: Theme;
  /**
   * Web-only gate: true when apiFetch received a 401 response (ADR-0052).
   * Never persisted — always starts false; set by the register401Handler callback.
   * NOT used in Tauri (which uses the ConnectScreen gate instead).
   */
  authRequired: boolean;
}

interface SettingsActions {
  setLanguage: (lang: string) => void;
  setContextWindow: (tokens: ContextWindowTokens) => void;
  setConversationHistoryLength: (n: ConvHistoryLength) => void;
  /**
   * Persist a validated server URL (Tauri only, ADR-0047 §2.7.1).
   * Delegates to base.ts setServerUrl (validates scheme, strips trailing slash),
   * then updates the store so gate reactivity fires.
   * ConnectScreen must call this ONLY after a successful /status probe (ADR-0047 §2.7.2).
   */
  setServerUrl: (url: string) => void;
  /**
   * Clear the persisted server URL and return to the Connect gate (Tauri only).
   * Called by the "change server" action in Header.
   */
  clearServerUrl: () => void;
  /**
   * Set the theme preference (ADR-0048 §T1).
   * Persists to localStorage, applies to DOM immediately (resolved value),
   * and manages the system-change listener.
   */
  setTheme: (theme: Theme) => void;
  /**
   * Set or clear the authRequired gate (ADR-0052).
   * Called by the register401Handler callback in AppShell (web only).
   */
  setAuthRequired: (required: boolean) => void;
  reset: () => void;
}

export type SettingsStore = SettingsState & SettingsActions;

const initial = loadSettings();

export const useSettingsStore = create<SettingsStore>((set, get) => {
  // ── Apply theme on module load (before first paint) ──────────────────────
  const initialResolved = resolveTheme(initial.theme);
  applyThemeToDom(initialResolved);

  // ── Install system listener if starting in "system" mode ──────────────────
  if (initial.theme === "system") {
    installSystemListener(() => get().theme);
  }

  return {
    ...initial,
    serverUrl: getServerUrl(),
    authRequired: false,

    setLanguage: (language) => {
      try { localStorage.setItem(LS_LANG, language); } catch { /* ignore */ }
      set({ language });
    },

    setContextWindow: (contextWindowTokens) => {
      saveSettings(contextWindowTokens, get().conversationHistoryLength);
      set({ contextWindowTokens });
    },

    setConversationHistoryLength: (conversationHistoryLength) => {
      saveSettings(get().contextWindowTokens, conversationHistoryLength);
      set({ conversationHistoryLength });
    },

    setServerUrl: (url) => {
      // Delegates validation + storage to base.ts (ADR-0047 §2.7.1)
      baseSetServerUrl(url);
      set({ serverUrl: getServerUrl() });
    },

    clearServerUrl: () => {
      baseClearServerUrl();
      set({ serverUrl: null });
    },

    setTheme: (theme) => {
      try { localStorage.setItem(LS_THEME, theme); } catch { /* ignore */ }

      // Manage system listener: install when switching TO "system", remove when leaving
      const prev = get().theme;
      if (theme === "system" && prev !== "system") {
        installSystemListener(() => get().theme);
      } else if (theme !== "system" && prev === "system") {
        removeSystemListener();
      }

      // Apply the resolved value to the DOM
      const resolved = resolveTheme(theme);
      applyThemeToDom(resolved);

      set({ theme });
    },

    setAuthRequired: (required) => {
      set({ authRequired: required });
    },

    reset: () => {
      try {
        localStorage.removeItem(LS_LANG);
        localStorage.removeItem(LS_SETTINGS);
        localStorage.removeItem(LS_THEME);
        localStorage.removeItem("synapse-panel-layout-v2");
      } catch { /* ignore */ }
      // Re-apply default theme on reset
      applyThemeToDom(resolveTheme(DEFAULT_THEME));
      set({
        language: "en",
        contextWindowTokens: DEFAULT_CONTEXT_WINDOW,
        conversationHistoryLength: DEFAULT_CONV_HISTORY,
        theme: DEFAULT_THEME,
      });
    },
  };
});

// ─── Typed selectors (I3) ─────────────────────────────────────────────────────

export function selectLanguage(s: SettingsStore): string {
  return s.language;
}

export function selectContextWindow(s: SettingsStore): ContextWindowTokens {
  return s.contextWindowTokens;
}

export function selectConversationHistoryLength(s: SettingsStore): ConvHistoryLength {
  return s.conversationHistoryLength;
}

export function selectSetLanguage(s: SettingsStore): SettingsActions["setLanguage"] {
  return s.setLanguage;
}

export function selectSetContextWindow(s: SettingsStore): SettingsActions["setContextWindow"] {
  return s.setContextWindow;
}

export function selectSetConversationHistoryLength(
  s: SettingsStore,
): SettingsActions["setConversationHistoryLength"] {
  return s.setConversationHistoryLength;
}

export function selectResetSettings(s: SettingsStore): SettingsActions["reset"] {
  return s.reset;
}

export function selectServerUrl(s: SettingsStore): string | null {
  return s.serverUrl;
}

export function selectSetServerUrl(s: SettingsStore): SettingsActions["setServerUrl"] {
  return s.setServerUrl;
}

export function selectClearServerUrl(s: SettingsStore): SettingsActions["clearServerUrl"] {
  return s.clearServerUrl;
}

export function selectTheme(s: SettingsStore): Theme {
  return s.theme;
}

export function selectSetTheme(s: SettingsStore): SettingsActions["setTheme"] {
  return s.setTheme;
}

export function selectAuthRequired(s: SettingsStore): boolean {
  return s.authRequired;
}

export function selectSetAuthRequired(s: SettingsStore): SettingsActions["setAuthRequired"] {
  return s.setAuthRequired;
}
