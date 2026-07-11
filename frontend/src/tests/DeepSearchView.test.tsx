/**
 * DeepSearchView.test.tsx — vitest + React Testing Library tests for the Deep Search UI.
 *
 * Tests: renders topic input + start button, start fires POST, run list rendered,
 *        run detail shown on selection, error states, status rendering.
 * All network calls are mocked via vi.mock — no real fetch.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DeepSearchView } from "../components/research/DeepSearchView";
import { useResearchStore } from "../store/researchStore";
import type { ResearchRunDetail, ResearchRunSummary } from "../api/types";

// ─── Mock the API clients ─────────────────────────────────────────────────────

vi.mock("../api/researchClient", () => ({
  startResearch: vi.fn(),
  fetchResearchRuns: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 }),
  fetchResearchRunDetail: vi.fn(),
  deleteResearchRun: vi.fn(),
}));

import * as researchClient from "../api/researchClient";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      // Return descriptive strings for key paths used in the component
      const translations: Record<string, string> = {
        "research.title": "Deep Search",
        "research.topicLabel": "Research topic",
        "research.topicPlaceholder": "e.g. Kubernetes networking",
        "research.topicHint": "Synapse will search the web.",
        "research.startButton": "Start Research",
        "research.empty": "No research runs yet.",
        "research.noRunSelected": "Select a run to see its detail.",
        "research.loadMore": "Load more",
        "research.runLabel": "Research run",
        "research.cost": "Cost",
        "research.iterations": "Iterations",
        "research.sources": "Sources",
        "research.synthesis": "Synthesis",
        "research.queriesUsed": "Queries used",
        "research.sourcesFetched": "Fetched sources",
        "research.wikiPageCreated": "Wiki page created",
        "research.iteration": "Iter",
        "research.relevance": "Rel",
        "research.status.running": "Running",
        "research.status.converged": "Converged",
        "research.status.max_iter_reached": "Max iterations",
        "research.status.budget_exhausted": "Budget exhausted",
        "research.status.error": "Error",
        "common.loading": "Loading…",
        "common.retry": "Retry",
      };
      const val = translations[key];
      if (val !== undefined) return val;
      // For dynamic keys (e.g. provider.type.*) fall back to the key suffix
      if (opts?.defaultValue) return opts.defaultValue as string;
      return key;
    },
    i18n: { language: "en" },
  }),
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: { vaultId: string }) => unknown) =>
    selector({ vaultId: "default" }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeRun(id: string, status: string, topic = "Test topic"): ResearchRunSummary {
  return {
    id,
    vault_id: "default",
    topic,
    status: status as ResearchRunSummary["status"],
    iterations_used: 2,
    sources_fetched: 3,
    total_cost_usd: 0.0012,
    started_at: new Date().toISOString(),
    completed_at: null,
  };
}

function makeDetail(id: string, status: string): ResearchRunDetail {
  return {
    id,
    vault_id: "default",
    topic: "Test topic",
    status: status as ResearchRunDetail["status"],
    max_iter: 3,
    token_budget: 100000,
    iterations_used: 2,
    queries_used: ["query A", "query B"],
    sources_fetched: 3,
    total_cost_usd: 0.0012,
    synthesis_text: status !== "running" ? "# Summary\nContent here." : null,
    synthesis_page_id: null,
    sources: [
      { url: "https://example.com", title: "Example Page", relevance_score: 0.9, iteration: 1 },
    ],
    started_at: new Date().toISOString(),
    completed_at: status !== "running" ? new Date().toISOString() : null,
    error_message: null,
  };
}

// Reset store before each test
beforeEach(() => {
  vi.clearAllMocks();
  useResearchStore.setState({
    runs: [],
    total: 0,
    offset: 0,
    listLoading: false,
    listError: null,
    selectedRunId: null,
    detail: null,
    detailLoading: false,
    detailError: null,
    runningCount: 0,
    starting: false,
    startError: null,
    deletingRunId: null,
    deleteError: null,
  });
  // Default: fetchResearchRuns returns empty
  vi.mocked(researchClient.fetchResearchRuns).mockResolvedValue({
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
  });
});

// ─── Rendering ────────────────────────────────────────────────────────────────

describe("DeepSearchView — rendering", () => {
  it("renders the section title", () => {
    render(<DeepSearchView />);
    expect(screen.getByText("Deep Search")).toBeDefined();
  });

  it("renders topic input field", () => {
    render(<DeepSearchView />);
    expect(screen.getByTestId("research-topic-input")).toBeDefined();
  });

  it("renders Start Research button", () => {
    render(<DeepSearchView />);
    expect(screen.getByTestId("research-start-btn")).toBeDefined();
  });

  it("Start Research button is disabled when topic is empty", () => {
    render(<DeepSearchView />);
    const btn = screen.getByTestId("research-start-btn") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("Start Research button is enabled when topic is typed", () => {
    render(<DeepSearchView />);
    const input = screen.getByTestId("research-topic-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Kubernetes networking" } });
    const btn = screen.getByTestId("research-start-btn") as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  it("shows the empty state message when no runs", async () => {
    render(<DeepSearchView />);
    await waitFor(() => {
      expect(screen.getByTestId("research-run-list-empty")).toBeDefined();
    });
  });

  it("shows 'Select a run' placeholder in the detail pane", () => {
    render(<DeepSearchView />);
    expect(screen.getByTestId("research-detail-empty")).toBeDefined();
  });
});

// ─── Topic input + start action ───────────────────────────────────────────────

describe("DeepSearchView — start research action (AC-F10-8b)", () => {
  it("calls POST /research/start when Start button is clicked", async () => {
    vi.mocked(researchClient.startResearch).mockResolvedValueOnce({ run_id: "new-run" });
    vi.mocked(researchClient.fetchResearchRuns).mockResolvedValue({
      items: [makeRun("new-run", "running")],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(researchClient.fetchResearchRunDetail).mockResolvedValueOnce(
      makeDetail("new-run", "running"),
    );

    render(<DeepSearchView />);

    const input = screen.getByTestId("research-topic-input");
    fireEvent.change(input, { target: { value: "Kubernetes networking" } });

    const btn = screen.getByTestId("research-start-btn");
    fireEvent.click(btn);

    await waitFor(() => {
      expect(researchClient.startResearch).toHaveBeenCalledWith(
        expect.objectContaining({ topic: "Kubernetes networking", vault_id: "default" }),
      );
    });
  });

  it("clears topic after successful start", async () => {
    vi.mocked(researchClient.startResearch).mockResolvedValueOnce({ run_id: "new-run" });
    vi.mocked(researchClient.fetchResearchRuns).mockResolvedValue({
      items: [makeRun("new-run", "running")],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(researchClient.fetchResearchRunDetail).mockResolvedValueOnce(
      makeDetail("new-run", "running"),
    );

    render(<DeepSearchView />);
    const input = screen.getByTestId("research-topic-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Some topic" } });
    fireEvent.click(screen.getByTestId("research-start-btn"));

    await waitFor(() => {
      expect(input.value).toBe("");
    });
  });

  it("shows startError when POST fails", async () => {
    vi.mocked(researchClient.startResearch).mockRejectedValueOnce(
      new Error("503 SEARXNG not configured"),
    );

    render(<DeepSearchView />);
    const input = screen.getByTestId("research-topic-input");
    fireEvent.change(input, { target: { value: "test" } });
    fireEvent.click(screen.getByTestId("research-start-btn"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeDefined();
    });
  });
});

// ─── Run list rendering ───────────────────────────────────────────────────────
// Note: TanStack Virtual requires a real scroll container with dimensions to render rows.
// In jsdom there is no layout engine, so no virtual rows are mounted when runs > 0.
// We test the empty state here (no runs → empty message) and verify the list container
// is present. Individual run card rendering is covered by researchStore tests + Playwright E2E.

describe("DeepSearchView — run list (AC-F10-8c)", () => {
  it("renders the run list container when runs are present", async () => {
    useResearchStore.setState({
      runs: [makeRun("r1", "converged", "K8s networking"), makeRun("r2", "running", "Calico BGP")],
      total: 2,
    });

    render(<DeepSearchView />);

    // The list container should exist; virtual rows may or may not be rendered in jsdom
    await waitFor(() => {
      expect(screen.getByTestId("research-run-list")).toBeDefined();
    });
  });

  it("hides the empty-state message when runs are present", async () => {
    useResearchStore.setState({
      runs: [makeRun("r1", "converged", "K8s networking")],
      total: 1,
    });

    render(<DeepSearchView />);

    await waitFor(() => {
      // The virtualised list container should be present (not the empty state)
      expect(screen.getByTestId("research-run-list")).toBeDefined();
      expect(screen.queryByTestId("research-run-list-empty")).toBeNull();
    });
  });
});

// ─── Run detail rendering ─────────────────────────────────────────────────────

describe("DeepSearchView — run detail (AC-F10-8d)", () => {
  it("shows synthesis text when detail has synthesis_text and status is not running", async () => {
    useResearchStore.setState({
      selectedRunId: "r1",
      detail: makeDetail("r1", "converged"),
    });

    render(<DeepSearchView />);

    expect(screen.getByTestId("research-run-detail")).toBeDefined();
    expect(screen.getByTestId("research-synthesis-text")).toBeDefined();
    expect(screen.getByText(/# Summary/)).toBeDefined();
  });

  it("shows loading spinner when detailLoading is true", () => {
    useResearchStore.setState({
      selectedRunId: "r1",
      detail: null,
      detailLoading: true,
    });

    render(<DeepSearchView />);
    expect(screen.getByText("Loading…")).toBeDefined();
  });

  it("shows error message when detailError is set", () => {
    useResearchStore.setState({
      selectedRunId: "r1",
      detail: null,
      detailLoading: false,
      detailError: "404 Not found",
    });

    render(<DeepSearchView />);
    expect(screen.getByRole("alert")).toBeDefined();
    expect(screen.getByText("404 Not found")).toBeDefined();
  });

  it("selectRun is triggered when store.selectRun is called directly (store logic)", async () => {
    // Testing the store-driven selectRun directly (not via a virtual-rendered card click)
    // since jsdom virtualizer does not render rows without a layout engine.
    vi.mocked(researchClient.fetchResearchRunDetail).mockResolvedValueOnce(
      makeDetail("r1", "converged"),
    );

    await useResearchStore.getState().selectRun("r1");

    await waitFor(() => {
      expect(researchClient.fetchResearchRunDetail).toHaveBeenCalledWith("r1");
    });
  });
});

// ─── Status badge rendering ───────────────────────────────────────────────────
// Status badges on run cards are not accessible in jsdom (virtualizer doesn't render rows
// without layout). We test them via the run detail panel (which is NOT virtualised).

describe("DeepSearchView — status badges in detail panel", () => {
  it.each([
    ["running", "Running"],
    ["converged", "Converged"],
    ["max_iter_reached", "Max iterations"],
    ["budget_exhausted", "Budget exhausted"],
    ["error", "Error"],
  ])("detail panel renders status badge label for status '%s'", async (status, label) => {
    useResearchStore.setState({
      selectedRunId: "r1",
      detail: makeDetail("r1", status),
    });

    render(<DeepSearchView />);

    // The detail panel is always rendered (not virtualised) — badge label should appear
    await waitFor(() => {
      const badge = screen.getAllByText(label);
      expect(badge.length).toBeGreaterThan(0);
    });
  });
});
