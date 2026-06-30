/**
 * LintView.test.tsx — vitest + React Testing Library tests for K2 Lint UI (ADR-0037 §6).
 *
 * Covers:
 *   - Renders header with title
 *   - Empty state when no open findings
 *   - Renders finding rows with category badge, target_title, description
 *   - "Fix" label for real-fix categories (missing-xref, missing-page)
 *   - "Acknowledge" label for flag-only categories (orphan-page, contradiction, stale-claim)
 *   - Apply button fires store.apply; finding leaves list on success
 *   - Dismiss button fires store.dismiss; finding leaves list on success
 *   - Per-finding actionError shown
 *   - 409 from apply → actionError set, finding stays in list
 *   - Scan error banner shows and can be dismissed
 *   - Run info line shows cost at 4dp
 *   - Load more button present when items < total
 *   - Empty state rendered when no findings
 *
 * All network calls are mocked. Store is reset between tests.
 * INVARIANT I3: Zustand selectors; no per-token parsing (descriptions are plain text).
 * INVARIANT I4: virtualisation is present (TanStack Virtual mocked for jsdom).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LintView } from "../components/lint/LintView";
import { useLintStore } from "../store/lintStore";
import type { LintFinding, LintRun } from "../api/types";

// ─── Mock TanStack Virtual ────────────────────────────────────────────────────
// jsdom has no layout engine; mock the virtualizer to pass items through.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: (opts: { count: number; estimateSize: () => number }) => ({
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, i) => ({
        index: i,
        start: i * opts.estimateSize(),
        end: (i + 1) * opts.estimateSize(),
        size: opts.estimateSize(),
        key: i,
        lane: 0,
      })),
    getTotalSize: () => opts.count * opts.estimateSize(),
    measureElement: () => undefined,
    scrollToIndex: () => undefined,
    scrollToOffset: () => undefined,
    scrollRect: { width: 0, height: 600 },
    options: opts,
  }),
}));

// ─── Mock API client ──────────────────────────────────────────────────────────

vi.mock("../api/lintClient", () => ({
  runLintScan: vi.fn().mockResolvedValue({ run: null, findings: [] }),
  fetchLintRuns: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 }),
  fetchLintRun: vi.fn(),
  fetchLintFindings: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 }),
  applyLintFinding: vi.fn(),
  dismissLintFinding: vi.fn(),
}));

import * as lintClient from "../api/lintClient";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        "lint.title": "Lint",
        "lint.hint": "Wiki health findings.",
        "lint.empty": "No open findings.",
        "lint.emptyBody": "Run a lint scan to check wiki health.",
        "lint.runLint": "Run Lint",
        "lint.runLintHelp": "Run a bounded lint scan.",
        "lint.scanning": "Scanning…",
        "lint.refresh": "Refresh",
        "lint.loadMore": "Load more",
        "lint.fix": "Fix",
        "lint.fixing": "Fixing…",
        "lint.acknowledge": "Acknowledge",
        "lint.dismiss": "Dismiss",
        "lint.cost": "Cost",
        "lint.findings": "Findings",
        "lint.iterations": "Iterations",
        "lint.flagOnly": "Flag-only — no file write on apply",
        "lint.noTarget": "(unknown page)",
        "lint.toastError": `Lint scan failed: ${String(params?.detail ?? "")}`,
        "lint.runStatus.completed": "Completed",
        "lint.runStatus.running": "Running",
        "lint.runStatus.error": "Error",
        "lint.category.orphan-page": "Orphan page",
        "lint.category.missing-xref": "Missing xref",
        "lint.category.contradiction": "Contradiction",
        "lint.category.stale-claim": "Stale claim",
        "lint.category.missing-page": "Missing page",
        "common.loading": "Loading…",
        "common.retry": "Retry",
        "common.close": "Close",
        "nav.lint": "Lint",
      };
      return map[key] ?? key;
    },
    i18n: { language: "en" },
  }),
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({ vaultId: "default", setActiveSection: vi.fn() }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  selectSetActiveSection: (s: { setActiveSection: () => void }) => s.setActiveSection,
}));

// ─── Mock Toast ───────────────────────────────────────────────────────────────

vi.mock("../components/common/Toast", () => ({
  showToast: vi.fn(),
}));

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
    target_title: `Page ${id}`,
    description: `Description for finding ${id}.`,
    proposed_action: `Create page: Missing ${id}`,
    status: "open",
    resolution_note: null,
    created_at: new Date().toISOString(),
    reviewed_at: null,
    ...overrides,
  };
}

function resetStore(overrides: Partial<ReturnType<typeof useLintStore.getState>> = {}) {
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
    ...overrides,
  });
}

beforeEach(() => {
  resetStore();
  vi.clearAllMocks();
  vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
  });
});

// ─── Rendering ────────────────────────────────────────────────────────────────

describe("LintView — rendering", () => {
  it("renders the header with title", async () => {
    render(<LintView />);
    expect(screen.getByText("Lint")).toBeTruthy();
  });

  it("renders empty state when no findings", async () => {
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-empty")).toBeTruthy();
    });
    expect(screen.getByText("No open findings.")).toBeTruthy();
  });

  it("renders finding rows with target_title and description", async () => {
    const findings = [makeFinding("f1"), makeFinding("f2")];
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: findings,
      total: 2,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("lint-finding-row")).toHaveLength(2);
    });
    expect(screen.getByText("Page f1")).toBeTruthy();
    expect(screen.getByText("Page f2")).toBeTruthy();
  });

  it("renders category badge for missing-xref", async () => {
    const findings = [makeFinding("f1", { category: "missing-xref" })];
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: findings,
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByText("Missing xref")).toBeTruthy();
    });
  });

  it("renders count badge in header when findings > 0", async () => {
    const findings = [makeFinding("f1"), makeFinding("f2")];
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: findings,
      total: 2,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByLabelText("2 findings")).toBeTruthy();
    });
  });

  it("shows load-more button when items < total", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1")],
      total: 5,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-load-more")).toBeTruthy();
    });
  });

  it("does not show load-more when all items loaded", async () => {
    resetStore({ findings: [makeFinding("f1")], findingsTotal: 1 });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.queryByTestId("lint-load-more")).toBeNull();
    });
  });
});

// ─── Apply label: real-fix vs flag-only ───────────────────────────────────────

describe("LintView — Apply button label (real-fix vs flag-only)", () => {
  it("shows 'Fix' label for missing-xref (real-fix category)", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { category: "missing-xref" })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getAllByTestId("lint-finding-row")).toHaveLength(1);
    });
    // data-testid="lint-action-apply" has label "Fix"
    const applyBtn = screen.getByTestId("lint-action-apply");
    expect(applyBtn.textContent).toContain("Fix");
  });

  it("shows 'Fix' label for missing-page (real-fix category)", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { category: "missing-page", proposed_action: "Create page: X" })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getAllByTestId("lint-finding-row")).toHaveLength(1);
    });
    const applyBtn = screen.getByTestId("lint-action-apply");
    expect(applyBtn.textContent).toContain("Fix");
  });

  it("shows 'Acknowledge' label for orphan-page (flag-only category)", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { category: "orphan-page", proposed_action: null })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getAllByTestId("lint-finding-row")).toHaveLength(1);
    });
    const ackBtn = screen.getByTestId("lint-action-acknowledge");
    expect(ackBtn.textContent).toContain("Acknowledge");
  });

  it("shows 'Acknowledge' label for contradiction (flag-only category)", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { category: "contradiction", proposed_action: null })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getAllByTestId("lint-finding-row")).toHaveLength(1);
    });
    expect(screen.getByTestId("lint-action-acknowledge")).toBeTruthy();
  });

  it("shows 'Acknowledge' label for stale-claim (flag-only category)", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { category: "stale-claim", proposed_action: null })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getAllByTestId("lint-finding-row")).toHaveLength(1);
    });
    expect(screen.getByTestId("lint-action-acknowledge")).toBeTruthy();
  });

  it("shows flag-only hint for orphan-page", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { category: "orphan-page", proposed_action: null })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByText("Flag-only — no file write on apply")).toBeTruthy();
    });
  });
});

// ─── Apply action ─────────────────────────────────────────────────────────────

describe("LintView — Apply action", () => {
  it("calls applyLintFinding; finding leaves list on success", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1"), makeFinding("f2")],
      total: 2,
      limit: 50,
      offset: 0,
    });
    vi.mocked(lintClient.applyLintFinding).mockResolvedValueOnce(
      makeFinding("f1", { status: "applied" }),
    );
    render(<LintView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("lint-action-apply")).toHaveLength(2);
    });

    fireEvent.click(screen.getAllByTestId("lint-action-apply")[0]!);

    await waitFor(() => {
      expect(lintClient.applyLintFinding).toHaveBeenCalledWith("f1");
    });

    await waitFor(() => {
      expect(screen.getAllByTestId("lint-finding-row")).toHaveLength(1);
      expect(screen.queryByText("Page f1")).toBeNull();
      expect(screen.getByText("Page f2")).toBeTruthy();
    });
  });

  it("shows per-finding error on 409 and keeps finding in list", async () => {
    const { ApiError } = await import("../api/graphClient");
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1")],
      total: 1,
      limit: 50,
      offset: 0,
    });
    vi.mocked(lintClient.applyLintFinding).mockRejectedValueOnce(
      new ApiError(409, "409 finding is not open"),
    );
    render(<LintView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("lint-action-apply")).toHaveLength(1);
    });

    fireEvent.click(screen.getAllByTestId("lint-action-apply")[0]!);

    await waitFor(() => {
      // Finding stays in list
      expect(screen.getByTestId("lint-finding-row")).toBeTruthy();
      // Error shown
      expect(screen.getByRole("alert")).toBeTruthy();
    });
  });
});

// ─── Dismiss action ───────────────────────────────────────────────────────────

describe("LintView — Dismiss action", () => {
  it("calls dismissLintFinding; finding leaves list on success", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1"), makeFinding("f2")],
      total: 2,
      limit: 50,
      offset: 0,
    });
    vi.mocked(lintClient.dismissLintFinding).mockResolvedValueOnce(
      makeFinding("f1", { status: "dismissed" }),
    );
    render(<LintView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("lint-action-dismiss")).toHaveLength(2);
    });

    fireEvent.click(screen.getAllByTestId("lint-action-dismiss")[0]!);

    await waitFor(() => {
      expect(lintClient.dismissLintFinding).toHaveBeenCalledWith("f1");
    });

    await waitFor(() => {
      expect(screen.getAllByTestId("lint-finding-row")).toHaveLength(1);
    });
  });
});

// ─── Run Lint button ──────────────────────────────────────────────────────────

describe("LintView — Run Lint button", () => {
  it("calls runLintScan when clicked", async () => {
    const run = makeLintRun();
    vi.mocked(lintClient.runLintScan).mockResolvedValueOnce({ run, findings: [] });
    render(<LintView />);

    const runBtn = screen.getByTestId("lint-run-btn");
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(lintClient.runLintScan).toHaveBeenCalledWith(
        { vault_id: "default" },
        undefined,
      );
    });
  });
});

// ─── Run info line ────────────────────────────────────────────────────────────

describe("LintView — run info line", () => {
  it("shows cost at 4dp after scan completes", async () => {
    resetStore({
      currentRun: makeLintRun({ total_cost_usd: 0.0042, findings_count: 2 }),
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-run-info")).toBeTruthy();
    });
    // 0.0042 at 4dp = $0.0042
    expect(screen.getByText(/\$0\.0042/)).toBeTruthy();
  });
});

// ─── Scan error banner ────────────────────────────────────────────────────────

describe("LintView — scan error banner", () => {
  it("shows scan error banner when scanError is set", async () => {
    resetStore({ scanError: "Provider not configured" });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-scan-error")).toBeTruthy();
      expect(screen.getByText("Provider not configured")).toBeTruthy();
    });
  });

  it("closes scan error banner when close is clicked", async () => {
    resetStore({ scanError: "Provider not configured" });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-scan-error")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("Close"));
    await waitFor(() => {
      expect(screen.queryByTestId("lint-scan-error")).toBeNull();
    });
  });
});

// ─── Findings load error ──────────────────────────────────────────────────────

describe("LintView — findings load error", () => {
  it("shows error message when fetch fails", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockRejectedValueOnce(
      new Error("Backend unavailable"),
    );
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-findings-error")).toBeTruthy();
      expect(screen.getByText("Backend unavailable")).toBeTruthy();
    });
  });
});
