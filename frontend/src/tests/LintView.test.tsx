/**
 * LintView.test.tsx — vitest + React Testing Library tests for K2 Lint UI (ADR-0037 §6, B1).
 *
 * Covers:
 *   - Renders header with title
 *   - Empty state when no open findings
 *   - Renders finding rows with category badge, target_title, description
 *   - "Fix" label for real-fix categories (missing-xref, missing-page)
 *   - "Acknowledge" label for flag-only categories (orphan-page, contradiction, stale-claim)
 *   - "Fix" label for broken-wikilink with suggested_target; "Acknowledge" without
 *   - Suggested target strip rendered when suggested_target present
 *   - B1-L5: checkbox on each row; Select all in batch bar; batch bar visible
 *   - B1-L8: Semantic (LLM) checkbox present
 *   - B1-L4: Open button present when target_page_id present
 *   - B1-L9: Delete button present for orphan-page
 *   - B1-L6: Send to Review button on each row
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
  useVirtualizer: (opts: { count: number; estimateSize: (i: number) => number }) => ({
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, i) => ({
        index: i,
        start: i * opts.estimateSize(i),
        end: (i + 1) * opts.estimateSize(i),
        size: opts.estimateSize(i),
        key: i,
        lane: 0,
      })),
    getTotalSize: () =>
      Array.from({ length: opts.count }, (_, i) => opts.estimateSize(i)).reduce((a, b) => a + b, 0),
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
  startLintScan: vi.fn().mockResolvedValue({
    run_id: "run-1",
    status: "started",
    max_iter: 3,
    token_budget: 20000,
    semantic: true,
  }),
  fetchLintRuns: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 }),
  fetchLintRun: vi.fn(),
  fetchLintFindings: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 }),
  applyLintFinding: vi.fn(),
  dismissLintFinding: vi.fn(),
  batchLintAction: vi.fn().mockResolvedValue({ results: [], ok_count: 0, error_count: 0 }),
  sendLintFindingToReview: vi.fn().mockResolvedValue({}),
  deleteWikiPage: vi.fn().mockResolvedValue({
    deleted_page_id: "p1",
    wikilinks_cleaned: 0,
    index_entry_removed: false,
    shared_entity_warnings: [],
  }),
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
        "lint.category.broken-wikilink": "Broken link",
        "lint.groups.errors": "Errors",
        "lint.groups.warnings": "Warnings",
        "lint.groups.info": "Info",
        "lint.suggestedTarget": "Suggested target",
        "lint.open": "Open",
        "lint.selectAll": "Select all",
        "lint.selectFinding": "Select finding",
        "lint.selected": `${String(params?.count ?? 0)} selected`,
        "lint.fixSelected": "Fix selected",
        "lint.ignoreSelected": "Ignore selected",
        "lint.sendToReview": "Send to review",
        "lint.sendSelectedToReview": "Send to review",
        "lint.semantic": "Semantic (LLM)",
        "lint.semanticHelp": "When enabled, includes LLM semantic pass.",
        "lint.delete": "Delete",
        "lint.deleteConfirm": "Confirm delete",
        "lint.deleteConfirmHint": "Click Delete again to confirm",
        "lint.deleteSuccess": "Page deleted",
        "lint.batchApplied": `Fixed ${String(params?.count ?? 0)} findings`,
        "lint.batchDismissed": `Ignored ${String(params?.count ?? 0)} findings`,
        "lint.batchSentToReview": `Sent ${String(params?.count ?? 0)} findings to review`,
        "lint.batchPartial": `${String(params?.ok ?? 0)} succeeded, ${String(params?.err ?? 0)} failed`,
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
    selector({
      vaultId: "default",
      setActiveSection: vi.fn(),
      selectPage: vi.fn(),
    }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  selectSetActiveSection: (s: { setActiveSection: () => void }) => s.setActiveSection,
  selectSelectPage: (s: { selectPage: () => void }) => s.selectPage,
}));

// ─── Mock useProviderConfigured ───────────────────────────────────────────────

vi.mock("../hooks/useProviderConfigured", () => ({
  useProviderConfigured: () => ({ configured: true, loading: false, error: null }),
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
    suggested_target: null,
    suggested_page_id: null,
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
    semanticEnabled: true,
    selectedIds: new Set<string>(),
    batchInFlight: false,
    batchError: null,
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

  it("renders category badge for broken-wikilink", async () => {
    const findings = [makeFinding("f1", { category: "broken-wikilink", severity: "warning" })];
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: findings,
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByText("Broken link")).toBeTruthy();
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

// ─── B1-L8: Semantic checkbox ─────────────────────────────────────────────────

describe("LintView — Semantic (LLM) checkbox [B1-L8]", () => {
  it("renders the Semantic (LLM) checkbox", () => {
    render(<LintView />);
    expect(screen.getByTestId("lint-semantic-checkbox")).toBeTruthy();
  });

  it("checkbox label is present", () => {
    render(<LintView />);
    expect(screen.getByText("Semantic (LLM)")).toBeTruthy();
  });

  it("semantic checkbox defaults to checked (semanticEnabled=true)", () => {
    resetStore({ semanticEnabled: true });
    render(<LintView />);
    const cb = screen.getByTestId("lint-semantic-checkbox") as HTMLInputElement;
    expect(cb.checked).toBe(true);
  });
});

// ─── B1-L5: Batch bar ────────────────────────────────────────────────────────

describe("LintView — Batch bar [B1-L5]", () => {
  it("renders batch bar when findings exist", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1")],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-batch-bar")).toBeTruthy();
    });
  });

  it("Select all checkbox is present in batch bar", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1")],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-select-all")).toBeTruthy();
    });
  });

  it("Fix selected, Ignore selected, Send to review buttons present", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1")],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-batch-action-apply")).toBeTruthy();
      expect(screen.getByTestId("lint-batch-action-dismiss")).toBeTruthy();
      expect(screen.getByTestId("lint-batch-action-send-to-review")).toBeTruthy();
    });
  });

  it("each finding row has a checkbox", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1"), makeFinding("f2")],
      total: 2,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-row-checkbox-f1")).toBeTruthy();
      expect(screen.getByTestId("lint-row-checkbox-f2")).toBeTruthy();
    });
  });
});

// ─── B1-L2: Suggested target strip ───────────────────────────────────────────

describe("LintView — Suggested target strip [B1-L2]", () => {
  it("renders green suggested target strip when suggested_target present", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [
        makeFinding("f1", {
          category: "broken-wikilink",
          severity: "warning",
          suggested_target: "Temperature Scaling",
          suggested_page_id: "page-ts",
        }),
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-suggested-target")).toBeTruthy();
      expect(screen.getByText("Temperature Scaling")).toBeTruthy();
    });
  });

  it("does not render suggested strip when suggested_target is null", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { category: "broken-wikilink", suggested_target: null })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.queryByTestId("lint-suggested-target")).toBeNull();
    });
  });
});

// ─── B1-L4: Open button ──────────────────────────────────────────────────────

describe("LintView — Open button [B1-L4]", () => {
  it("renders Open button when target_page_id is present", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { target_page_id: "page-abc" })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-action-open")).toBeTruthy();
    });
  });

  it("does not render Open button when target_page_id is null", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { target_page_id: null })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.queryByTestId("lint-action-open")).toBeNull();
    });
  });
});

// ─── B1-L9: Delete button ────────────────────────────────────────────────────

describe("LintView — Delete button [B1-L9]", () => {
  it("renders Delete button for orphan-page finding", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { category: "orphan-page", target_page_id: "page-orphan" })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-action-delete")).toBeTruthy();
    });
  });

  it("does not render Delete button for non-orphan finding", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { category: "missing-xref" })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.queryByTestId("lint-action-delete")).toBeNull();
    });
  });

  it("shows delete confirm banner on first click (two-stage)", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [
        makeFinding("f1", {
          category: "orphan-page",
          target_page_id: "page-orphan",
          target_title: "Orphan Page",
        }),
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-action-delete")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("lint-action-delete"));
    await waitFor(() => {
      expect(screen.getByTestId("lint-delete-confirm-banner")).toBeTruthy();
    });
  });
});

// ─── B1-L6: Send to Review per-row ───────────────────────────────────────────

describe("LintView — Send to Review button [B1-L6]", () => {
  it("renders Send to review button on each finding row", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1")],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<LintView />);
    await waitFor(() => {
      // "Send to review" appears on the row (may also appear in batch bar)
      const btns = screen.getAllByTestId("lint-action-send-to-review");
      expect(btns.length).toBeGreaterThanOrEqual(1);
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

  it("shows 'Fix' for broken-wikilink WITH suggested_target", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [
        makeFinding("f1", {
          category: "broken-wikilink",
          severity: "warning",
          suggested_target: "Some Existing Page",
          suggested_page_id: "page-sep",
        }),
      ],
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

  it("shows 'Acknowledge' for broken-wikilink WITHOUT suggested_target", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [
        makeFinding("f1", {
          category: "broken-wikilink",
          severity: "warning",
          suggested_target: null,
          suggested_page_id: null,
        }),
      ],
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
  it("starts a background scan when clicked", async () => {
    const run = makeLintRun();
    vi.mocked(lintClient.fetchLintRun).mockResolvedValueOnce(run);
    render(<LintView />);

    const runBtn = screen.getByTestId("lint-run-btn");
    fireEvent.click(runBtn);

    await waitFor(() => {
      // semantic=true is default, passed as third param to startLintScan
      expect(lintClient.startLintScan).toHaveBeenCalledWith(
        { vault_id: "default" },
        undefined,
        true,
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
    vi.mocked(lintClient.fetchLintFindings).mockRejectedValueOnce(new Error("Backend unavailable"));
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-findings-error")).toBeTruthy();
      expect(screen.getByText("Backend unavailable")).toBeTruthy();
    });
  });
});

// ─── L11: severity group headers use severity_totals ─────────────────────────

describe("LintView — L11 severity_totals in group headers", () => {
  it("shows loaded-count in header when severity_totals absent (pre-v0.6 backend)", async () => {
    // 1 warning finding loaded; no severity_totals in response → header shows "1"
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { severity: "warning" })],
      total: 5, // backend has 5 but only returned 1 page
      limit: 50,
      offset: 0,
      // severity_totals absent
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-group-header-warning")).toBeTruthy();
    });
    // Header should display loaded count = 1 (fallback, no severity_totals)
    expect(screen.getByTestId("lint-group-header-warning").textContent).toContain("(1)");
  });

  it("shows severity_totals count in header even when fewer rows are loaded [L11]", async () => {
    // 1 error finding loaded; severity_totals says there are 12 errors in total
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { severity: "error" })],
      total: 20,
      limit: 50,
      offset: 0,
      severity_totals: { error: 12, warning: 5, info: 3 },
    });
    render(<LintView />);
    await waitFor(() => {
      expect(screen.getByTestId("lint-group-header-error")).toBeTruthy();
    });
    // Header must show the true total (12), not the loaded-row count (1)
    expect(screen.getByTestId("lint-group-header-error").textContent).toContain("(12)");
  });

  it("stores severity_totals in lintStore after refresh", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [makeFinding("f1", { severity: "warning" })],
      total: 10,
      limit: 50,
      offset: 0,
      severity_totals: { error: 2, warning: 7, info: 1 },
    });
    render(<LintView />);
    await waitFor(() => {
      // The store should have picked up severityTotals
      const { severityTotals } = useLintStore.getState();
      expect(severityTotals).toMatchObject({ error: 2, warning: 7, info: 1 });
    });
  });

  it("severityTotals is null in store when backend omits the field", async () => {
    vi.mocked(lintClient.fetchLintFindings).mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
      // severity_totals deliberately absent
    });
    render(<LintView />);
    await waitFor(() => {
      const { severityTotals } = useLintStore.getState();
      expect(severityTotals).toBeNull();
    });
  });
});
