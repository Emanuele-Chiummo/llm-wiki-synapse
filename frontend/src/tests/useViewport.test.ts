/**
 * useViewport.test.ts — ADR-0057 §2: useViewport hook tier-mapping tests.
 *
 * Mocks window.matchMedia to simulate different viewport widths and verifies
 * that useViewport returns the correct tier ("mobile" | "tablet" | "desktop").
 *
 * The global setup.ts stubs matchMedia with matches=false.
 * These tests override that stub per-test to simulate specific breakpoints.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// ─── Helper to mock matchMedia ────────────────────────────────────────────────

type ChangeListener = (e: MediaQueryListEvent) => void;

function mockMatchMedia(mobileMatches: boolean, tabletMatches: boolean) {
  const listeners = new Map<string, ChangeListener[]>();

  const makeQuery = (query: string, matches: boolean) => ({
    matches,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn((_event: string, handler: ChangeListener) => {
      if (!listeners.has(query)) listeners.set(query, []);
      listeners.get(query)!.push(handler);
    }),
    removeEventListener: vi.fn((_event: string, handler: ChangeListener) => {
      const arr = listeners.get(query) ?? [];
      const idx = arr.indexOf(handler);
      if (idx >= 0) arr.splice(idx, 1);
    }),
    dispatchEvent: vi.fn(),
  });

  const mobileQuery = "(max-width: 767px)";
  const tabletQuery = "(min-width: 768px) and (max-width: 1023px)";

  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => {
      if (query === mobileQuery) return makeQuery(query, mobileMatches);
      if (query === tabletQuery) return makeQuery(query, tabletMatches);
      return makeQuery(query, false);
    },
  });
}

afterEach(() => {
  // Restore the global stub from setup.ts (matches always false)
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
});

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("useViewport — tier mapping (ADR-0057 §2)", () => {
  it("returns 'desktop' when matchMedia is not available", async () => {
    // The default setup.ts stub returns matches=false for both queries → desktop
    const { useViewport } = await import("../hooks/useViewport");
    const { result } = renderHook(() => useViewport());
    expect(result.current).toBe("desktop");
  });

  it("returns 'mobile' when mobile query matches", async () => {
    mockMatchMedia(true, false);
    // Dynamic import to pick up fresh module after mocking
    vi.resetModules();
    const { useViewport } = await import("../hooks/useViewport");
    const { result } = renderHook(() => useViewport());
    act(() => {}); // flush
    expect(result.current).toBe("mobile");
  });

  it("returns 'tablet' when tablet query matches", async () => {
    mockMatchMedia(false, true);
    vi.resetModules();
    const { useViewport } = await import("../hooks/useViewport");
    const { result } = renderHook(() => useViewport());
    act(() => {});
    expect(result.current).toBe("tablet");
  });

  it("returns 'desktop' when neither query matches", async () => {
    mockMatchMedia(false, false);
    vi.resetModules();
    const { useViewport } = await import("../hooks/useViewport");
    const { result } = renderHook(() => useViewport());
    act(() => {});
    expect(result.current).toBe("desktop");
  });
});

describe("viewport.ts constants", () => {
  it("MOBILE_MAX is 767", async () => {
    const { MOBILE_MAX } = await import("../utils/viewport");
    expect(MOBILE_MAX).toBe(767);
  });

  it("TABLET_MAX is 1023", async () => {
    const { TABLET_MAX } = await import("../utils/viewport");
    expect(TABLET_MAX).toBe(1023);
  });

  it("MOBILE_QUERY uses MOBILE_MAX", async () => {
    const { MOBILE_QUERY, MOBILE_MAX } = await import("../utils/viewport");
    expect(MOBILE_QUERY).toContain(String(MOBILE_MAX));
  });

  it("TABLET_QUERY spans MOBILE_MAX+1 to TABLET_MAX", async () => {
    const { TABLET_QUERY, MOBILE_MAX, TABLET_MAX } = await import("../utils/viewport");
    expect(TABLET_QUERY).toContain(String(MOBILE_MAX + 1));
    expect(TABLET_QUERY).toContain(String(TABLET_MAX));
  });
});
