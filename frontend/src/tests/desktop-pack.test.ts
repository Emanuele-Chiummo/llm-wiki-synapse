/**
 * desktop-pack.test.ts — unit tests for T4 desktop pack features (ADR-0048 §T4).
 *
 * Tests:
 *   1. getKnownServers / addKnownServer (via setServerUrl): dedupe, cap, order
 *   2. useDesktopZoom: clamps, persists, restores, no-op outside Tauri
 *
 * @vitest-environment jsdom
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// ─── Fake localStorage (Node.js 26 / jsdom compat) ────────────────────────────

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
vi.stubGlobal("localStorage", fakeStorage);

// ─── Import modules under test (after stubbing) ───────────────────────────────

import { getKnownServers, setServerUrl, clearServerUrl } from "../api/base";
import { renderHook, act } from "@testing-library/react";
import { useDesktopZoom } from "../hooks/useDesktopZoom";

// ─── Setup ────────────────────────────────────────────────────────────────────

const LS_SERVERS = "synapse.servers";
const LS_ZOOM = "synapse.zoom";

beforeEach(() => {
  fakeStorage.clear();
  // Reset __TAURI_INTERNALS__ between tests
  const w = globalThis as Record<string, unknown>;
  if ("__TAURI_INTERNALS__" in w) delete w["__TAURI_INTERNALS__"];
  // Reset document.documentElement.style.zoom
  try { document.documentElement.style.zoom = ""; } catch { /* ignore */ }
});

afterEach(() => {
  fakeStorage.clear();
  vi.restoreAllMocks();
  const w = globalThis as Record<string, unknown>;
  if ("__TAURI_INTERNALS__" in w) delete w["__TAURI_INTERNALS__"];
  try { document.documentElement.style.zoom = ""; } catch { /* ignore */ }
});

// ─── 1. getKnownServers — dedupe / cap / order ────────────────────────────────

describe("getKnownServers() — dedupe, cap, order (ADR-0048 §T4a)", () => {
  it("returns [] when localStorage is empty", () => {
    expect(getKnownServers()).toEqual([]);
  });

  it("returns [] when localStorage has invalid JSON", () => {
    fakeStorage.setItem(LS_SERVERS, "not-json");
    expect(getKnownServers()).toEqual([]);
  });

  it("returns [] when localStorage holds non-array JSON", () => {
    fakeStorage.setItem(LS_SERVERS, JSON.stringify({ a: 1 }));
    expect(getKnownServers()).toEqual([]);
  });

  it("appends a server after successful setServerUrl", () => {
    setServerUrl("http://truenas:8000");
    const list = getKnownServers();
    expect(list).toContain("http://truenas:8000");
  });

  it("keeps most-recent server first", () => {
    setServerUrl("http://server-a:8000");
    setServerUrl("http://server-b:9000");
    const list = getKnownServers();
    expect(list[0]).toBe("http://server-b:9000");
    expect(list[1]).toBe("http://server-a:8000");
  });

  it("dedupes case-insensitively — uppercase duplicate is removed", () => {
    setServerUrl("http://truenas:8000");
    setServerUrl("http://TRUENAS:8000");
    const list = getKnownServers();
    // Only one entry for truenas:8000 — the most-recent lowercase-normalised form
    const truenasEntries = list.filter(
      (s) => s.toLowerCase() === "http://truenas:8000",
    );
    expect(truenasEntries).toHaveLength(1);
  });

  it("dedupes the same URL re-added — moves it to front", () => {
    setServerUrl("http://server-a:8000");
    setServerUrl("http://server-b:9000");
    setServerUrl("http://server-a:8000"); // re-add A
    const list = getKnownServers();
    expect(list[0]).toBe("http://server-a:8000");
    // server-b still present but no longer first
    expect(list).toContain("http://server-b:9000");
    // Only one entry for server-a
    expect(list.filter((s) => s === "http://server-a:8000")).toHaveLength(1);
  });

  it("caps the list at 5 entries", () => {
    const urls = [
      "http://s1:8000",
      "http://s2:8000",
      "http://s3:8000",
      "http://s4:8000",
      "http://s5:8000",
      "http://s6:8000",
    ];
    for (const url of urls) setServerUrl(url);
    const list = getKnownServers();
    expect(list).toHaveLength(5);
  });

  it("the oldest entry is evicted when the cap is exceeded", () => {
    const urls = [
      "http://s1:8000",
      "http://s2:8000",
      "http://s3:8000",
      "http://s4:8000",
      "http://s5:8000",
      "http://s6:8000",
    ];
    for (const url of urls) setServerUrl(url);
    const list = getKnownServers();
    // s1 was added first → it is the oldest → should be evicted
    expect(list).not.toContain("http://s1:8000");
    // s6 was added last → it should be first
    expect(list[0]).toBe("http://s6:8000");
  });

  it("does NOT add a server that fails validation (invalid scheme)", () => {
    try { setServerUrl("ftp://bad"); } catch { /* expected */ }
    expect(getKnownServers()).toEqual([]);
    expect(fakeStorage.getItem(LS_SERVERS)).toBeNull();
  });

  it("clearServerUrl does NOT remove the known-servers list", () => {
    setServerUrl("http://truenas:8000");
    clearServerUrl();
    // The known-servers list must survive clearServerUrl (it is the history, not active state)
    expect(getKnownServers()).toContain("http://truenas:8000");
  });
});

// ─── 2. useDesktopZoom — clamps, persists, restores, Tauri-gate ───────────────

describe("useDesktopZoom() — Tauri-gated zoom hook (ADR-0048 §T4b)", () => {
  /** Simulate Tauri presence via window.__TAURI_INTERNALS__ = {} */
  function enableTauri() {
    (globalThis as Record<string, unknown>)["__TAURI_INTERNALS__"] = {};
  }

  /** Fire a Cmd/Ctrl+key keydown event. */
  function dispatchZoomKey(key: string, ctrl = true) {
    window.dispatchEvent(
      new KeyboardEvent("keydown", {
        key,
        ctrlKey: ctrl,
        metaKey: !ctrl,
        bubbles: true,
      }),
    );
  }

  it("is a no-op when not in Tauri — zoom stays default", () => {
    // __TAURI_INTERNALS__ not set → isTauri() returns false
    renderHook(() => useDesktopZoom());
    dispatchZoomKey("=");
    expect(document.documentElement.style.zoom).toBe("");
  });

  it("applies persisted zoom on mount when in Tauri", () => {
    enableTauri();
    fakeStorage.setItem(LS_ZOOM, "1.2");
    renderHook(() => useDesktopZoom());
    expect(document.documentElement.style.zoom).toBe("1.2");
  });

  it("increases zoom by 0.1 on Ctrl+=", () => {
    enableTauri();
    renderHook(() => useDesktopZoom());
    // Start at default (1.0)
    act(() => dispatchZoomKey("="));
    expect(document.documentElement.style.zoom).toBe("1.1");
  });

  it("decreases zoom by 0.1 on Ctrl+-", () => {
    enableTauri();
    renderHook(() => useDesktopZoom());
    act(() => dispatchZoomKey("-"));
    expect(document.documentElement.style.zoom).toBe("0.9");
  });

  it("resets zoom to default (clears style) on Ctrl+0", () => {
    enableTauri();
    // Set a non-default zoom first
    fakeStorage.setItem(LS_ZOOM, "1.2");
    renderHook(() => useDesktopZoom());
    act(() => dispatchZoomKey("0"));
    // Default zoom → empty string (not "1")
    expect(document.documentElement.style.zoom).toBe("");
  });

  it("clamps at ZOOM_MAX (1.4)", () => {
    enableTauri();
    fakeStorage.setItem(LS_ZOOM, "1.4");
    renderHook(() => useDesktopZoom());
    act(() => dispatchZoomKey("="));
    expect(document.documentElement.style.zoom).toBe("1.4");
  });

  it("clamps at ZOOM_MIN (0.8)", () => {
    enableTauri();
    fakeStorage.setItem(LS_ZOOM, "0.8");
    renderHook(() => useDesktopZoom());
    act(() => dispatchZoomKey("-"));
    expect(document.documentElement.style.zoom).toBe("0.8");
  });

  it("persists the zoom value to localStorage on keydown", () => {
    enableTauri();
    renderHook(() => useDesktopZoom());
    act(() => dispatchZoomKey("="));
    expect(fakeStorage.getItem(LS_ZOOM)).toBe("1.1");
  });

  it("removes the zoom key from localStorage on reset (Ctrl+0)", () => {
    enableTauri();
    fakeStorage.setItem(LS_ZOOM, "1.3");
    renderHook(() => useDesktopZoom());
    act(() => dispatchZoomKey("0"));
    expect(fakeStorage.getItem(LS_ZOOM)).toBeNull();
  });

  it("also responds to Ctrl++ (plus key)", () => {
    enableTauri();
    renderHook(() => useDesktopZoom());
    act(() => dispatchZoomKey("+"));
    expect(document.documentElement.style.zoom).toBe("1.1");
  });

  it("does NOT respond to bare + key (no modifier)", () => {
    enableTauri();
    renderHook(() => useDesktopZoom());
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "=", bubbles: true }));
    expect(document.documentElement.style.zoom).toBe("");
  });
});
