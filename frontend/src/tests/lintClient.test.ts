/**
 * lintClient.test.ts — unit tests for the K2 lint API client (ADR-0037 §6).
 *
 * Covers:
 *   - runLintScan: POST to /lint/scan with vault_id; returns run + findings
 *   - fetchLintRuns: GET with correct query params
 *   - fetchLintRun: GET single run by id
 *   - fetchLintFindings: GET with vault_id + status filter
 *   - applyLintFinding: POST to /lint/findings/{id}/apply
 *   - dismissLintFinding: POST to /lint/findings/{id}/dismiss
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
