/**
 * vaultMetaClient.test.ts — unit tests for fetchVaultMeta (WS-D8 / K1 / I5).
 *
 * Covers:
 *   - Successful 200 response → returns parsed VaultMetaResponse.
 *   - 404 response → returns { files: [] } (graceful degradation, endpoint not yet deployed).
 *   - 500 server error → throws ApiError.
 *   - Malformed JSON → returns { files: [] } (graceful).
 *   - AbortError propagates to caller.
 *   - vault_id is correctly URL-encoded in the request URL.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fetchVaultMeta } from "../api/vaultMetaClient";
import type { VaultMetaResponse } from "../api/vaultMetaClient";

// ─── Mock fetchWithTimeout ────────────────────────────────────────────────────
// We mock the http module so the tests don't need a real server.

vi.mock("../api/http", () => ({
  fetchWithTimeout: vi.fn(),
  DEFAULT_REQUEST_TIMEOUT_MS: 10_000,
  ApiTimeoutError: class ApiTimeoutError extends Error {
    constructor(ms: number) {
      super(`timed out after ${ms}ms`);
    }
  },
}));

// Also mock base so apiBase() returns a predictable value.
vi.mock("../api/base", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/base")>();
  return {
    ...actual,
    apiBase: () => "http://test-server",
    apiFetch: vi.fn(),
    authHeaders: () => ({}),
  };
});

import { fetchWithTimeout } from "../api/http";

const mockFetch = fetchWithTimeout as ReturnType<typeof vi.fn>;

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeMalformedResponse(): Response {
  return new Response("not-json{{{", {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const META_RESPONSE: VaultMetaResponse = {
  files: [
    {
      name: "schema.md",
      path: "schema.md",
      title: "Schema",
      content: "# Schema\nThis is the schema.",
    },
    {
      name: "purpose.md",
      path: "purpose.md",
      title: "Purpose",
      content: "# Purpose\nThis is the purpose.",
    },
  ],
};

// ─── Tests ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("fetchVaultMeta — success", () => {
  it("returns parsed VaultMetaResponse on 200", async () => {
    mockFetch.mockResolvedValueOnce(makeJsonResponse(META_RESPONSE));

    const result = await fetchVaultMeta("default");
    expect(result.files).toHaveLength(2);
    expect(result.files[0]?.name).toBe("schema.md");
    expect(result.files[1]?.name).toBe("purpose.md");
  });

  it("includes content in each file entry", async () => {
    mockFetch.mockResolvedValueOnce(makeJsonResponse(META_RESPONSE));

    const result = await fetchVaultMeta("default");
    expect(result.files[0]?.content).toContain("Schema");
    expect(result.files[1]?.content).toContain("Purpose");
  });

  it("URL-encodes the vault_id parameter", async () => {
    mockFetch.mockResolvedValueOnce(makeJsonResponse({ files: [] }));

    await fetchVaultMeta("my vault/id");
    const callUrl = mockFetch.mock.calls[0]?.[0] as string;
    expect(callUrl).toContain("vault_id=my%20vault%2Fid");
  });

  it("passes the AbortSignal to fetchWithTimeout", async () => {
    mockFetch.mockResolvedValueOnce(makeJsonResponse({ files: [] }));
    const ctrl = new AbortController();

    await fetchVaultMeta("default", ctrl.signal);
    const callInit = mockFetch.mock.calls[0]?.[1] as { signal?: AbortSignal } | undefined;
    expect(callInit?.signal).toBe(ctrl.signal);
  });
});

describe("fetchVaultMeta — graceful degradation", () => {
  it("returns { files: [] } on 404 (endpoint not yet deployed)", async () => {
    mockFetch.mockResolvedValueOnce(new Response("Not Found", { status: 404 }));

    const result = await fetchVaultMeta("default");
    expect(result.files).toEqual([]);
  });

  it("returns { files: [] } when response JSON is malformed", async () => {
    mockFetch.mockResolvedValueOnce(makeMalformedResponse());

    const result = await fetchVaultMeta("default");
    expect(result.files).toEqual([]);
  });

  it("returns { files: [] } when files array is empty", async () => {
    mockFetch.mockResolvedValueOnce(makeJsonResponse({ files: [] }));

    const result = await fetchVaultMeta("default");
    expect(result.files).toEqual([]);
  });
});

describe("fetchVaultMeta — error propagation", () => {
  it("throws ApiError on 500 server error", async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: { code: "internal_error", message: "internal error", status: 500, details: null },
        }),
        { status: 500 },
      ),
    );

    await expect(fetchVaultMeta("default")).rejects.toMatchObject({
      status: 500,
    });
  });

  it("propagates AbortError from fetchWithTimeout", async () => {
    const abortErr = Object.assign(new Error("aborted"), { name: "AbortError" });
    mockFetch.mockRejectedValueOnce(abortErr);

    await expect(fetchVaultMeta("default")).rejects.toMatchObject({
      name: "AbortError",
    });
  });

  it("propagates network error from fetchWithTimeout", async () => {
    mockFetch.mockRejectedValueOnce(new Error("Failed to fetch"));

    await expect(fetchVaultMeta("default")).rejects.toThrow("Failed to fetch");
  });
});

describe("fetchVaultMeta — default vault_id", () => {
  it("defaults to 'default' when vaultId is omitted", async () => {
    mockFetch.mockResolvedValueOnce(makeJsonResponse({ files: [] }));

    await fetchVaultMeta();
    const callUrl = mockFetch.mock.calls[0]?.[0] as string;
    expect(callUrl).toContain("vault_id=default");
  });
});
