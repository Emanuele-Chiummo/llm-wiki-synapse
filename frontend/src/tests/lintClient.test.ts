/**
 * lintClient.test.ts — unit tests for the K2 lint API client (ADR-0037 §6, B1).
 *
 * Covers:
 *   - runLintScan: POST to /lint/scan with vault_id + semantic param; returns run + findings
 *   - fetchLintRuns: GET with correct query params
 *   - fetchLintRun: GET single run by id
 *   - fetchLintFindings: GET with vault_id + status filter + optional category/severity [L10]
 *   - applyLintFinding: POST to /lint/findings/{id}/apply
 *   - dismissLintFinding: POST to /lint/findings/{id}/dismiss
 *   - batchLintAction: POST to /lint/findings/batch [L5]
 *   - sendLintFindingToReview: POST to /lint/findings/{id}/send-to-review [L6]
 *   - deleteWikiPage: DELETE to /pages/{page_id} [L9]
 *   - Error handling: non-ok responses throw ApiError with status
 *   - 409 on apply when finding already closed
 *
 * Mocks global fetch via vi.stubGlobal.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  runLintScan,
  fetchLintRuns,
  fetchLintRun,
  fetchLintFindings,
  applyLintFinding,
  dismissLintFinding,
  batchLintAction,
  sendLintFindingToReview,
  deleteWikiPage,
} from "../api/lintClient";
import type { LintRun, LintFinding, LintScanResponse } from "../api/types";
import { ApiError } from "../api/graphClient";

// Inline type for fetch init to avoid ESLint no-undef on the DOM global RequestInit
interface FetchInit {
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
  signal?: AbortSignal;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeLintRun(overrides: Partial<LintRun> = {}): LintRun {
  return {
    id: "run-1",
    vault_id: "default",
    status: "completed",
    max_iter: 3,
    token_budget: 20000,
    iterations_used: 2,
    findings_count: 2,
    total_cost_usd: 0.0042,
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    error_message: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function makeFinding(id: string, overrides: Partial<LintFinding> = {}): LintFinding {
  return {
    id,
    lint_run_id: "run-1",
    vault_id: "default",
    category: "missing-xref",
    severity: "warning",
    target_page_id: "page-abc",
    target_title: "Some Wiki Page",
    description: "A cross-reference to [[Missing Page]] exists but the target page does not.",
    proposed_action: "Create page: Missing Page",
    status: "open",
    resolution_note: null,
    created_at: new Date().toISOString(),
    reviewed_at: null,
    suggested_target: null,
    suggested_page_id: null,
    ...overrides,
  };
}

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : status === 201 ? "Created" : "Error",
    json: () => Promise.resolve(body),
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

// ─── runLintScan ──────────────────────────────────────────────────────────────

describe("lintClient — runLintScan", () => {
  it("POSTs to /lint/scan with vault_id in request body", async () => {
    const run = makeLintRun();
    const findings = [makeFinding("f1"), makeFinding("f2")];
    const resp: LintScanResponse = { run, findings };
    const fetchMock = mockFetch(resp);
    vi.stubGlobal("fetch", fetchMock);

    const result = await runLintScan({ vault_id: "default" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, FetchInit];
    expect(url).toContain("/lint/scan");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ "Content-Type": "application/json" });
    expect(JSON.parse(init.body as string)).toMatchObject({ vault_id: "default" });
    expect(result.run.id).toBe("run-1");
    expect(result.findings).toHaveLength(2);
  });

  it("includes semantic=true in URL by default [B1-L8]", async () => {
    const resp: LintScanResponse = { run: makeLintRun(), findings: [] };
    const fetchMock = mockFetch(resp);
    vi.stubGlobal("fetch", fetchMock);

    await runLintScan({ vault_id: "default" });

    const [url] = fetchMock.mock.calls[0] as [string, FetchInit];
    expect(url).toContain("semantic=true");
  });

  it("includes semantic=false when semantic=false passed [B1-L8]", async () => {
    const resp: LintScanResponse = { run: makeLintRun(), findings: [] };
    const fetchMock = mockFetch(resp);
    vi.stubGlobal("fetch", fetchMock);

    await runLintScan({ vault_id: "default" }, undefined, false);

    const [url] = fetchMock.mock.calls[0] as [string, FetchInit];
    expect(url).toContain("semantic=false");
  });

  it("includes optional max_iter and token_budget when provided", async () => {
    const resp: LintScanResponse = { run: makeLintRun(), findings: [] };
    const fetchMock = mockFetch(resp);
    vi.stubGlobal("fetch", fetchMock);

    await runLintScan({ vault_id: "default", max_iter: 5, token_budget: 50000 });

    const [, init] = fetchMock.mock.calls[0] as [string, FetchInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.max_iter).toBe(5);
    expect(body.token_budget).toBe(50000);
  });

  it("throws ApiError on non-ok response", async () => {
    const fetchMock = mockFetch({ detail: "No provider configured" }, 503);
    vi.stubGlobal("fetch", fetchMock);

    await expect(runLintScan({ vault_id: "default" })).rejects.toBeInstanceOf(ApiError);
  });

  it("throws ApiError with status 503 when provider not configured", async () => {
    const fetchMock = mockFetch({ detail: "No provider configured" }, 503);
    vi.stubGlobal("fetch", fetchMock);

    await expect(runLintScan({ vault_id: "default" })).rejects.toMatchObject({
      status: 503,
    });
  });
});

// ─── fetchLintRuns ────────────────────────────────────────────────────────────

describe("lintClient — fetchLintRuns", () => {
  it("calls GET /lint/runs with default limit and offset", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 20, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchLintRuns();

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("/lint/runs");
    expect(url).toContain("limit=20");
    expect(url).toContain("offset=0");
  });

  it("includes vault_id when provided", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 20, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchLintRuns({ vaultId: "my-vault" });

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("vault_id=my-vault");
  });

  it("returns the list response", async () => {
    const run = makeLintRun();
    const fetchMock = mockFetch({ items: [run], total: 1, limit: 20, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchLintRuns();
    expect(result.items).toHaveLength(1);
    expect(result.total).toBe(1);
    expect(result.items[0]?.id).toBe("run-1");
  });

  it("throws ApiError on non-ok response", async () => {
    const fetchMock = mockFetch({ detail: "Internal error" }, 500);
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchLintRuns()).rejects.toBeInstanceOf(ApiError);
  });
});

// ─── fetchLintRun ─────────────────────────────────────────────────────────────

describe("lintClient — fetchLintRun", () => {
  it("calls GET /lint/runs/{id}", async () => {
    const run = makeLintRun({ id: "run-xyz" });
    const fetchMock = mockFetch(run);
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchLintRun("run-xyz");

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("/lint/runs/run-xyz");
    expect(result.id).toBe("run-xyz");
  });

  it("throws ApiError with status 404 when run not found", async () => {
    const fetchMock = mockFetch({ detail: "Lint run not found" }, 404);
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchLintRun("unknown")).rejects.toMatchObject({ status: 404 });
  });
});

// ─── fetchLintFindings ────────────────────────────────────────────────────────

describe("lintClient — fetchLintFindings", () => {
  it("calls GET /lint/findings with vault_id, status, limit, offset", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchLintFindings({ vaultId: "default" });

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("/lint/findings");
    expect(url).toContain("vault_id=default");
    expect(url).toContain("status=open");
    expect(url).toContain("limit=50");
    expect(url).toContain("offset=0");
  });

  it("supports status filter override", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchLintFindings({ vaultId: "default", status: "dismissed" });

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("status=dismissed");
  });

  it("returns the findings list response", async () => {
    const findings = [makeFinding("f1"), makeFinding("f2")];
    const fetchMock = mockFetch({ items: findings, total: 2, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchLintFindings({ vaultId: "default" });
    expect(result.items).toHaveLength(2);
    expect(result.total).toBe(2);
  });

  it("findings carry category and proposed_action fields", async () => {
    const finding = makeFinding("f1", {
      category: "missing-xref",
      proposed_action: "Create page: Missing Page",
    });
    const fetchMock = mockFetch({ items: [finding], total: 1, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchLintFindings({ vaultId: "default" });
    const first = result.items[0]!;
    expect(first.category).toBe("missing-xref");
    expect(first.proposed_action).toBe("Create page: Missing Page");
  });

  it("flag-only categories have null proposed_action", async () => {
    const finding = makeFinding("f1", {
      category: "orphan-page",
      proposed_action: null,
    });
    const fetchMock = mockFetch({ items: [finding], total: 1, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchLintFindings({ vaultId: "default" });
    expect(result.items[0]?.proposed_action).toBeNull();
  });

  it("includes category filter when provided [B1-L10]", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchLintFindings({ vaultId: "default", category: "broken-wikilink" });

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("category=broken-wikilink");
  });

  it("includes severity filter when provided [B1-L10]", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchLintFindings({ vaultId: "default", severity: "error" });

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("severity=error");
  });

  it("does not include category param when not provided", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchLintFindings({ vaultId: "default" });

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).not.toContain("category=");
  });
});

// ─── applyLintFinding ─────────────────────────────────────────────────────────

describe("lintClient — applyLintFinding", () => {
  it("POSTs to /lint/findings/{id}/apply", async () => {
    const finding = makeFinding("f1", { status: "applied" });
    const fetchMock = mockFetch(finding);
    vi.stubGlobal("fetch", fetchMock);

    const result = await applyLintFinding("f1");

    const [url, init] = fetchMock.mock.calls[0] as [string, FetchInit];
    expect(url).toContain("/lint/findings/f1/apply");
    expect(init.method).toBe("POST");
    expect(result.status).toBe("applied");
  });

  it("throws ApiError with status 409 when finding already applied/dismissed", async () => {
    const fetchMock = mockFetch({ detail: "finding is not open" }, 409);
    vi.stubGlobal("fetch", fetchMock);

    await expect(applyLintFinding("f1")).rejects.toMatchObject({ status: 409 });
  });

  it("throws ApiError on generic server error", async () => {
    const fetchMock = mockFetch({ detail: "Unexpected error" }, 500);
    vi.stubGlobal("fetch", fetchMock);

    await expect(applyLintFinding("f1")).rejects.toBeInstanceOf(ApiError);
  });
});

// ─── dismissLintFinding ───────────────────────────────────────────────────────

describe("lintClient — dismissLintFinding", () => {
  it("POSTs to /lint/findings/{id}/dismiss", async () => {
    const finding = makeFinding("f2", { status: "dismissed" });
    const fetchMock = mockFetch(finding);
    vi.stubGlobal("fetch", fetchMock);

    const result = await dismissLintFinding("f2");

    const [url, init] = fetchMock.mock.calls[0] as [string, FetchInit];
    expect(url).toContain("/lint/findings/f2/dismiss");
    expect(init.method).toBe("POST");
    expect(result.status).toBe("dismissed");
  });

  it("throws ApiError on non-ok response", async () => {
    const fetchMock = mockFetch({ detail: "Not found" }, 404);
    vi.stubGlobal("fetch", fetchMock);

    await expect(dismissLintFinding("unknown")).rejects.toBeInstanceOf(ApiError);
  });
});

// ─── batchLintAction [B1-L5] ──────────────────────────────────────────────────

describe("lintClient — batchLintAction [B1-L5]", () => {
  it("POSTs to /lint/findings/batch with ids and action=apply", async () => {
    const fetchMock = mockFetch({
      results: [{ id: "f1", status: "ok" }, { id: "f2", status: "ok" }],
      ok_count: 2,
      error_count: 0,
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await batchLintAction(["f1", "f2"], "apply");

    const [url, init] = fetchMock.mock.calls[0] as [string, FetchInit];
    expect(url).toContain("/lint/findings/batch");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.ids).toEqual(expect.arrayContaining(["f1", "f2"]));
    expect(body.action).toBe("apply");
    expect(result.ok_count).toBe(2);
    expect(result.error_count).toBe(0);
  });

  it("POSTs action=dismiss", async () => {
    const fetchMock = mockFetch({ results: [], ok_count: 0, error_count: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await batchLintAction(["f1"], "dismiss");

    const [, init] = fetchMock.mock.calls[0] as [string, FetchInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.action).toBe("dismiss");
  });

  it("POSTs action=send-to-review", async () => {
    const fetchMock = mockFetch({ results: [], ok_count: 0, error_count: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await batchLintAction(["f1"], "send-to-review");

    const [, init] = fetchMock.mock.calls[0] as [string, FetchInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.action).toBe("send-to-review");
  });

  it("throws ApiError on non-ok response", async () => {
    const fetchMock = mockFetch({ detail: "Too many ids" }, 400);
    vi.stubGlobal("fetch", fetchMock);

    await expect(batchLintAction(["f1"], "apply")).rejects.toBeInstanceOf(ApiError);
  });

  it("splits selections >200 into ≤200-id chunks and merges the responses (I7 cap)", async () => {
    const ids = Array.from({ length: 450 }, (_, i) => `f${i}`);
    // Each chunk echoes an ok result per id so the merge is verifiable.
    const fetchMock = vi.fn().mockImplementation((_url: string, init: FetchInit) => {
      const { ids: chunkIds } = JSON.parse(init.body as string) as { ids: string[] };
      return Promise.resolve({
        ok: true,
        status: 200,
        statusText: "OK",
        json: () =>
          Promise.resolve({
            results: chunkIds.map((id) => ({ id, status: "ok" })),
            ok_count: chunkIds.length,
            error_count: 0,
          }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await batchLintAction(ids, "apply");

    // 450 ids → 200 + 200 + 50 = 3 requests, none exceeding the cap.
    expect(fetchMock).toHaveBeenCalledTimes(3);
    for (const call of fetchMock.mock.calls) {
      const [, init] = call as [string, FetchInit];
      const body = JSON.parse(init.body as string) as { ids: string[] };
      expect(body.ids.length).toBeLessThanOrEqual(200);
    }
    // Aggregate response covers every id exactly once.
    expect(result.ok_count).toBe(450);
    expect(result.error_count).toBe(0);
    expect(result.results).toHaveLength(450);
  });
});

// ─── sendLintFindingToReview [B1-L6] ─────────────────────────────────────────

describe("lintClient — sendLintFindingToReview [B1-L6]", () => {
  it("POSTs to /lint/findings/{id}/send-to-review", async () => {
    const finding = makeFinding("f1", { status: "applied" });
    const fetchMock = mockFetch(finding);
    vi.stubGlobal("fetch", fetchMock);

    const result = await sendLintFindingToReview("f1");

    const [url, init] = fetchMock.mock.calls[0] as [string, FetchInit];
    expect(url).toContain("/lint/findings/f1/send-to-review");
    expect(init.method).toBe("POST");
    expect(result.status).toBe("applied");
  });

  it("throws ApiError on non-ok response", async () => {
    const fetchMock = mockFetch({ detail: "Not found" }, 404);
    vi.stubGlobal("fetch", fetchMock);

    await expect(sendLintFindingToReview("unknown")).rejects.toBeInstanceOf(ApiError);
  });
});

// ─── deleteWikiPage [B1-L9] ───────────────────────────────────────────────────

describe("lintClient — deleteWikiPage [B1-L9]", () => {
  it("DELETEs /pages/{page_id}", async () => {
    const fetchMock = mockFetch({
      deleted_page_id: "page-1",
      wikilinks_cleaned: 3,
      index_entry_removed: true,
      shared_entity_warnings: [],
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteWikiPage("page-1");

    const [url, init] = fetchMock.mock.calls[0] as [string, FetchInit];
    expect(url).toContain("/pages/page-1");
    expect(init.method).toBe("DELETE");
    expect(result.deleted_page_id).toBe("page-1");
    expect(result.wikilinks_cleaned).toBe(3);
  });

  it("throws ApiError with status 404 when page not found", async () => {
    const fetchMock = mockFetch({ detail: "Page not found" }, 404);
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteWikiPage("nonexistent")).rejects.toMatchObject({ status: 404 });
  });

  it("throws ApiError on server error", async () => {
    const fetchMock = mockFetch({ detail: "Internal error" }, 500);
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteWikiPage("page-1")).rejects.toBeInstanceOf(ApiError);
  });
});
