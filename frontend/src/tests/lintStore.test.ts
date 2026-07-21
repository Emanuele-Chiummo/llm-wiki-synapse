/**
 * lintStore.test.ts — Zustand store unit tests for K2 lint (ADR-0037 §6, B1).
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
 *   - B1: semanticEnabled persisted to localStorage
 *   - B1: toggleSelect / selectAll / clearSelection
 *   - B1: applyBatch / dismissBatch / sendToReviewBatch
 *   - B1: sendToReview single
 *   - B1: deleteOrphanPage
 *
 * All network calls are mocked via vi.mock — no real fetch.
 * INVARIANT I3: store selectors tested independently.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { useLintStore } from "../store/lintStore";
import type { LintFinding, LintRun } from "../api/types";
import { ApiError } from "../api/graphClient";

// ─── Mock API client ──────────────────────────────────────────────────────────

vi.mock("../api/lintClient", () => ({
  runLintScan: vi.fn(),
  startLintScan: vi.fn(),
  fetchLintRuns: vi.fn(),
  fetchLintRun: vi.fn(),
  fetchLintFindings: vi.fn(),
  applyLintFinding: vi.fn(),
  dismissLintFinding: vi.fn(),
  batchLintAction: vi.fn(),
  sendLintFindingToReview: vi.fn(),
  deleteWikiPage: vi.fn(),
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
    suggested_target: null,
    suggested_page_id: null,
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
    semanticEnabled: true,
    selectedIds: new Set<string>(),
    batchInFlight: false,
    batchError: null,
  });
  vi.clearAllMocks();
});

// ─── scan ─────────────────────────────────────────────────────────────────────

/**
 * Wire the background-scan happy path: POST /lint/scan/start returns a run_id, the first
 * poll of GET /lint/runs/{id} already reports a terminal status, then findings are loaded.
 * `runStates` lets a test simulate the run still being "running" for a few polls.
 */
function mockScanFlow(runStates: LintRun[], findings: LintFinding[] = []): void {
  const last = runStates[runStates.length - 1];
  vi.mocked(lintClient.startLintScan).mockResolvedValueOnce({
    run_id: last?.id ?? "run-1",
    status: "started",
    max_iter: 3,
    token_budget: 20000,
    semantic: true,
  });
  const fetchRun = vi.mocked(lintClient.fetchLintRun);
  for (const r of runStates) fetchRun.mockResolvedValueOnce(r);
  // Only a completed run loads findings. Queueing this unconditionally would leave an
  // unconsumed mockResolvedValueOnce behind for the error case — and vi.clearAllMocks()
  // clears call history but NOT the once-queue, so it would leak into the next test.
  if (last?.status === "completed") {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValueOnce({
      items: findings,
      total: findings.length,
      limit: 50,
      offset: 0,
    });
  }
}

describe("lintStore — scan", () => {
  it("replaces findings, sets currentRun, prepends to run history on success", async () => {
    const run = makeLintRun({ id: "run-new" });
    mockScanFlow([run], [makeFinding("f1"), makeFinding("f2")]);

    await useLintStore.getState().scan("default");

    const state = useLintStore.getState();
    expect(state.scanning).toBe(false);
    expect(state.scanError).toBeNull();
    expect(state.currentRun?.id).toBe("run-new");
    expect(state.findings).toHaveLength(2);
    expect(state.findingsTotal).toBe(2);
    expect(state.runs[0]?.id).toBe("run-new");
  });

  it("polls until the run leaves 'running' before loading findings", async () => {
    vi.useFakeTimers();
    try {
      mockScanFlow(
        [
          makeLintRun({ id: "run-slow", status: "running", completed_at: null }),
          makeLintRun({ id: "run-slow", status: "running", completed_at: null }),
          makeLintRun({ id: "run-slow", status: "completed" }),
        ],
        [makeFinding("f1")],
      );

      const done = useLintStore.getState().scan("default");
      await vi.runAllTimersAsync();
      await done;

      // Three polls happened; findings were only fetched after the terminal status.
      expect(lintClient.fetchLintRun).toHaveBeenCalledTimes(3);
      expect(lintClient.fetchLintFindings).toHaveBeenCalledTimes(1);
      expect(useLintStore.getState().currentRun?.status).toBe("completed");
      expect(useLintStore.getState().findings).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("surfaces a run that finished with status 'error'", async () => {
    mockScanFlow([
      makeLintRun({ id: "run-bad", status: "error", error_message: "Provider timeout" }),
    ]);

    await useLintStore.getState().scan("default");

    const state = useLintStore.getState();
    expect(state.scanning).toBe(false);
    expect(state.scanError).toBe("Provider timeout");
    // A failed run must not clear the findings list via a findings fetch.
    expect(lintClient.fetchLintFindings).not.toHaveBeenCalled();
  });

  it("sets scanError on failure", async () => {
    vi.mocked(lintClient.startLintScan).mockRejectedValueOnce(new Error("Provider not configured"));

    await useLintStore.getState().scan("default");

    const state = useLintStore.getState();
    expect(state.scanning).toBe(false);
    expect(state.scanError).toBe("Provider not configured");
    expect(state.findings).toHaveLength(0);
  });

  it("ignores AbortError and does not set scanError", async () => {
    const abortErr = Object.assign(new Error("AbortError"), { name: "AbortError" });
    vi.mocked(lintClient.startLintScan).mockRejectedValueOnce(abortErr);

    await useLintStore.getState().scan("default");

    expect(useLintStore.getState().scanError).toBeNull();
    expect(useLintStore.getState().scanning).toBe(false);
  });

  it("deduplicates run in history when same run id returned again", async () => {
    const run = makeLintRun({ id: "run-existing" });
    useLintStore.setState({ runs: [run], runsTotal: 1 });

    mockScanFlow([makeLintRun({ id: "run-existing", findings_count: 5 })]);

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
    vi.mocked(lintClient.dismissLintFinding).mockRejectedValueOnce(new Error("404 Not Found"));

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
    vi.mocked(lintClient.fetchLintFindings).mockRejectedValueOnce(new Error("Backend unavailable"));

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
    vi.mocked(lintClient.fetchLintRuns).mockRejectedValueOnce(new Error("Network error"));

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
    useLintStore.setState({ actionError: { f1: "apply failed" } });
    useLintStore.getState().clearActionError("f1");
    expect(useLintStore.getState().actionError["f1"]).toBeFalsy();
  });
});

// ─── B1: semanticEnabled ─────────────────────────────────────────────────────

describe("lintStore — semanticEnabled [B1-L8]", () => {
  it("defaults to true", () => {
    expect(useLintStore.getState().semanticEnabled).toBe(true);
  });

  it("setSemanticEnabled updates state", () => {
    useLintStore.getState().setSemanticEnabled(false);
    expect(useLintStore.getState().semanticEnabled).toBe(false);
  });

  it("setSemanticEnabled back to true", () => {
    useLintStore.setState({ semanticEnabled: false });
    useLintStore.getState().setSemanticEnabled(true);
    expect(useLintStore.getState().semanticEnabled).toBe(true);
  });

  it("scan passes semanticEnabled=false to startLintScan", async () => {
    useLintStore.setState({ semanticEnabled: false });
    mockScanFlow([makeLintRun()]);
    await useLintStore.getState().scan("default");
    expect(lintClient.startLintScan).toHaveBeenCalledWith(
      { vault_id: "default" },
      undefined,
      false,
    );
  });

  it("scan passes semanticEnabled=true to startLintScan", async () => {
    useLintStore.setState({ semanticEnabled: true });
    mockScanFlow([makeLintRun()]);
    await useLintStore.getState().scan("default");
    expect(lintClient.startLintScan).toHaveBeenCalledWith({ vault_id: "default" }, undefined, true);
  });
});

// ─── B1: selection ────────────────────────────────────────────────────────────

describe("lintStore — selection [B1-L5]", () => {
  it("toggleSelect adds a finding id to selectedIds", () => {
    useLintStore.getState().toggleSelect("f1");
    expect(useLintStore.getState().selectedIds.has("f1")).toBe(true);
  });

  it("toggleSelect removes an already-selected id", () => {
    useLintStore.setState({ selectedIds: new Set(["f1"]) });
    useLintStore.getState().toggleSelect("f1");
    expect(useLintStore.getState().selectedIds.has("f1")).toBe(false);
  });

  it("selectAll selects all currently-loaded finding ids", () => {
    useLintStore.setState({
      findings: [makeFinding("f1"), makeFinding("f2"), makeFinding("f3")],
    });
    useLintStore.getState().selectAll();
    const ids = useLintStore.getState().selectedIds;
    expect(ids.has("f1")).toBe(true);
    expect(ids.has("f2")).toBe(true);
    expect(ids.has("f3")).toBe(true);
    expect(ids.size).toBe(3);
  });

  it("clearSelection empties selectedIds", () => {
    useLintStore.setState({ selectedIds: new Set(["f1", "f2"]) });
    useLintStore.getState().clearSelection();
    expect(useLintStore.getState().selectedIds.size).toBe(0);
  });
});

// ─── B1: batch actions ────────────────────────────────────────────────────────

describe("lintStore — applyBatch [B1-L5]", () => {
  it("removes ok findings from list and returns ok/err counts", async () => {
    useLintStore.setState({
      findings: [makeFinding("f1"), makeFinding("f2")],
      findingsTotal: 2,
      selectedIds: new Set(["f1", "f2"]),
    });
    vi.mocked(lintClient.batchLintAction).mockResolvedValueOnce({
      results: [
        { id: "f1", status: "ok" },
        { id: "f2", status: "ok" },
      ],
      ok_count: 2,
      error_count: 0,
    });
    // fetchLintFindings called by refresh after batch
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValueOnce({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });

    const result = await useLintStore.getState().applyBatch("default");

    expect(result.ok).toBe(2);
    expect(result.err).toBe(0);
    expect(lintClient.batchLintAction).toHaveBeenCalledWith(
      expect.arrayContaining(["f1", "f2"]),
      "apply",
    );
    // selection cleared after batch
    expect(useLintStore.getState().selectedIds.size).toBe(0);
  });

  it("returns err count when batch fails", async () => {
    useLintStore.setState({
      findings: [makeFinding("f1")],
      findingsTotal: 1,
      selectedIds: new Set(["f1"]),
    });
    vi.mocked(lintClient.batchLintAction).mockRejectedValueOnce(new Error("Server error"));

    const result = await useLintStore.getState().applyBatch("default");

    expect(result.ok).toBe(0);
    expect(result.err).toBe(1);
    expect(useLintStore.getState().batchError).toBe("Server error");
  });

  it("does nothing when no ids selected", async () => {
    useLintStore.setState({ selectedIds: new Set<string>() });
    const result = await useLintStore.getState().applyBatch("default");
    expect(result.ok).toBe(0);
    expect(result.err).toBe(0);
    expect(lintClient.batchLintAction).not.toHaveBeenCalled();
  });
});

describe("lintStore — dismissBatch [B1-L5]", () => {
  it("calls batchLintAction with action=dismiss", async () => {
    useLintStore.setState({
      findings: [makeFinding("f1")],
      findingsTotal: 1,
      selectedIds: new Set(["f1"]),
    });
    vi.mocked(lintClient.batchLintAction).mockResolvedValueOnce({
      results: [{ id: "f1", status: "ok" }],
      ok_count: 1,
      error_count: 0,
    });
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValueOnce({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });

    await useLintStore.getState().dismissBatch("default");

    expect(lintClient.batchLintAction).toHaveBeenCalledWith(
      expect.arrayContaining(["f1"]),
      "dismiss",
    );
  });
});

describe("lintStore — sendToReviewBatch [B1-L5/L6]", () => {
  it("calls batchLintAction with action=send-to-review", async () => {
    useLintStore.setState({
      findings: [makeFinding("f1")],
      findingsTotal: 1,
      selectedIds: new Set(["f1"]),
    });
    vi.mocked(lintClient.batchLintAction).mockResolvedValueOnce({
      results: [{ id: "f1", status: "ok" }],
      ok_count: 1,
      error_count: 0,
    });
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValueOnce({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });

    await useLintStore.getState().sendToReviewBatch("default");

    expect(lintClient.batchLintAction).toHaveBeenCalledWith(
      expect.arrayContaining(["f1"]),
      "send-to-review",
    );
  });
});

// ─── B1: sendToReview single ──────────────────────────────────────────────────

describe("lintStore — sendToReview [B1-L6]", () => {
  it("removes finding from list on success", async () => {
    useLintStore.setState({
      findings: [makeFinding("f1"), makeFinding("f2")],
      findingsTotal: 2,
    });
    vi.mocked(lintClient.sendLintFindingToReview).mockResolvedValueOnce(
      makeFinding("f1", { status: "applied" }),
    );

    await useLintStore.getState().sendToReview("f1");

    const state = useLintStore.getState();
    expect(state.findings).toHaveLength(1);
    expect(state.findings[0]?.id).toBe("f2");
    expect(state.findingsTotal).toBe(1);
  });

  it("sets actionError on failure", async () => {
    useLintStore.setState({ findings: [makeFinding("f1")], findingsTotal: 1 });
    vi.mocked(lintClient.sendLintFindingToReview).mockRejectedValueOnce(
      new Error("Review queue unavailable"),
    );

    await useLintStore.getState().sendToReview("f1");

    expect(useLintStore.getState().actionError["f1"]).toBe("Review queue unavailable");
    expect(useLintStore.getState().findings).toHaveLength(1);
  });
});

// ─── B1: deleteOrphanPage ─────────────────────────────────────────────────────

describe("lintStore — deleteOrphanPage [B1-L9]", () => {
  it("calls deleteWikiPage then removes finding from list", async () => {
    useLintStore.setState({
      findings: [makeFinding("f1", { category: "orphan-page", target_page_id: "page-1" })],
      findingsTotal: 1,
    });
    vi.mocked(lintClient.deleteWikiPage).mockResolvedValueOnce({
      deleted_page_id: "page-1",
      wikilinks_cleaned: 2,
      index_entry_removed: true,
      shared_entity_warnings: [],
    });
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValueOnce({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });

    await useLintStore.getState().deleteOrphanPage("f1", "page-1", "default");

    expect(lintClient.deleteWikiPage).toHaveBeenCalledWith("page-1");
    expect(useLintStore.getState().findings).toHaveLength(0);
  });

  it("sets actionError on failure and keeps finding in list", async () => {
    useLintStore.setState({
      findings: [makeFinding("f1", { category: "orphan-page", target_page_id: "page-1" })],
      findingsTotal: 1,
    });
    vi.mocked(lintClient.deleteWikiPage).mockRejectedValueOnce(new Error("Page not found"));

    await useLintStore.getState().deleteOrphanPage("f1", "page-1", "default");

    expect(useLintStore.getState().actionError["f1"]).toBe("Page not found");
    expect(useLintStore.getState().findings).toHaveLength(1);
  });
});
