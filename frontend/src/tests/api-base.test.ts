/**
 * api-base.test.ts — unit tests for frontend/src/api/base.ts (ADR-0047 §2.1 / §2.7.1).
 *
 * Covers:
 *   - Priority order: localStorage > VITE_API_BASE > ""
 *   - Trailing-slash stripping
 *   - Scheme rejection (non-http/https)
 *   - Call-time resolution: changing localStorage changes apiBase() result
 *   - getServerUrl / setServerUrl / clearServerUrl
 *   - isTauri()
 *
 * Node.js 26 note: window.localStorage and globalThis.localStorage are
 * undefined in the jsdom test env because Node 26 intercepts the global
 * before jsdom can replace it. We inject a fake Storage object via
 * vi.stubGlobal so base.ts can read/write it normally.
 *
 * PROJECT GOTCHA: vi.clearAllMocks() wipes mock implementations — re-set
 * them inside each beforeEach.
 *
 * @vitest-environment jsdom
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// ─── Fake localStorage ────────────────────────────────────────────────────────

/** Minimal in-memory Storage implementation for Node.js 26 / jsdom compat. */
function makeFakeStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    get length() {
      return Object.keys(store).length;
    },
    key(n: number) {
      return Object.keys(store)[n] ?? null;
    },
    getItem(k: string) {
      return Object.prototype.hasOwnProperty.call(store, k) ? (store[k] ?? null) : null;
    },
    setItem(k: string, v: string) {
      store[k] = v;
    },
    removeItem(k: string) {
      delete store[k];
    },
    clear() {
      store = {};
    },
  };
}

const fakeStorage = makeFakeStorage();

// Inject before any imports that touch localStorage
vi.stubGlobal("localStorage", fakeStorage);

// ─── Import module under test (after stubbing) ────────────────────────────────

import {
  apiBase,
  getServerUrl,
  setServerUrl,
  clearServerUrl,
  getLastServerUrl,
  isTauri,
} from "../api/base";

// ─── Setup ────────────────────────────────────────────────────────────────────

const LS_KEY = "synapse.serverUrl";

beforeEach(() => {
  fakeStorage.clear();
  // Reset any __TAURI_INTERNALS__ state between tests
  const w = globalThis as Record<string, unknown>;
  if ("__TAURI_INTERNALS__" in w) {
    delete w["__TAURI_INTERNALS__"];
  }
});

afterEach(() => {
  fakeStorage.clear();
  vi.restoreAllMocks();
  const w = globalThis as Record<string, unknown>;
  if ("__TAURI_INTERNALS__" in w) {
    delete w["__TAURI_INTERNALS__"];
  }
});

// ─── apiBase() priority order ─────────────────────────────────────────────────

describe("apiBase() — priority order", () => {
  it("returns '' when localStorage is empty and VITE_API_BASE is unset", () => {
    expect(apiBase()).toBe("");
  });

  it("returns the localStorage value when set (priority 1)", () => {
    fakeStorage.setItem(LS_KEY, "http://truenas:8000");
    expect(apiBase()).toBe("http://truenas:8000");
  });

  it("strips trailing slash from localStorage value", () => {
    fakeStorage.setItem(LS_KEY, "http://truenas:8000/");
    expect(apiBase()).toBe("http://truenas:8000");
  });

  it("strips multiple trailing slashes from localStorage value", () => {
    fakeStorage.setItem(LS_KEY, "http://truenas:8000///");
    expect(apiBase()).toBe("http://truenas:8000");
  });

  it("resolves at call time: changing localStorage changes apiBase() result", () => {
    // Initially empty
    expect(apiBase()).toBe("");

    // Set a value
    fakeStorage.setItem(LS_KEY, "http://server-a:8000");
    expect(apiBase()).toBe("http://server-a:8000");

    // Change to a different value
    fakeStorage.setItem(LS_KEY, "http://server-b:9000");
    expect(apiBase()).toBe("http://server-b:9000");

    // Clear
    fakeStorage.removeItem(LS_KEY);
    expect(apiBase()).toBe("");
  });
});

// ─── Trailing-slash stripping ─────────────────────────────────────────────────

describe("setServerUrl() — trailing-slash stripping", () => {
  it("stores without trailing slash", () => {
    setServerUrl("http://truenas:8000/");
    expect(getServerUrl()).toBe("http://truenas:8000");
  });

  it("stores value unchanged when no trailing slash", () => {
    setServerUrl("http://truenas:8000");
    expect(getServerUrl()).toBe("http://truenas:8000");
  });

  it("trims whitespace", () => {
    setServerUrl("  http://truenas:8000  ");
    expect(getServerUrl()).toBe("http://truenas:8000");
  });

  it("stores https:// URL correctly", () => {
    setServerUrl("https://synapse.tailnet/");
    expect(getServerUrl()).toBe("https://synapse.tailnet");
  });
});

// ─── Scheme rejection ─────────────────────────────────────────────────────────

describe("setServerUrl() — scheme allowlist (ADR-0047 §2.7.1)", () => {
  it("throws TypeError for javascript: scheme", () => {
    expect(() => setServerUrl("javascript:alert(1)")).toThrow(TypeError);
  });

  it("throws TypeError for file: scheme", () => {
    expect(() => setServerUrl("file:///etc/passwd")).toThrow(TypeError);
  });

  it("throws TypeError for tauri: scheme", () => {
    expect(() => setServerUrl("tauri://localhost")).toThrow(TypeError);
  });

  it("throws TypeError for ftp: scheme", () => {
    expect(() => setServerUrl("ftp://server")).toThrow(TypeError);
  });

  it("throws TypeError for a bare hostname with no scheme", () => {
    // "truenas:8000" parses as scheme="truenas:" — not http/https
    expect(() => setServerUrl("truenas:8000")).toThrow(TypeError);
  });

  it("does NOT throw for http:// scheme", () => {
    expect(() => setServerUrl("http://truenas:8000")).not.toThrow();
  });

  it("does NOT throw for https:// scheme", () => {
    expect(() => setServerUrl("https://synapse.example.com")).not.toThrow();
  });

  it("does not persist when scheme is invalid (storage must be empty)", () => {
    try {
      setServerUrl("javascript:void(0)");
    } catch {
      /* expected */
    }
    expect(getServerUrl()).toBeNull();
    expect(fakeStorage.getItem(LS_KEY)).toBeNull();
  });
});

// ─── getServerUrl / clearServerUrl ────────────────────────────────────────────

describe("getServerUrl() / clearServerUrl()", () => {
  it("returns null when nothing is stored", () => {
    expect(getServerUrl()).toBeNull();
  });

  it("returns the stored value after setServerUrl", () => {
    setServerUrl("http://myserver:8000");
    expect(getServerUrl()).toBe("http://myserver:8000");
  });

  it("returns null after clearServerUrl", () => {
    setServerUrl("http://myserver:8000");
    clearServerUrl();
    expect(getServerUrl()).toBeNull();
  });

  it("clearServerUrl is idempotent when nothing is set", () => {
    expect(() => clearServerUrl()).not.toThrow();
    expect(getServerUrl()).toBeNull();
  });
});

// ─── isTauri() ────────────────────────────────────────────────────────────────

describe("isTauri()", () => {
  it("returns false in jsdom (no __TAURI_INTERNALS__)", () => {
    expect(isTauri()).toBe(false);
  });

  it("returns true when __TAURI_INTERNALS__ is present on window/globalThis", () => {
    (globalThis as Record<string, unknown>)["__TAURI_INTERNALS__"] = {};
    expect(isTauri()).toBe(true);
  });

  it("returns false again after __TAURI_INTERNALS__ is removed", () => {
    (globalThis as Record<string, unknown>)["__TAURI_INTERNALS__"] = {};
    delete (globalThis as Record<string, unknown>)["__TAURI_INTERNALS__"];
    expect(isTauri()).toBe(false);
  });
});

describe("getLastServerUrl() — survives clearServerUrl (Connect gate prefill)", () => {
  it("is set by setServerUrl alongside the active URL", () => {
    setServerUrl("http://truenas:8000");
    expect(getLastServerUrl()).toBe("http://truenas:8000");
  });

  it("survives clearServerUrl while the active URL is removed", () => {
    setServerUrl("http://truenas:8000");
    clearServerUrl();
    expect(getServerUrl()).toBeNull();
    expect(getLastServerUrl()).toBe("http://truenas:8000");
  });

  it("returns null when the app never connected", () => {
    expect(getLastServerUrl()).toBeNull();
  });
});
