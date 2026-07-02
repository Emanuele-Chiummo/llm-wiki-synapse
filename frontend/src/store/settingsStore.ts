/**
 * settingsStore.ts — Zustand store for user settings (ADR-0018 §5 / F14 + F16-rest).
 *
 * Persists: language (synapse.lang) + contextWindowTokens (synapse.settings) to localStorage.
 * Also persists: serverUrl (synapse.serverUrl) for Tauri desktop runtime (ADR-0047 §2.1).
 * INVARIANT I3: separate from graphStore so settings changes never cause the graph to re-render.
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

interface PersistedSettings {
  contextWindowTokens: number;
  conversationHistoryLength?: number;
}

function loadSettings(): {
  language: string;
  contextWindowTokens: ContextWindowTokens;
  conversationHistoryLength: ConvHistoryLength;
} {
  let language = "en";
  let contextWindowTokens: ContextWindowTokens = DEFAULT_CONTEXT_WINDOW;
  let conversationHistoryLength: ConvHistoryLength = DEFAULT_CONV_HISTORY;

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

  return { language, contextWindowTokens, conversationHistoryLength };
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
  reset: () => void;
}

export type SettingsStore = SettingsState & SettingsActions;

const initial = loadSettings();

export const useSettingsStore = create<SettingsStore>((set, get) => ({
  ...initial,
  serverUrl: getServerUrl(),

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

  reset: () => {
    try {
      localStorage.removeItem(LS_LANG);
      localStorage.removeItem(LS_SETTINGS);
      localStorage.removeItem("synapse-panel-layout-v2");
    } catch { /* ignore */ }
    set({
      language: "en",
      contextWindowTokens: DEFAULT_CONTEXT_WINDOW,
      conversationHistoryLength: DEFAULT_CONV_HISTORY,
    });
  },
}));

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
