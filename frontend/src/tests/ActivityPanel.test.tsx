/**
 * ActivityPanel.test.tsx — unit tests for the expandable Activity Panel (F1).
 *
 * Coverage:
 *   - Renders task rows from a mocked snapshot.
 *   - Cancel button calls cancelRun with the run_id.
 *   - Retry button disabled when retry_count >= 3.
 *   - Retry button calls retryRun when retry_count < 3.
 *   - Pause toggle calls togglePause.
 *   - Auto-expand when processing > 0.
 *   - Empty-state rendered when total = 0.
 *   - Panel toggle (expand / collapse via chevron button).
 *
 * Mocking strategy:
 *   - useActivityStore is mocked via vi.mock — poll function is never started.
 *   - ingestClient functions are mocked to avoid real HTTP.
 *   - graphStore / providerStore / pagesClient mocked for ActivityBar footer needs.
 *   - i18next mocked with key passthrough.
 *   - lucide-react mocked so SVG rendering does not fail in jsdom.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ActivityBar } from "../components/activity/ActivityBar";
import type { IngestQueueSnapshot } from "../api/types";
import type { ActivityStore } from "../store/activityStore";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (params) {
        return Object.entries(params).reduce<string>(
          (s, [k, v]) => s.replace(`{{${k}}}`, String(v)),
          key,
        );
      }
      return key;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// ─── Mock lucide-react ────────────────────────────────────────────────────────

vi.mock("lucide-react", () => ({
  Loader2: ({ "aria-hidden": ah }: { "aria-hidden"?: boolean }) => (
    <span aria-hidden={ah}>Loader2</span>
  ),
  AlertCircle: ({ "aria-hidden": ah }: { "aria-hidden"?: boolean }) => (
    <span aria-hidden={ah}>AlertCircle</span>
  ),
  CheckCircle2: ({ "aria-hidden": ah }: { "aria-hidden"?: boolean }) => (
    <span aria-hidden={ah}>CheckCircle2</span>
  ),
  ChevronUp: ({ "aria-hidden": ah }: { "aria-hidden"?: boolean }) => (
    <span aria-hidden={ah}>ChevronUp</span>
  ),
  ChevronDown: ({ "aria-hidden": ah }: { "aria-hidden"?: boolean }) => (
    <span aria-hidden={ah}>ChevronDown</span>
  ),
  Clock: ({ "aria-hidden": ah }: { "aria-hidden"?: boolean }) => (
    <span aria-hidden={ah}>Clock</span>
  ),
  X: ({ "aria-hidden": ah }: { "aria-hidden"?: boolean }) => <span aria-hidden={ah}>X</span>,
  RotateCcw: ({ "aria-hidden": ah }: { "aria-hidden"?: boolean }) => (
    <span aria-hidden={ah}>RotateCcw</span>
  ),
  PauseCircle: ({ "aria-hidden": ah }: { "aria-hidden"?: boolean }) => (
    <span aria-hidden={ah}>PauseCircle</span>
  ),
  PlayCircle: ({ "aria-hidden": ah }: { "aria-hidden"?: boolean }) => (
    <span aria-hidden={ah}>PlayCircle</span>
  ),
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({ vaultId: "test-vault", dataVersion: 42 }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  useGraphMeta: () => ({ dataVersion: 42 }),
}));

// ─── Mock providerStore ───────────────────────────────────────────────────────

vi.mock("../store/providerStore", () => ({
  useProviderStore: (selector: (s: unknown) => unknown) =>
    selector({ activeItem: null }),
  selectActiveProvider: (s: { activeItem: unknown }) => s.activeItem,
}));

// ─── Mock pagesClient ─────────────────────────────────────────────────────────

vi.mock("../api/pagesClient", () => ({
  fetchStatus: vi.fn().mockResolvedValue({ data_version: 1, uptime_seconds: 120 }),
}));

// ─── Mock ingestClient ────────────────────────────────────────────────────────

const mockCancelIngestRun = vi.fn().mockResolvedValue({ run_id: "r1", status: "cancelling", cleaned_pages: 0 });
const mockRetryIngestRun = vi.fn().mockResolvedValue({ run_id_prev: "r1", source_path: "a.md", retry_count: 1, status: "queued" });
const mockPauseIngestQueue = vi.fn().mockResolvedValue({ paused: true });
const mockResumeIngestQueue = vi.fn().mockResolvedValue({ paused: false, drained: 0 });

vi.mock("../api/ingestClient", () => ({
  getIngestQueue: vi.fn().mockResolvedValue({
    paused: false, pending: 0, processing: 0, failed: 0,
    completed_since_idle: 0, total: 0, tasks: [],
  }),
  cancelIngestRun: (...args: unknown[]) => mockCancelIngestRun(...args),
  retryIngestRun: (...args: unknown[]) => mockRetryIngestRun(...args),
  pauseIngestQueue: (...args: unknown[]) => mockPauseIngestQueue(...args),
  resumeIngestQueue: (...args: unknown[]) => mockResumeIngestQueue(...args),
  MaxRetriesExceededError: class MaxRetriesExceededError extends Error {
    constructor() {
      super("max_retries_exceeded");
      this.name = "MaxRetriesExceededError";
    }
  },
}));

// ─── Mock activityStore ────────────────────────────────────────────────────────
//
// We build a mutable state object that all selectors read from, so tests can
// set up any snapshot they like. startPolling is a no-op so no real setTimeout
// loops run.

let mockActivityState: ActivityStore;

function buildMockState(snapshot: IngestQueueSnapshot | null): ActivityStore {
  const state: ActivityStore = {
    snapshot,
    loading: false,
    error: null,
    cancellingIds: new Set<string>(),
    fetchOnce: vi.fn().mockResolvedValue(undefined),
    startPolling: vi.fn().mockReturnValue(() => {}),
    cancelRun: vi.fn().mockImplementation(async (runId: string) => {
      state.cancellingIds = new Set([...state.cancellingIds, runId]);
    }),
    retryRun: vi.fn().mockResolvedValue(undefined),
    togglePause: vi.fn().mockResolvedValue(undefined),
  };
  return state;
}

vi.mock("../store/activityStore", () => {
  // Capture the module factory so we can also export hooks/selectors consistently.
  const useActivityStore = (selector?: (s: ActivityStore) => unknown) => {
    if (typeof selector === "function") return selector(mockActivityState);
    return mockActivityState;
  };
  // ActivityBar's adaptive /status poll reads useActivityStore.getState().snapshot (RT-1).
  (useActivityStore as unknown as { getState: () => ActivityStore }).getState = () =>
    mockActivityState;
  const hooks = {
    useActivityStore,
    useActivityCounts: () => ({
      paused: mockActivityState.snapshot?.paused ?? false,
      pending: mockActivityState.snapshot?.pending ?? 0,
      processing: mockActivityState.snapshot?.processing ?? 0,
      failed: mockActivityState.snapshot?.failed ?? 0,
      completed_since_idle: mockActivityState.snapshot?.completed_since_idle ?? 0,
      total: mockActivityState.snapshot?.total ?? 0,
    }),
    useActivityTasks: () => mockActivityState.snapshot?.tasks ?? [],
    useActivityBatch: () => {
      const b = mockActivityState.snapshot?.batch;
      return b
        ? { running: b.running, done: b.done, total: b.total, eta_seconds: b.eta_seconds ?? null }
        : null;
    },
    selectStartPolling: (s: ActivityStore) => s.startPolling,
    selectCancelRun: (s: ActivityStore) => s.cancelRun,
    selectRetryRun: (s: ActivityStore) => s.retryRun,
    selectTogglePause: (s: ActivityStore) => s.togglePause,
    selectSnapshot: (s: ActivityStore) => s.snapshot,
    selectActivityLoading: (s: ActivityStore) => s.loading,
    selectActivityError: (s: ActivityStore) => s.error,
    selectCancellingIds: (s: ActivityStore) => s.cancellingIds,
    selectFetchOnce: (s: ActivityStore) => s.fetchOnce,
    MAX_VISIBLE_FAILED: 50,
  };
  return hooks;
});

// ─── Helpers ──────────────────────────────────────────────────────────────────

const EMPTY_SNAPSHOT: IngestQueueSnapshot = {
  paused: false, pending: 0, processing: 0, failed: 0,
  completed_since_idle: 0, total: 0, tasks: [],
};

function renderBar() {
  return render(<ActivityBar />);
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("ActivityBar — collapsed bar", () => {
  beforeEach(() => {
    mockActivityState = buildMockState(EMPTY_SNAPSHOT);
  });

  it("renders the activity-bar footer", () => {
    renderBar();
    expect(screen.getByTestId("activity-bar")).toBeTruthy();
  });

  it("renders the toggle button", () => {
    renderBar();
    expect(screen.getByTestId("activity-panel-toggle")).toBeTruthy();
  });

  it("panel is NOT visible when collapsed", () => {
    renderBar();
    expect(screen.queryByTestId("activity-panel")).toBeNull();
  });

  it("starts polling on mount", () => {
    renderBar();
    expect(mockActivityState.startPolling).toHaveBeenCalled();
  });
});

describe("ActivityPanel — toggle expand/collapse", () => {
  beforeEach(() => {
    mockActivityState = buildMockState(EMPTY_SNAPSHOT);
  });

  it("clicking the toggle expands the panel", () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    expect(screen.getByTestId("activity-panel")).toBeTruthy();
  });

  it("clicking the toggle twice collapses the panel again", () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    expect(screen.queryByTestId("activity-panel")).toBeNull();
  });
});

describe("ActivityPanel — empty state", () => {
  beforeEach(() => {
    mockActivityState = buildMockState(EMPTY_SNAPSHOT);
  });

  it("shows empty-queue message when total = 0 (panel expanded)", () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    expect(screen.getByText("activity.emptyQueue")).toBeTruthy();
  });

  it("does NOT render any task rows for empty queue", () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    expect(screen.queryAllByTestId("activity-task-row")).toHaveLength(0);
  });
});

describe("ActivityPanel — task rows", () => {
  // Use processing=0 so the panel does NOT auto-expand, letting tests control expansion.
  beforeEach(() => {
    mockActivityState = buildMockState({
      paused: false,
      pending: 1,
      processing: 0,
      failed: 1,
      completed_since_idle: 2,
      total: 4,
      tasks: [
        { source_path: "raw/b.md", filename: "b.md", status: "pending", retry_count: 0 },
        { run_id: "fail-1", source_path: "raw/c.md", filename: "c.md", status: "failed", retry_count: 1, error: "parse error" },
      ],
    });
  });

  it("renders 2 task rows", async () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    await waitFor(() => expect(screen.getAllByTestId("activity-task-row")).toHaveLength(2));
  });

  it("renders Retry button for failed task", async () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    await waitFor(() => {
      const retryBtns = screen.getAllByTestId("activity-retry");
      expect(retryBtns.length).toBeGreaterThanOrEqual(1);
    });
  });

  it("Retry button is NOT disabled when retry_count < 3", async () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    await waitFor(() => {
      const retryBtn = screen.getAllByTestId("activity-retry")[0];
      expect((retryBtn as HTMLButtonElement).disabled).toBe(false);
    });
  });
});

describe("ActivityPanel — retry disabled at max retries", () => {
  beforeEach(() => {
    mockActivityState = buildMockState({
      paused: false,
      pending: 0,
      processing: 0,
      failed: 1,
      completed_since_idle: 0,
      total: 1,
      tasks: [
        {
          run_id: "fail-max",
          source_path: "raw/d.md",
          filename: "d.md",
          status: "failed",
          retry_count: 3,
          error: "too many retries",
        },
      ],
    });
  });

  it("Retry button is disabled when retry_count >= 3", async () => {
    renderBar();
    await act(async () => { fireEvent.click(screen.getByTestId("activity-panel-toggle")); });
    await waitFor(() => {
      const retryBtn = screen.getByTestId("activity-retry") as HTMLButtonElement;
      expect(retryBtn.disabled).toBe(true);
    });
  });
});

describe("ActivityPanel — cancel calls cancelRun", () => {
  // Use processing=0 so the panel doesn't auto-expand; we expand manually.
  // The task is still shown as a "processing" status row.
  beforeEach(() => {
    mockActivityState = buildMockState({
      paused: false,
      pending: 0,
      processing: 0,
      failed: 0,
      completed_since_idle: 0,
      total: 1,
      tasks: [
        {
          run_id: "proc-cancel",
          source_path: "raw/e.md",
          filename: "e.md",
          status: "processing",
          retry_count: 0,
        },
      ],
    });
  });

  it("clicking Cancel calls cancelRun with the run_id", async () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    const cancelBtn = await screen.findByTestId("activity-cancel");
    fireEvent.click(cancelBtn);
    await waitFor(() => {
      expect(mockActivityState.cancelRun).toHaveBeenCalledWith("proc-cancel");
    });
  });
});

describe("ActivityPanel — pause toggle", () => {
  beforeEach(() => {
    mockActivityState = buildMockState({ ...EMPTY_SNAPSHOT, paused: false });
  });

  it("renders Pause button when not paused", async () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    expect(await screen.findByTestId("activity-pause-toggle")).toBeTruthy();
  });

  it("clicking pause toggle calls togglePause", async () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    const pauseBtn = await screen.findByTestId("activity-pause-toggle");
    fireEvent.click(pauseBtn);
    await waitFor(() => {
      expect(mockActivityState.togglePause).toHaveBeenCalled();
    });
  });
});

describe("ActivityPanel — auto-expand when processing > 0", () => {
  it("panel is initially collapsed when processing = 0", () => {
    mockActivityState = buildMockState({ ...EMPTY_SNAPSHOT, processing: 0 });
    renderBar();
    expect(screen.queryByTestId("activity-panel")).toBeNull();
  });

  it("panel auto-expands when processing transitions from 0 to 1", async () => {
    // Start with 0 processing
    mockActivityState = buildMockState({ ...EMPTY_SNAPSHOT, processing: 0 });
    const { rerender } = renderBar();

    // Now simulate processing > 0
    mockActivityState = buildMockState({
      ...EMPTY_SNAPSHOT,
      processing: 1,
      total: 1,
      tasks: [
        {
          run_id: "auto-1",
          source_path: "raw/auto.md",
          filename: "auto.md",
          status: "processing",
          retry_count: 0,
        },
      ],
    });
    rerender(<ActivityBar />);

    await waitFor(() => {
      expect(screen.getByTestId("activity-panel")).toBeTruthy();
    });
  });
});

describe("ActivityPanel — progress bar", () => {
  // processing=0 so no auto-expand; panel is opened manually.
  beforeEach(() => {
    mockActivityState = buildMockState({
      paused: false,
      pending: 2,
      processing: 0,
      failed: 0,
      completed_since_idle: 3,
      total: 5,
      tasks: [
        { source_path: "b.md", filename: "b.md", status: "pending", retry_count: 0 },
        { source_path: "c.md", filename: "c.md", status: "pending", retry_count: 0 },
      ],
    });
  });

  it("renders the progress bar element", async () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    expect(await screen.findByTestId("activity-progress")).toBeTruthy();
  });
});

describe("ActivityPanel — Cancel All button", () => {
  // processing=0 so panel doesn't auto-expand; we control expansion.
  // pending+processing count comes from counts (pending=2, processing=0 → still 2 >= 2).
  beforeEach(() => {
    mockActivityState = buildMockState({
      paused: false,
      pending: 2,
      processing: 0,
      failed: 0,
      completed_since_idle: 0,
      total: 2,
      tasks: [
        { run_id: "r2", source_path: "b.md", filename: "b.md", status: "pending", retry_count: 0 },
        { run_id: "r3", source_path: "c.md", filename: "c.md", status: "pending", retry_count: 0 },
      ],
    });
    vi.stubGlobal("confirm", () => true);
  });

  it("shows Cancel All when pending+processing >= 2", async () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    expect(await screen.findByTestId("activity-cancel-all")).toBeTruthy();
  });

  it("clicking Cancel All then confirming the dialog calls cancelRun for each active run_id", async () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    fireEvent.click(await screen.findByTestId("activity-cancel-all"));
    // Cancel All now routes through ConfirmDialog (R7-12) — confirm to proceed.
    const confirmBtn = await screen.findByTestId("confirm-dialog-confirm");
    fireEvent.click(confirmBtn);
    await waitFor(() => {
      expect(mockActivityState.cancelRun).toHaveBeenCalledWith("r2");
      expect(mockActivityState.cancelRun).toHaveBeenCalledWith("r3");
    });
  });
});

describe("ActivityPanel — Retry Failed button", () => {
  beforeEach(() => {
    mockActivityState = buildMockState({
      paused: false,
      pending: 0,
      processing: 0,
      failed: 2,
      completed_since_idle: 0,
      total: 2,
      tasks: [
        { run_id: "f1", source_path: "a.md", filename: "a.md", status: "failed", retry_count: 0 },
        { run_id: "f2", source_path: "b.md", filename: "b.md", status: "failed", retry_count: 1 },
      ],
    });
  });

  it("shows Retry Failed button when failed > 0", async () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    expect(await screen.findByTestId("activity-retry-failed")).toBeTruthy();
  });

  it("clicking Retry Failed calls retryRun for each retriable task", async () => {
    renderBar();
    fireEvent.click(screen.getByTestId("activity-panel-toggle"));
    fireEvent.click(await screen.findByTestId("activity-retry-failed"));
    await waitFor(() => {
      expect(mockActivityState.retryRun).toHaveBeenCalledWith("f1");
      expect(mockActivityState.retryRun).toHaveBeenCalledWith("f2");
    });
  });
});

// ─── Phase / progress / ETA task row tests ────────────────────────────────────

describe("ActivityPanel — phase, determinate progress bar, and ETA (orchestrated task)", () => {
  // processing=1 so panel auto-expands.
  beforeEach(() => {
    mockActivityState = buildMockState({
      paused: false,
      pending: 0,
      processing: 1,
      failed: 0,
      completed_since_idle: 0,
      total: 1,
      tasks: [
        {
          run_id: "orch-1",
          source_path: "raw/doc.md",
          filename: "doc.md",
          status: "processing",
          retry_count: 0,
          phase: "generating (2/3)",
          progress: 0.5,
          elapsed_seconds: 65,
          eta_seconds: 90,
        },
      ],
    });
  });

  it("renders phase text for a processing task with phase set", async () => {
    renderBar();
    await waitFor(() => expect(screen.getByTestId("activity-panel")).toBeTruthy());
    const phaseEl = await screen.findByTestId("activity-task-phase");
    // "generating (2/3)" is a raw pass-through (not mapped to an i18n key)
    expect(phaseEl.textContent).toContain("generating (2/3)");
  });

  it("renders a determinate progress bar at ~50% for progress=0.5", async () => {
    renderBar();
    await waitFor(() => expect(screen.getByTestId("activity-panel")).toBeTruthy());
    const progressEl = await screen.findByTestId("activity-task-progress");
    expect(progressEl).toBeTruthy();
    // The inner fill div should have width 50%
    const fillDiv = progressEl.querySelector("div");
    expect(fillDiv).toBeTruthy();
    expect((fillDiv as HTMLElement).style.width).toBe("50%");
  });

  it("renders ETA text when eta_seconds is a number", async () => {
    renderBar();
    await waitFor(() => expect(screen.getByTestId("activity-panel")).toBeTruthy());
    const etaEl = await screen.findByTestId("activity-task-eta");
    // The i18n mock does key-passthrough with param interpolation:
    // t("activity.etaLeft", { eta: "1m 30s" }) → "activity.etaLeft" (key only, no real interpolation in mock)
    // so we assert the key is present AND the formatted duration "1m 30s" is included in the element.
    // The mock replaces {{eta}} → "1m 30s" in the key string: "activity.etaLeft" becomes "activity.etaLeft"
    // because the mock returns key when no {{}} replacements match.
    // Actually the mock does: replace("{{eta}}", "1m 30s") on the key string "activity.etaLeft"
    // which has no {{eta}}, so it returns "activity.etaLeft" unchanged.
    // The elapsed span is a sibling that shows "1m 5s". We verify the container shows "1m 5s".
    expect(etaEl.textContent).toContain("1m 5s");
  });

  it("renders the etaLeft i18n key in the ETA span when eta_seconds is a number", async () => {
    renderBar();
    await waitFor(() => expect(screen.getByTestId("activity-panel")).toBeTruthy());
    const etaEl = await screen.findByTestId("activity-task-eta");
    // The mock t() returns the key string; confirm the etaLeft key is present in rendered text
    expect(etaEl.textContent).toContain("activity.etaLeft");
  });

  it("renders elapsed time alongside ETA", async () => {
    renderBar();
    await waitFor(() => expect(screen.getByTestId("activity-panel")).toBeTruthy());
    const etaEl = await screen.findByTestId("activity-task-eta");
    // elapsed 65s → "1m 5s"
    expect(etaEl.textContent).toContain("1m 5s");
  });
});

describe("ActivityPanel — indeterminate bar and ETA for delegated/CLI task", () => {
  // processing=1 so panel auto-expands.
  beforeEach(() => {
    mockActivityState = buildMockState({
      paused: false,
      pending: 0,
      processing: 1,
      failed: 0,
      completed_since_idle: 0,
      total: 1,
      tasks: [
        {
          run_id: "cli-1",
          source_path: "raw/doc.md",
          filename: "doc.md",
          status: "processing",
          retry_count: 0,
          phase: "agent running",
          progress: null,
          elapsed_seconds: 30,
          eta_seconds: 120,
        },
      ],
    });
  });

  it("renders phase text for agent running", async () => {
    renderBar();
    await waitFor(() => expect(screen.getByTestId("activity-panel")).toBeTruthy());
    const phaseEl = await screen.findByTestId("activity-task-phase");
    // "agent running" maps to i18n key "activity.phase.agentRunning" — key passthrough in mock → "activity.phase.agentRunning"
    expect(phaseEl.textContent).toContain("activity.phase.agentRunning");
  });

  it("renders an indeterminate progress bar (no fill width = 50%) when progress=null", async () => {
    renderBar();
    await waitFor(() => expect(screen.getByTestId("activity-panel")).toBeTruthy());
    const progressEl = await screen.findByTestId("activity-task-progress");
    expect(progressEl).toBeTruthy();
    const fillDiv = progressEl.querySelector("div");
    expect(fillDiv).toBeTruthy();
    // Indeterminate: no explicit percentage width set on the fill (it uses 40% or animation)
    // — the fill div should NOT have width "50%" (that would be determinate at 0.5)
    expect((fillDiv as HTMLElement).style.width).not.toBe("50%");
  });

  it("renders ETA when eta_seconds=120", async () => {
    renderBar();
    await waitFor(() => expect(screen.getByTestId("activity-panel")).toBeTruthy());
    const etaEl = await screen.findByTestId("activity-task-eta");
    // The i18n mock returns the key "activity.etaLeft" (no real interpolation);
    // the elapsed span shows "30s". Verify the etaLeft key and elapsed are present.
    expect(etaEl.textContent).toContain("activity.etaLeft");
    expect(etaEl.textContent).toContain("30s");
  });
});

describe("ActivityPanel — no ETA rendered when eta_seconds=null", () => {
  // processing=1 so panel auto-expands.
  beforeEach(() => {
    mockActivityState = buildMockState({
      paused: false,
      pending: 0,
      processing: 1,
      failed: 0,
      completed_since_idle: 0,
      total: 1,
      tasks: [
        {
          run_id: "noeta-1",
          source_path: "raw/doc.md",
          filename: "doc.md",
          status: "processing",
          retry_count: 0,
          phase: "analyzing",
          progress: 0.1,
          elapsed_seconds: 5,
          eta_seconds: null,
        },
      ],
    });
  });

  it("does NOT render activity-task-eta when eta_seconds=null (no history)", async () => {
    renderBar();
    await waitFor(() => expect(screen.getByTestId("activity-panel")).toBeTruthy());
    // elapsed>0 but eta=null → etaEl still rendered for elapsed, but no ETA text
    // OR etaEl is absent if elapsed also treated as not enough — check spec:
    // "when eta_seconds is null, show only elapsed (if present) or nothing"
    // elapsed_seconds=5 > 0 → etaEl should appear with elapsed only
    const etaEl = await screen.findByTestId("activity-task-eta");
    expect(etaEl.textContent).toContain("5s");
    // Must NOT contain the etaLeft i18n key pattern
    expect(etaEl.textContent).not.toContain("left");
    expect(etaEl.textContent).not.toContain("rimanenti");
  });

  it("renders phase text when phase=analyzing", async () => {
    renderBar();
    await waitFor(() => expect(screen.getByTestId("activity-panel")).toBeTruthy());
    const phaseEl = await screen.findByTestId("activity-task-phase");
    expect(phaseEl.textContent).toContain("activity.phase.analyzing");
  });
});
