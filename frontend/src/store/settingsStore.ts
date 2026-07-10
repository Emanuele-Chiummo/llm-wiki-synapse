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

// ─── Retrieval mode (B2 / F5) ─────────────────────────────────────────────────

export type RetrievalMode = "fast" | "standard" | "deep" | "local_first";

export const RETRIEVAL_MODE_OPTIONS: RetrievalMode[] = ["fast", "standard", "deep", "local_first"];

export const DEFAULT_RETRIEVAL_MODE: RetrievalMode = "standard";

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
  retrievalMode?: string;
  webSearchEnabled?: boolean;
}

function loadSettings(): {
  language: string;
  contextWindowTokens: ContextWindowTokens;
  conversationHistoryLength: ConvHistoryLength;
  theme: Theme;
  retrievalMode: RetrievalMode;
  webSearchEnabled: boolean;
} {
  let language = "en";
  let contextWindowTokens: ContextWindowTokens = DEFAULT_CONTEXT_WINDOW;
  let conversationHistoryLength: ConvHistoryLength = DEFAULT_CONV_HISTORY;
  let theme: Theme = DEFAULT_THEME;
  let retrievalMode: RetrievalMode = DEFAULT_RETRIEVAL_MODE;
  let webSearchEnabled = false;

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
      const rm = parsed.retrievalMode;
      if (rm !== undefined && RETRIEVAL_MODE_OPTIONS.includes(rm as RetrievalMode)) {
        retrievalMode = rm as RetrievalMode;
      }
      if (typeof parsed.webSearchEnabled === "boolean") {
        webSearchEnabled = parsed.webSearchEnabled;
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

  return { language, contextWindowTokens, conversationHistoryLength, theme, retrievalMode, webSearchEnabled };
}

function saveSettings(
  contextWindowTokens: number,
  conversationHistoryLength: number,
  retrievalMode: RetrievalMode,
  webSearchEnabled: boolean,
): void {
  try {
    localStorage.setItem(
      LS_SETTINGS,
      JSON.stringify({ contextWindowTokens, conversationHistoryLength, retrievalMode, webSearchEnabled }),
    );
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
  /**
   * Retrieval mode sent to POST /chat/stream (B2 / F5).
   * Persisted to localStorage["synapse.settings"]. Default: "standard".
   */
  retrievalMode: RetrievalMode;
  /**
   * Whether web search is enabled for chat (B2).
   * Persisted to localStorage["synapse.settings"]. Default: false.
   */
  webSearchEnabled: boolean;

  // ── Draft layer (F16 unified-save UX) ─────────────────────────────────────
  // Staged values for the 4 client-preference fields. Sections read/write these;
  // the committed fields above are the source of truth persisted to localStorage.
  // isDirty: any draft !== its committed counterpart.
  draftTheme: Theme;
  draftLanguage: string;
  draftConversationHistoryLength: ConvHistoryLength;
  draftContextWindowTokens: ContextWindowTokens;
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
  /** Set retrieval mode and persist to localStorage (B2). */
  setRetrievalMode: (mode: RetrievalMode) => void;
  /** Toggle web-search-enabled flag and persist to localStorage (B2). */
  setWebSearchEnabled: (enabled: boolean) => void;
  reset: () => void;

  // ── Draft layer actions (F16 unified-save UX) ──────────────────────────────
  /** Stage a theme change without persisting (commits on commitDraft). */
  setDraftTheme: (theme: Theme) => void;
  /** Stage a language change without persisting (commits on commitDraft). */
  setDraftLanguage: (lang: string) => void;
  /** Stage a conversation-history-length change without persisting. */
  setDraftConversationHistoryLength: (n: ConvHistoryLength) => void;
  /** Stage a context-window change without persisting. */
  setDraftContextWindow: (tokens: ContextWindowTokens) => void;
  /**
   * Commit all staged drafts: persist to localStorage, apply DOM side-effects
   * (theme → DOM, settings → LS). Caller is responsible for i18n.changeLanguage.
   * After commit, isDirty = false.
   */
  commitDraft: () => void;
  /**
   * Discard all staged drafts: resets draft values to match current committed values.
   * After discard, isDirty = false.
   */
  discardDraft: () => void;
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

    // Draft values start equal to committed values → isDirty = false on mount
    draftTheme: initial.theme,
    draftLanguage: initial.language,
    draftConversationHistoryLength: initial.conversationHistoryLength,
    draftContextWindowTokens: initial.contextWindowTokens,

    setLanguage: (language) => {
      try { localStorage.setItem(LS_LANG, language); } catch { /* ignore */ }
      set({ language });
    },

    setContextWindow: (contextWindowTokens) => {
      const s = get();
      saveSettings(contextWindowTokens, s.conversationHistoryLength, s.retrievalMode, s.webSearchEnabled);
      set({ contextWindowTokens });
    },

    setConversationHistoryLength: (conversationHistoryLength) => {
      const s = get();
      saveSettings(s.contextWindowTokens, conversationHistoryLength, s.retrievalMode, s.webSearchEnabled);
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

    setRetrievalMode: (retrievalMode) => {
      const s = get();
      saveSettings(s.contextWindowTokens, s.conversationHistoryLength, retrievalMode, s.webSearchEnabled);
      set({ retrievalMode });
    },

    setWebSearchEnabled: (webSearchEnabled) => {
      const s = get();
      saveSettings(s.contextWindowTokens, s.conversationHistoryLength, s.retrievalMode, webSearchEnabled);
      set({ webSearchEnabled });
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
        retrievalMode: DEFAULT_RETRIEVAL_MODE,
        webSearchEnabled: false,
        // Also reset drafts so isDirty = false after reset
        draftTheme: DEFAULT_THEME,
        draftLanguage: "en",
        draftContextWindowTokens: DEFAULT_CONTEXT_WINDOW,
        draftConversationHistoryLength: DEFAULT_CONV_HISTORY,
      });
    },

    // ── Draft layer (F16 unified-save UX) ───────────────────────────────────

    setDraftTheme: (draftTheme) => set({ draftTheme }),

    setDraftLanguage: (draftLanguage) => set({ draftLanguage }),

    setDraftConversationHistoryLength: (draftConversationHistoryLength) =>
      set({ draftConversationHistoryLength }),

    setDraftContextWindow: (draftContextWindowTokens) => set({ draftContextWindowTokens }),

    commitDraft: () => {
      const s = get();
      // Call existing setters so each field gets persisted + side-effects applied:
      // setTheme: persists to LS, applies DOM, manages system listener
      // setLanguage: persists to LS
      // setContextWindow: persists to LS (budget split)
      // setConversationHistoryLength: persists to LS
      s.setTheme(s.draftTheme);
      s.setLanguage(s.draftLanguage);
      s.setContextWindow(s.draftContextWindowTokens);
      s.setConversationHistoryLength(s.draftConversationHistoryLength);
      // After these calls the committed values equal draft values → isDirty = false.
      // (No additional set() needed; selectIsDirty computes false from the updated state.)
    },

    discardDraft: () => {
      const s = get();
      set({
        draftTheme: s.theme,
        draftLanguage: s.language,
        draftContextWindowTokens: s.contextWindowTokens,
        draftConversationHistoryLength: s.conversationHistoryLength,
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

export function selectRetrievalMode(s: SettingsStore): RetrievalMode {
  return s.retrievalMode;
}

export function selectSetRetrievalMode(s: SettingsStore): SettingsActions["setRetrievalMode"] {
  return s.setRetrievalMode;
}

export function selectWebSearchEnabled(s: SettingsStore): boolean {
  return s.webSearchEnabled;
}

export function selectSetWebSearchEnabled(s: SettingsStore): SettingsActions["setWebSearchEnabled"] {
  return s.setWebSearchEnabled;
}

// ─── Draft layer selectors (F16 unified-save UX) ─────────────────────────────

export function selectDraftTheme(s: SettingsStore): Theme {
  return s.draftTheme;
}

export function selectDraftLanguage(s: SettingsStore): string {
  return s.draftLanguage;
}

export function selectDraftConversationHistoryLength(s: SettingsStore): ConvHistoryLength {
  return s.draftConversationHistoryLength;
}

export function selectDraftContextWindow(s: SettingsStore): ContextWindowTokens {
  return s.draftContextWindowTokens;
}

export function selectSetDraftTheme(s: SettingsStore): SettingsActions["setDraftTheme"] {
  return s.setDraftTheme;
}

export function selectSetDraftLanguage(s: SettingsStore): SettingsActions["setDraftLanguage"] {
  return s.setDraftLanguage;
}

export function selectSetDraftConversationHistoryLength(
  s: SettingsStore,
): SettingsActions["setDraftConversationHistoryLength"] {
  return s.setDraftConversationHistoryLength;
}

export function selectSetDraftContextWindow(
  s: SettingsStore,
): SettingsActions["setDraftContextWindow"] {
  return s.setDraftContextWindow;
}

/**
 * True when any staged draft differs from its committed counterpart.
 * Used by SettingsSaveFooter to show/hide the Save bar.
 */
export function selectIsDirty(s: SettingsStore): boolean {
  return (
    s.draftTheme !== s.theme ||
    s.draftLanguage !== s.language ||
    s.draftConversationHistoryLength !== s.conversationHistoryLength ||
    s.draftContextWindowTokens !== s.contextWindowTokens
  );
}

export function selectCommitDraft(s: SettingsStore): SettingsActions["commitDraft"] {
  return s.commitDraft;
}

export function selectDiscardDraft(s: SettingsStore): SettingsActions["discardDraft"] {
  return s.discardDraft;
}
