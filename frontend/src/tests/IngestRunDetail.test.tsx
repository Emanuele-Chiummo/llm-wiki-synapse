/**
 * IngestRunDetail.test.tsx — unit tests for IngestRunDetail (UXA-06 zero-pages hint).
 *
 * Covers:
 *   UXA-06-1: completed run with pages_created=0 shows zeroPagesHint
 *   UXA-06-2: completed run with pages_created>0 does NOT show hint
 *   UXA-06-3: failed run with pages_created=0 does NOT show hint (wrong status)
 *   UXA-06-4: running run with pages_created=0 does NOT show hint
 *
 * PROJECT GOTCHA: vi.clearAllMocks() wipes implementations — re-set in beforeEach.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { IngestRunItem } from "../api/types";

// ─── Mock react-i18next ───────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { defaultValue?: string }) => {
      const map: Record<string, string> = {
        "ingest.manifest": "Run details",
        "ingest.status.completed": "Completed",
        "provider.label": "Provider",
        "ingest.iterationsUsed": "Iterations",
        "ingest.pagesCreated": "pages created",
        "ingest.typeDistribution": "Generated page types",
        "ingest.cost": "Cost",
        "ingest.startedAt": "Started",
        "ingest.completedAt": "Completed at",
        "ingest.error": "Error",
        "ingest.costAnomaly": "Cost anomaly",
        "ingest.noRunSelected": "Select a run to see details.",
        "ingest.zeroPagesHint":
          "0 pages created — check the provider configuration and that the file format is supported.",
        "ingest.diagnostics.heading": "Why it didn't converge",
        "ingest.diagnostics.stopReason": "Stop reason",
        "ingest.diagnostics.stopReasonValue.max_iter": "Max iterations reached",
        "ingest.diagnostics.stopReasonValue.token_budget": "Token budget exhausted",
        "ingest.diagnostics.stopReasonValue.converged": "Converged",
        "ingest.diagnostics.iterationsRun": "Iterations run",
        "ingest.diagnostics.tokenBudget": "Tokens used",
        "ingest.diagnostics.lastErrors": "Last validation errors",
        "ingest.diagnostics.noErrors": "No validation errors were recorded for the final attempt.",
      };
      return map[key] ?? opts?.defaultValue ?? key;
    },
  }),
}));

// ─── Mock zustand/react/shallow (useShallow passthrough for tests) ────────────

vi.mock("zustand/react/shallow", () => ({
  useShallow: (fn: unknown) => fn,
}));

// ─── Mock ingestStore ─────────────────────────────────────────────────────────

const mockState = {
  runs: [] as IngestRunItem[],
  selectedRunId: null as string | null,
};

vi.mock("../store/ingestStore", () => ({
  useIngestStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
  selectRuns: (s: typeof mockState) => s.runs,
  selectSelectedRunId: (s: typeof mockState) => s.selectedRunId,
}));

// ─── Mock formatCost from IngestRunList ───────────────────────────────────────

vi.mock("../components/ingest/IngestRunList", () => ({
  formatCost: (v: number) => `$${v.toFixed(4)}`,
}));

// ─── Import after mocks ───────────────────────────────────────────────────────

import { IngestRunDetail } from "../components/ingest/IngestRunDetail";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

function makeRun(overrides: Partial<IngestRunItem> = {}): IngestRunItem {
  return {
    id: "run-abc-1234-5678-9abc",
    vault_id: "default",
    status: "completed",
    pages_created: 0,
    iterations_used: 1,
    total_cost_usd: 0,
    provider_type: "local",
    started_at: "2026-07-01T10:00:00Z",
    completed_at: "2026-07-01T10:01:00Z",
    error_message: null,
    ...overrides,
  };
}

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  mockState.runs = [];
  mockState.selectedRunId = null;
});

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("IngestRunDetail — UXA-06: zero-pages hint", () => {
  it("UXA-06-1: completed run with pages_created=0 shows zeroPagesHint", () => {
    const run = makeRun({ status: "completed" as const, pages_created: 0 });
    mockState.runs = [run];
    mockState.selectedRunId = run.id;

    render(<IngestRunDetail />);
    expect(screen.getByTestId("ingest-zero-pages-hint")).toBeTruthy();
    expect(screen.getByTestId("ingest-zero-pages-hint").textContent).toContain("0 pages created");
  });

  it("UXA-06-2: completed run with pages_created>0 does NOT show hint", () => {
    const run = makeRun({ status: "completed" as const, pages_created: 3 });
    mockState.runs = [run];
    mockState.selectedRunId = run.id;

    render(<IngestRunDetail />);
    expect(screen.queryByTestId("ingest-zero-pages-hint")).toBeNull();
  });

  it("UXA-06-3: failed run with pages_created=0 does NOT show hint", () => {
    const run = makeRun({ status: "failed" as const, pages_created: 0 });
    mockState.runs = [run];
    mockState.selectedRunId = run.id;

    render(<IngestRunDetail />);
    expect(screen.queryByTestId("ingest-zero-pages-hint")).toBeNull();
  });

  it("UXA-06-4: running run with pages_created=0 does NOT show hint", () => {
    const run = makeRun({ status: "running" as const, pages_created: 0 });
    mockState.runs = [run];
    mockState.selectedRunId = run.id;

    render(<IngestRunDetail />);
    expect(screen.queryByTestId("ingest-zero-pages-hint")).toBeNull();
  });

  it("shows 'Select a run' when no run is selected", () => {
    mockState.runs = [];
    mockState.selectedRunId = null;

    render(<IngestRunDetail />);
    expect(screen.getByText(/Select a run/)).toBeTruthy();
  });

  it("shows the v1.6 generated PageType distribution when available", () => {
    const run = makeRun({
      pages_created: 4,
      page_type_counts: { source: 1, query: 1, comparison: 2, entity: 0 },
    });
    mockState.runs = [run];
    mockState.selectedRunId = run.id;

    render(<IngestRunDetail />);

    const distribution = screen.getByTestId("ingest-page-type-counts");
    expect(distribution.textContent).toContain("source: 1");
    expect(distribution.textContent).toContain("query: 1");
    expect(distribution.textContent).toContain("comparison: 2");
    expect(distribution.textContent).not.toContain("entity: 0");
  });
});

describe("IngestRunDetail — 1.9.1 W5 (NC-1): non-convergence diagnostics", () => {
  it("shows stop_reason/iterations/tokens/last_errors for a converged_false run", () => {
    const run = makeRun({
      status: "converged_false" as const,
      pages_created: 1,
      diagnostics: {
        stop_reason: "max_iter",
        iterations: 3,
        last_errors: ["generation produced no FILE blocks (0 parsed)"],
        tokens_used: 42000,
        token_budget: 60000,
      },
    });
    mockState.runs = [run];
    mockState.selectedRunId = run.id;

    render(<IngestRunDetail />);

    const block = screen.getByTestId("ingest-nonconvergence-diagnostics");
    expect(block.textContent).toContain("Max iterations reached");
    expect(block.textContent).toContain("3");
    expect(block.textContent).toContain("42000 / 60000");
    expect(block.textContent).toContain("generation produced no FILE blocks (0 parsed)");
  });

  it("shows the token_budget stop reason", () => {
    const run = makeRun({
      status: "converged_false" as const,
      diagnostics: {
        stop_reason: "token_budget",
        iterations: 2,
        last_errors: [],
        tokens_used: 60000,
        token_budget: 60000,
      },
    });
    mockState.runs = [run];
    mockState.selectedRunId = run.id;

    render(<IngestRunDetail />);

    const block = screen.getByTestId("ingest-nonconvergence-diagnostics");
    expect(block.textContent).toContain("Token budget exhausted");
    expect(block.textContent).toContain("No validation errors were recorded");
  });

  it("does NOT show the diagnostics block for a converged run", () => {
    const run = makeRun({
      status: "completed" as const,
      diagnostics: {
        stop_reason: "converged",
        iterations: 1,
        last_errors: [],
        tokens_used: 1000,
        token_budget: 60000,
      },
    });
    mockState.runs = [run];
    mockState.selectedRunId = run.id;

    render(<IngestRunDetail />);
    expect(screen.queryByTestId("ingest-nonconvergence-diagnostics")).toBeNull();
  });

  it("does NOT show the diagnostics block when diagnostics is null (delegated/CLI route)", () => {
    const run = makeRun({
      status: "converged_false" as const,
      diagnostics: null,
    });
    mockState.runs = [run];
    mockState.selectedRunId = run.id;

    render(<IngestRunDetail />);
    expect(screen.queryByTestId("ingest-nonconvergence-diagnostics")).toBeNull();
  });
});
