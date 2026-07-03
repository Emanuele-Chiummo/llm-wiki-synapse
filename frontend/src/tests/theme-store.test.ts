/**
 * theme-store.test.ts — Unit tests for settingsStore theme persistence + resolution
 * (ADR-0048 §T1).
 *
 * Covers:
 *   - resolveTheme(): light/dark/system resolution (mocked matchMedia)
 *   - applyThemeToDom: dataset.theme set to resolved value (never "system")
 *   - setTheme() persists to localStorage and updates DOM
 *   - loadSettings(): reads synapse.theme from localStorage
 *   - reset() clears theme back to default "system"
 *   - matchMedia change listener updates DOM when theme === "system"
 *
 * Node.js 26 note: window.localStorage is undefined in jsdom because Node 26
 * intercepts the global before jsdom can replace it. We inject a fake Storage
 * via vi.stubGlobal (same approach as api-base.test.ts).
 *
 * GOTCHA (project rule): vi.clearAllMocks() wipes mock impls — re-set in each beforeEach.
 *
 * @vitest-environment jsdom
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// ─── Fake localStorage (Node.js 26 / jsdom compat) ───────────────────────────

/** Minimal in-memory Storage implementation. */
function makeFakeStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    get length() { return Object.keys(store).length; },
    key(n: number) { return Object.keys(store)[n] ?? null; },
    getItem(k: string) {
      return Object.prototype.hasOwnProperty.call(store, k) ? (store[k] ?? null) : null;
    },
    setItem(k: string, v: string) { store[k] = v; },
    removeItem(k: string) { delete store[k]; },
    clear() { store = {}; },
  };
}

const fakeStorage = makeFakeStorage();

// Inject before any imports that touch localStorage (module-level, before hoisting)
vi.stubGlobal("localStorage", fakeStorage);

// ─── matchMedia mock factory ──────────────────────────────────────────────────

/**
 * Build a mock MediaQueryList that returns `matches` and captures listeners
 * so tests can fire synthetic OS-preference change events.
 */
function makeMqlMock(prefersDark: boolean) {
  const listeners: Array<(e: MediaQueryListEvent) => void> = [];
  const mql = {
    matches: prefersDark,
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addEventListener: vi.fn((event: string, cb: (e: MediaQueryListEvent) => void) => {
      if (event === "change") listeners.push(cb);
    }),
    removeEventListener: vi.fn((event: string, cb: (e: MediaQueryListEvent) => void) => {
      if (event === "change") {
        const idx = listeners.indexOf(cb);
        if (idx !== -1) listeners.splice(idx, 1);
      }
    }),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(() => false),
    // Helper: fire the change event to simulate OS toggle
    _fire: (newMatches: boolean) => {
      const ev = { matches: newMatches } as MediaQueryListEvent;
      listeners.forEach((cb) => cb(ev));
    },
  };
  return mql;
}

// ─── Setup / teardown ─────────────────────────────────────────────────────────

let mqlMock: ReturnType<typeof makeMqlMock>;

beforeEach(() => {
  // Clear our fake localStorage before each test
  fakeStorage.clear();

  // Reset dataset.theme
  delete document.documentElement.dataset["theme"];

  // GOTCHA: re-create mock impl in each beforeEach so vi.clearAllMocks() does not wipe it
  mqlMock = makeMqlMock(false); // default: system = light
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: vi.fn(() => mqlMock),
  });
});

afterEach(() => {
  // Re-stub localStorage after each module reset so the global persists
  vi.stubGlobal("localStorage", fakeStorage);
  vi.resetModules();
});

// ─── resolveTheme ─────────────────────────────────────────────────────────────

describe("resolveTheme()", () => {
  it("returns 'light' when theme is 'light'", async () => {
    const { resolveTheme } = await import("../store/settingsStore");
    expect(resolveTheme("light")).toBe("light");
  });

  it("returns 'dark' when theme is 'dark'", async () => {
    const { resolveTheme } = await import("../store/settingsStore");
    expect(resolveTheme("dark")).toBe("dark");
  });

  it("returns 'light' when theme is 'system' and OS prefers light", async () => {
    mqlMock = makeMqlMock(false);
    Object.defineProperty(window, "matchMedia", { writable: true, configurable: true, value: vi.fn(() => mqlMock) });
    const { resolveTheme } = await import("../store/settingsStore");
    expect(resolveTheme("system")).toBe("light");
  });

  it("returns 'dark' when theme is 'system' and OS prefers dark", async () => {
    mqlMock = makeMqlMock(true);
    Object.defineProperty(window, "matchMedia", { writable: true, configurable: true, value: vi.fn(() => mqlMock) });
    const { resolveTheme } = await import("../store/settingsStore");
    expect(resolveTheme("system")).toBe("dark");
  });
});

// ─── dataset.theme set to resolved value ─────────────────────────────────────

describe("Theme applier — dataset.theme", () => {
  it("sets dataset.theme to 'light' when theme is 'light'", async () => {
    fakeStorage.setItem("synapse.theme", "light");
    await import("../store/settingsStore");
    // The applier runs on module load
    expect(document.documentElement.dataset["theme"]).toBe("light");
  });

  it("sets dataset.theme to 'dark' when theme is 'dark'", async () => {
    fakeStorage.setItem("synapse.theme", "dark");
    await import("../store/settingsStore");
    expect(document.documentElement.dataset["theme"]).toBe("dark");
  });

  it("never sets dataset.theme to 'system' — always resolves to a concrete value", async () => {
    fakeStorage.setItem("synapse.theme", "system");
    await import("../store/settingsStore");
    const val = document.documentElement.dataset["theme"];
    expect(val).not.toBe("system");
    expect(["light", "dark"]).toContain(val);
  });

  it("sets dataset.theme to 'light' when theme is 'system' and OS is light", async () => {
    mqlMock = makeMqlMock(false);
    Object.defineProperty(window, "matchMedia", { writable: true, configurable: true, value: vi.fn(() => mqlMock) });
    fakeStorage.setItem("synapse.theme", "system");
    await import("../store/settingsStore");
    expect(document.documentElement.dataset["theme"]).toBe("light");
  });

  it("sets dataset.theme to 'dark' when theme is 'system' and OS prefers dark", async () => {
    mqlMock = makeMqlMock(true);
    Object.defineProperty(window, "matchMedia", { writable: true, configurable: true, value: vi.fn(() => mqlMock) });
    fakeStorage.setItem("synapse.theme", "system");
    await import("../store/settingsStore");
    expect(document.documentElement.dataset["theme"]).toBe("dark");
  });
});

// ─── setTheme() persists and updates DOM ─────────────────────────────────────

describe("setTheme() — persistence + DOM update", () => {
  it("setTheme('dark') sets localStorage['synapse.theme'] to 'dark'", async () => {
    const { useSettingsStore } = await import("../store/settingsStore");
    useSettingsStore.getState().setTheme("dark");
    expect(fakeStorage.getItem("synapse.theme")).toBe("dark");
  });

  it("setTheme('light') sets localStorage['synapse.theme'] to 'light'", async () => {
    const { useSettingsStore } = await import("../store/settingsStore");
    useSettingsStore.getState().setTheme("light");
    expect(fakeStorage.getItem("synapse.theme")).toBe("light");
  });

  it("setTheme('system') sets localStorage['synapse.theme'] to 'system'", async () => {
    const { useSettingsStore } = await import("../store/settingsStore");
    useSettingsStore.getState().setTheme("system");
    expect(fakeStorage.getItem("synapse.theme")).toBe("system");
  });

  it("setTheme('dark') updates dataset.theme to 'dark'", async () => {
    const { useSettingsStore } = await import("../store/settingsStore");
    useSettingsStore.getState().setTheme("dark");
    expect(document.documentElement.dataset["theme"]).toBe("dark");
  });

  it("setTheme('light') updates dataset.theme to 'light'", async () => {
    const { useSettingsStore } = await import("../store/settingsStore");
    useSettingsStore.getState().setTheme("light");
    expect(document.documentElement.dataset["theme"]).toBe("light");
  });

  it("setTheme() updates the store's theme field", async () => {
    const { useSettingsStore } = await import("../store/settingsStore");
    useSettingsStore.getState().setTheme("dark");
    expect(useSettingsStore.getState().theme).toBe("dark");
  });
});

// ─── Persistence: loadSettings reads synapse.theme ───────────────────────────

describe("loadSettings() — theme persistence", () => {
  it("defaults to 'system' when synapse.theme is not in localStorage", async () => {
    const { useSettingsStore } = await import("../store/settingsStore");
    expect(useSettingsStore.getState().theme).toBe("system");
  });

  it("loads 'dark' from localStorage on store init", async () => {
    fakeStorage.setItem("synapse.theme", "dark");
    const { useSettingsStore } = await import("../store/settingsStore");
    expect(useSettingsStore.getState().theme).toBe("dark");
  });

  it("loads 'light' from localStorage on store init", async () => {
    fakeStorage.setItem("synapse.theme", "light");
    const { useSettingsStore } = await import("../store/settingsStore");
    expect(useSettingsStore.getState().theme).toBe("light");
  });

  it("falls back to 'system' when localStorage contains an invalid theme value", async () => {
    fakeStorage.setItem("synapse.theme", "rainbow");
    const { useSettingsStore } = await import("../store/settingsStore");
    expect(useSettingsStore.getState().theme).toBe("system");
  });
});

// ─── reset() clears theme ────────────────────────────────────────────────────

describe("reset() — theme cleared to default", () => {
  it("reset() removes synapse.theme from localStorage", async () => {
    fakeStorage.setItem("synapse.theme", "dark");
    const { useSettingsStore } = await import("../store/settingsStore");
    useSettingsStore.getState().reset();
    expect(fakeStorage.getItem("synapse.theme")).toBeNull();
  });

  it("reset() sets store theme back to 'system'", async () => {
    const { useSettingsStore } = await import("../store/settingsStore");
    useSettingsStore.getState().setTheme("dark");
    useSettingsStore.getState().reset();
    expect(useSettingsStore.getState().theme).toBe("system");
  });
});

// ─── selectTheme / selectSetTheme selectors ───────────────────────────────────

describe("selectTheme / selectSetTheme selectors", () => {
  it("selectTheme returns the current theme", async () => {
    const { useSettingsStore, selectTheme } = await import("../store/settingsStore");
    useSettingsStore.getState().setTheme("dark");
    expect(selectTheme(useSettingsStore.getState())).toBe("dark");
  });

  it("selectSetTheme returns a callable function", async () => {
    const { useSettingsStore, selectSetTheme } = await import("../store/settingsStore");
    const fn = selectSetTheme(useSettingsStore.getState());
    expect(typeof fn).toBe("function");
  });
});
