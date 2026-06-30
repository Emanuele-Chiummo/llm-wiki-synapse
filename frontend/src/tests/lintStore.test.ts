/**
 * lintStore.test.ts — Zustand store unit tests for K2 lint (ADR-0037 §6).
 *
 * Covers:
 *   - scan: replaces findings + sets currentRun + prepends run to history
 *   - scan: error state
 *   - scan: AbortError ignored
 *   - apply: removes finding on success (optimistic)
 *   - apply: 409 (already applied/dismissed) → actionError set, finding stays
 *   - dismiss: removes finding on success (optimistic)
 *   - dismiss: error → actionError set, finding stays
 *   - refresh: replaces findings
 *   - fetchMoreFindings: appends findings
 *   - fetchRuns: loads run history
 *   - clear helpers
 *
 * All network calls are mocked via vi.mock — no real fetch.
 * INVARIANT I3: store selectors tested independently.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { useLintStore } from "../store/lintStore";
import type { LintFinding, LintRun, LintScanResponse } from "../api/types";
import { ApiError } from "../api/graphClient";

// ─── Mock API client ──────────────────────────────────────────────────────────

vi.mock("../api/lintClient", () => ({
  runLintScan: vi.fn(),
  fetchLintRuns: vi.fn(),
  fetchLintRun: vi.fn(),
  fetchLintFindings: vi.fn(),
  applyLintFinding: vi.fn(),
  dismissLintFinding: vi.fn(),
}));

import * as lintClient from "../api/lintClient";

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

// ─── Reset store state between tests ─────────────────────────────────────────

beforeEach(() => {
  useLintStore.setState({
    findings: [],
    findingsTotal: 0,
    findingsOffset: 0,
    findingsLoading: false,
    findingsError: null,
    runs: [],
    runsTotal: 0,
    runsLoading: false,
    runsError: null,
    currentRun: null,
    scanning: false,
    scanError: null,
    actionInFlight: {},
    actionError: {},
  });
  vi.clearAllMocks();
});

// ─── scan ─────────────────────────────────────────────────────────────────────

describe("lintStore — scan", () => {
  it("replaces findings, sets currentRun, prepends to run history on success", async () => {
    const run = makeLintRun({ id: "run-new" });
    const findings = [makeFinding("f1"), makeFinding("f2")];
    const resp: LintScanResponse = { run, findings };
    vi.mocked(lintClient.runLintScan).mockResolvedValueOnce(resp);

    await useLintStore.getState().scan("default");

    const state = useLintStore.getState();
    expect(state.scanning).toBe(false);
    expect(state.scanError).toBeNull();
    expect(state.currentRun?.id).toBe("run-new");
    expect(state.findings).toHaveLength(2);
    expect(state.findingsTotal).toBe(2);
    expect(state.runs[0]?.id).toBe("run-new");
  });

  it("sets scanError on failure", async () => {
    vi.mocked(lintClient.runLintScan).mockRejectedValueOnce(
      new Error("Provider not configured"),
    );

    await useLintStore.getState().scan("default");

    const state = useLintStore.getState();
    expect(state.scanning).toBe(false);
    expect(state.scanError).toBe("Provider not configured");
    expect(state.findings).toHaveLength(0);
  });

  it("ignores AbortError and does not set scanError", async () => {
    const abortErr = Object.assign(new Error("AbortError"), { name: "AbortError" });
    vi.mocked(lintClient.runLintScan).mockRejectedValueOnce(abortErr);

    await useLintStore.getState().scan("default");

    expect(useLintStore.getState().scanError).toBeNull();
    expect(useLintStore.getState().scanning).toBe(false);
  });

  it("deduplicates run in history when same run id returned again", async () => {
    const run = makeLintRun({ id: "run-existing" });
    useLintStore.setState({ runs: [run], runsTotal: 1 });

    const updatedRun = makeLintRun({ id: "run-existing", findings_count: 5 });
    vi.mocked(lintClient.runLintScan).mockResolvedValueOnce({
      run: updatedRun,
      findings: [],
    });

    await useLintStore.getState().scan("default");

    // Should not duplicate
    expect(useLintStore.getState().runs).toHaveLength(1);
  });
});

// ─── apply ────────────────────────────────────────────────────────────────────

describe("lintStore — apply", () => {
  it("removes finding from list on success (optimistic)", async () => {
    useLintStore.setState({
      findings: [makeFinding("f1"), makeFinding("f2")],
      findingsTotal: 2,
    });
    vi.mocked(lintClient.applyLintFinding).mockResolvedValueOnce(
      makeFinding("f1", { status: "applied" }),
    );

    await useLintStore.getState().apply("f1");

    const state = useLintStore.getState();
    expect(state.findings).toHaveLength(1);
    expect(state.findings[0]?.id).toBe("f2");
    expect(state.findingsTotal).toBe(1);
    expect(state.actionInFlight["f1"]).toBeNull();
    expect(state.actionError["f1"]).toBeFalsy();
  });

  it("sets actionError on 409 (already applied/dismissed) and keeps finding in list", async () => {
    useLintStore.setState({ findings: [makeFinding("f1")], findingsTotal: 1 });
    vi.mocked(lintClient.applyLintFinding).mockRejectedValueOnce(
      new ApiError(409, "409 finding is not open"),
    );

    await useLintStore.getState().apply("f1");

    const state = useLintStore.getState();
    expect(state.findings).toHaveLength(1); // not removed
    expect(state.actionError["f1"]).toBeTruthy();
    expect(state.actionInFlight["f1"]).toBeNull();
  });

  it("sets actionError on generic failure and keeps finding in list", async () => {
    useLintStore.setState({ findings: [makeFinding("f2")], findingsTotal: 1 });
    vi.mocked(lintClient.applyLintFinding).mockRejectedValueOnce(
      new Error("500 Internal Server Error"),
    );

    await useLintStore.getState().apply("f2");

    const state = useLintStore.getState();
    expect(state.findings).toHaveLength(1);
    expect(state.actionError["f2"]).toBeTruthy();
  });
});

// ─── dismiss ──────────────────────────────────────────────────────────────────

describe("lintStore — dismiss", () => {
  it("removes finding from list on success", async () => {
    useLintStore.setState({
      findings: [makeFinding("f1"), makeFinding("f2")],
      findingsTotal: 2,
    });
    vi.mocked(lintClient.dismissLintFinding).mockResolvedValueOnce(
      makeFinding("f1", { status: "dismissed" }),
    );

    await useLintStore.getState().dismiss("f1");

    const state = useLintStore.getState();
    expect(state.findings).toHaveLength(1);
    expect(state.findings[0]?.id).toBe("f2");
    expect(state.findingsTotal).toBe(1);
  });

  it("sets actionError on failure and keeps finding in list", async () => {
    useLintStore.setState({ findings: [makeFinding("f1")], findingsTotal: 1 });
    vi.mocked(lintClient.dismissLintFinding).mockRejectedValueOnce(
      new Error("404 Not Found"),
    );

    await useLintStore.getState().dismiss("f1");

    expect(useLintStore.getState().findings).toHaveLength(1);
    expect(useLintStore.getState().actionError["f1"]).toBeTruthy();
  });
});

// ─── refresh ──────────────────────────────────────────────────────────────────

describe("lintStore — refresh", () => {
  it("replaces findings on success", async () => {
    useLintStore.setState({ findings: [makeFinding("old")], findingsTotal: 1 });
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValueOnce({
      items: [makeFinding("new1"), makeFinding("new2")],
      total: 2,
      limit: 50,
      offset: 0,
    });

    await useLintStore.getState().refresh("default");

    const state = useLintStore.getState();
    expect(state.findings).toHaveLength(2);
    expect(state.findings[0]?.id).toBe("new1");
    expect(state.findingsTotal).toBe(2);
    expect(state.findingsLoading).toBe(false);
    expect(state.findingsError).toBeNull();
  });

  it("sets findingsError on failure", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockRejectedValueOnce(
      new Error("Backend unavailable"),
    );

    await useLintStore.getState().refresh("default");

    const state = useLintStore.getState();
    expect(state.findingsError).toBe("Backend unavailable");
    expect(state.findingsLoading).toBe(false);
  });

  it("ignores AbortError", async () => {
    const abortErr = Object.assign(new Error("AbortError"), { name: "AbortError" });
    vi.mocked(lintClient.fetchLintFindings).mockRejectedValueOnce(abortErr);

    await useLintStore.getState().refresh("default");

    expect(useLintStore.getState().findingsError).toBeNull();
  });
});

// ─── fetchMoreFindings ────────────────────────────────────────────────────────

describe("lintStore — fetchMoreFindings", () => {
  it("appends findings and increments offset", async () => {
    const initial = [makeFinding("f1"), makeFinding("f2")];
    useLintStore.setState({ findings: initial, findingsTotal: 4, findingsOffset: 0 });
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValueOnce({
      items: [makeFinding("f3"), makeFinding("f4")],
      total: 4,
      limit: 50,
      offset: 50,
    });

    await useLintStore.getState().fetchMoreFindings("default");

    const state = useLintStore.getState();
    expect(state.findings).toHaveLength(4);
    expect(state.findingsOffset).toBe(50);
  });

  it("does nothing when all items already loaded", async () => {
    useLintStore.setState({
      findings: [makeFinding("f1")],
      findingsTotal: 1,
      findingsOffset: 0,
    });

    await useLintStore.getState().fetchMoreFindings("default");

    expect(lintClient.fetchLintFindings).not.toHaveBeenCalled();
  });
});

// ─── fetchRuns ────────────────────────────────────────────────────────────────

describe("lintStore — fetchRuns", () => {
  it("loads run history on success", async () => {
    const runs = [makeLintRun({ id: "r1" }), makeLintRun({ id: "r2" })];
    vi.mocked(lintClient.fetchLintRuns).mockResolvedValueOnce({
      items: runs,
      total: 2,
      limit: 20,
      offset: 0,
    });

    await useLintStore.getState().fetchRuns("default");

    const state = useLintStore.getState();
    expect(state.runs).toHaveLength(2);
    expect(state.runsTotal).toBe(2);
    expect(state.runsLoading).toBe(false);
    expect(state.runsError).toBeNull();
  });

  it("sets runsError on failure", async () => {
    vi.mocked(lintClient.fetchLintRuns).mockRejectedValueOnce(
      new Error("Network error"),
    );

    await useLintStore.getState().fetchRuns("default");

    expect(useLintStore.getState().runsError).toBe("Network error");
    expect(useLintStore.getState().runsLoading).toBe(false);
  });
});

// ─── clear helpers ────────────────────────────────────────────────────────────

describe("lintStore — clear helpers", () => {
  it("clearScanError clears the error", () => {
    useLintStore.setState({ scanError: "some scan error" });
    useLintStore.getState().clearScanError();
    expect(useLintStore.getState().scanError).toBeNull();
  });

  it("clearActionError clears per-finding error", () => {
    useLintStore.setState({ actionError: { "f1": "apply failed" } });
    useLintStore.getState().clearActionError("f1");
    expect(useLintStore.getState().actionError["f1"]).toBeFalsy();
  });
});
