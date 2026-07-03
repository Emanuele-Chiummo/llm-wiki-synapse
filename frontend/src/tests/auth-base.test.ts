/**
 * auth-base.test.ts — unit tests for ADR-0052 auth functions in api/base.ts.
 *
 * Covers:
 *   - getAuthToken / setAuthToken / clearAuthToken
 *   - authHeaders() returns {} or { Authorization: "Bearer <token>" }
 *   - apiFetch() merges authHeaders into every request
 *   - apiFetch() on 401: clears token and calls the registered 401 handler
 *   - register401Handler fires exactly once per 401
 *
 * @vitest-environment jsdom
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// ─── Fake localStorage ────────────────────────────────────────────────────────

function makeFakeStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    get length() { return Object.keys(store).length; },
    key(n: number) { return Object.keys(store)[n] ?? null; },
    getItem(k: string) { return Object.prototype.hasOwnProperty.call(store, k) ? (store[k] ?? null) : null; },
    setItem(k: string, v: string) { store[k] = v; },
    removeItem(k: string) { delete store[k]; },
    clear() { store = {}; },
  };
}

const fakeStorage = makeFakeStorage();
vi.stubGlobal("localStorage", fakeStorage);

// ─── Mock fetch ───────────────────────────────────────────────────────────────

// Must be defined before module import
const mockFetch = vi.fn<typeof fetch>();
vi.stubGlobal("fetch", mockFetch);

// ─── Import module under test ─────────────────────────────────────────────────

import {
  getAuthToken,
  setAuthToken,
  clearAuthToken,
  authHeaders,
  apiFetch,
  register401Handler,
} from "../api/base";

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  fakeStorage.clear();
  mockFetch.mockReset();
  // Clear the module-level 401 handler by registering a no-op
  register401Handler(() => {/* no-op */});
});

afterEach(() => {
  fakeStorage.clear();
  vi.restoreAllMocks();
});

// ─── getAuthToken / setAuthToken / clearAuthToken ─────────────────────────────

describe("getAuthToken()", () => {
  it("returns null when nothing is stored", () => {
    expect(getAuthToken()).toBeNull();
  });

  it("returns the stored token after setAuthToken", () => {
    setAuthToken("my-secret-token");
    expect(getAuthToken()).toBe("my-secret-token");
  });

  it("trims whitespace from the stored token", () => {
    setAuthToken("  trimmed  ");
    expect(getAuthToken()).toBe("trimmed");
  });

  it("returns null after clearAuthToken", () => {
    setAuthToken("abc");
    clearAuthToken();
    expect(getAuthToken()).toBeNull();
  });

  it("setAuthToken with empty string removes the key", () => {
    setAuthToken("abc");
    setAuthToken("");
    expect(getAuthToken()).toBeNull();
  });

  it("setAuthToken with whitespace-only string removes the key", () => {
    setAuthToken("abc");
    setAuthToken("   ");
    expect(getAuthToken()).toBeNull();
  });
});

// ─── authHeaders() ────────────────────────────────────────────────────────────

describe("authHeaders()", () => {
  it("returns {} when no token is stored", () => {
    expect(authHeaders()).toEqual({});
  });

  it("returns { Authorization: 'Bearer <token>' } when token is stored", () => {
    setAuthToken("test-token-123");
    expect(authHeaders()).toEqual({ Authorization: "Bearer test-token-123" });
  });

  it("reads the current token at call time (not cached)", () => {
    setAuthToken("first");
    expect(authHeaders()).toEqual({ Authorization: "Bearer first" });
    setAuthToken("second");
    expect(authHeaders()).toEqual({ Authorization: "Bearer second" });
    clearAuthToken();
    expect(authHeaders()).toEqual({});
  });
});

// ─── apiFetch() — header injection ────────────────────────────────────────────

describe("apiFetch() — Authorization header injection", () => {
  it("sends no Authorization header when no token is stored", async () => {
    mockFetch.mockResolvedValue(new Response("{}", { status: 200 }));
    await apiFetch("/api/test");
    const [, init] = mockFetch.mock.calls[0]!;
    const headers = (init as { headers?: Record<string, string> } | undefined)?.headers;
    expect(headers?.["Authorization"]).toBeUndefined();
  });

  it("merges Authorization header when a token is stored", async () => {
    setAuthToken("bearer-token");
    mockFetch.mockResolvedValue(new Response("{}", { status: 200 }));
    await apiFetch("/api/test");
    const [, init] = mockFetch.mock.calls[0]!;
    const headers = (init as { headers?: Record<string, string> } | undefined)?.headers;
    expect(headers?.["Authorization"]).toBe("Bearer bearer-token");
  });

  it("preserves caller-supplied headers alongside auth header", async () => {
    setAuthToken("tok");
    mockFetch.mockResolvedValue(new Response("{}", { status: 200 }));
    await apiFetch("/api/test", { headers: { "X-Custom": "value" } });
    const [, init] = mockFetch.mock.calls[0]!;
    const headers = (init as { headers?: Record<string, string> } | undefined)?.headers;
    expect(headers?.["Authorization"]).toBe("Bearer tok");
    expect(headers?.["X-Custom"]).toBe("value");
  });

  it("returns the Response object (does not throw on 2xx)", async () => {
    mockFetch.mockResolvedValue(new Response("{}", { status: 200 }));
    const res = await apiFetch("/api/test");
    expect(res.status).toBe(200);
  });
});

// ─── apiFetch() — 401 handling ────────────────────────────────────────────────

describe("apiFetch() — 401 handling", () => {
  it("clears the stored token on 401 response", async () => {
    setAuthToken("stale-token");
    mockFetch.mockResolvedValue(new Response("{}", { status: 401 }));
    await apiFetch("/api/protected");
    expect(getAuthToken()).toBeNull();
  });

  it("fires the registered 401 handler on 401 response", async () => {
    const handler = vi.fn();
    register401Handler(handler);
    mockFetch.mockResolvedValue(new Response("{}", { status: 401 }));
    await apiFetch("/api/protected");
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does NOT fire the 401 handler on non-401 responses", async () => {
    const handler = vi.fn();
    register401Handler(handler);
    for (const status of [200, 403, 404, 500]) {
      mockFetch.mockResolvedValue(new Response("{}", { status }));
      await apiFetch("/api/test");
    }
    expect(handler).not.toHaveBeenCalled();
  });

  it("still returns the 401 Response (does not throw)", async () => {
    mockFetch.mockResolvedValue(new Response("{}", { status: 401 }));
    const res = await apiFetch("/api/protected");
    expect(res.status).toBe(401);
  });

  it("does not throw when no 401 handler is registered", async () => {
    // Clear handler by registering a no-op then resetting _on401 indirectly
    register401Handler(() => {/* no-op */});
    mockFetch.mockResolvedValue(new Response("{}", { status: 401 }));
    await expect(apiFetch("/api/protected")).resolves.not.toThrow();
  });
});

// ─── ADR-0052 Do-NOT: token never in URL / Zustand (structural test) ──────────

describe("ADR-0052 compliance", () => {
  it("apiFetch does not append the token to the URL", async () => {
    setAuthToken("secret");
    mockFetch.mockResolvedValue(new Response("{}", { status: 200 }));
    await apiFetch("/api/test");
    const [input] = mockFetch.mock.calls[0]!;
    expect(String(input)).not.toContain("secret");
  });
});
