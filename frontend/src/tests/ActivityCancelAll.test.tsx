/**
 * ActivityCancelAll.test.tsx — R7-12: Cancel-all confirmation dialog.
 *
 * Coverage:
 *   1. Cancel All button opens ConfirmDialog (not window.confirm)
 *   2. ConfirmDialog has role="alertdialog" (accessible)
 *   3. Confirming → cancelRun called for each active run_id
 *   4. Cancelling the dialog → cancelRun NOT called
 *   5. Dialog disappears after confirm
 *   6. Dialog disappears after cancel
 *
 * Mocking strategy: identical to ActivityPanel.test.tsx (copy all mocks,
 * then re-set impls in beforeEach because vi.clearAllMocks() wipes them).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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
  Loader2: () => <span>Loader2</span>,
  AlertCircle: () => <span>AlertCircle</span>,
  CheckCircle2: () => <span>CheckCircle2</span>,
  ChevronUp: () => <span>ChevronUp</span>,
  ChevronDown: () => <span>ChevronDown</span>,
  Clock: () => <span>Clock</span>,
  X: () => <span>X</span>,
  RotateCcw: () => <span>RotateCcw</span>,
  PauseCircle: () => <span>PauseCircle</span>,
  PlayCircle: () => <span>PlayCircle</span>,
  Layers: () => <span>Layers</span>,
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({ vaultId: "test-vault", dataVersion: 1 }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  useGraphMeta: () => ({ dataVersion: 1 }),
}));

// ─── Mock providerStore ───────────────────────────────────────────────────────

vi.mock("../store/providerStore", () => ({
  useProviderStore: (selector: (s: unknown) => unknown) =>
    selector({ activeItem: null }),
  selectActiveProvider: (s: { activeItem: unknown }) => s.activeItem,
}));

// ─── Mock pagesClient ─────────────────────────────────────────────────────────

vi.mock("../api/pagesClient", () => ({
  fetchStatus: vi.fn().mockResolvedValue({ data_version: 1, uptime_seconds: 60 }),
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

let mockActivityState: ActivityStore;

function buildMockState(snapshot: IngestQueueSnapshot | null): ActivityStore {
  const state: ActivityStore = {
    snapshot,
    loading: false,
    error: null,
    cancellingIds: new Set<string>(),
    fetchOnce: vi.fn().mockResolvedValue(undefined),
    startPolling: vi.fn().mockReturnValue(() => {}),
    cancelRun: vi.fn().mockResolvedValue(undefined),
    retryRun: vi.fn().mockResolvedValue(undefined),
    togglePause: vi.fn().mockResolvedValue(undefined),
    resetForVault: vi.fn(),
  };
  return state;
}

vi.mock("../store/activityStore", () => {
  const useActivityStore = (selector?: (s: ActivityStore) => unknown) => {
    if (typeof selector === "function") return selector(mockActivityState);
    return mockActivityState;
  };
  // ActivityBar's adaptive /status poll reads useActivityStore.getState().snapshot (RT-1) without
  // subscribing — the mock must expose getState too, else the poll throws in fake-timer runs.
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
      return b ? { running: b.running, done: b.done, total: b.total, eta_seconds: b.eta_seconds ?? null } : null;
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

// ─── Snapshot with 2 pending tasks ────────────────────────────────────────────

const TWO_PENDING_SNAPSHOT: IngestQueueSnapshot = {
  paused: false,
  pending: 2,
  processing: 0,
  failed: 0,
  completed_since_idle: 0,
  total: 2,
  tasks: [
    { run_id: "run-a", source_path: "a.md", filename: "a.md", status: "pending", retry_count: 0 },
    { run_id: "run-b", source_path: "b.md", filename: "b.md", status: "pending", retry_count: 0 },
  ],
};

// ─── Helper ───────────────────────────────────────────────────────────────────

function openPanel() {
  fireEvent.click(screen.getByTestId("activity-panel-toggle"));
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("ActivityBar — R7-12 Cancel All confirmation dialog", () => {
  beforeEach(() => {
    // Re-build state each time because vi.clearAllMocks() wipes the mock impls.
    mockActivityState = buildMockState(TWO_PENDING_SNAPSHOT);
    // Re-set mock impls that may have been cleared.
    mockCancelIngestRun.mockResolvedValue({ run_id: "r1", status: "cancelling", cleaned_pages: 0 });
    mockRetryIngestRun.mockResolvedValue({ run_id_prev: "r1", source_path: "a.md", retry_count: 1, status: "queued" });
  });

  it("Cancel All button is visible when pending >= 2", () => {
    render(<ActivityBar />);
    openPanel();
    expect(screen.getByTestId("activity-cancel-all")).toBeDefined();
  });

  it("clicking Cancel All opens a ConfirmDialog (not window.confirm)", () => {
    render(<ActivityBar />);
    openPanel();

    fireEvent.click(screen.getByTestId("activity-cancel-all"));

    // ConfirmDialog should be rendered
    expect(screen.getByTestId("confirm-dialog")).toBeDefined();
    // cancelRun should NOT have been called yet
    expect(mockActivityState.cancelRun).not.toHaveBeenCalled();
  });

  it("ConfirmDialog has role='alertdialog' (accessible)", () => {
    render(<ActivityBar />);
    openPanel();

    fireEvent.click(screen.getByTestId("activity-cancel-all"));

    const dialog = screen.getByTestId("confirm-dialog");
    expect(dialog.getAttribute("role")).toBe("alertdialog");
  });

  it("confirming the dialog calls cancelRun for each active run_id", async () => {
    render(<ActivityBar />);
    openPanel();

    fireEvent.click(screen.getByTestId("activity-cancel-all"));
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => {
      expect(mockActivityState.cancelRun).toHaveBeenCalledWith("run-a");
      expect(mockActivityState.cancelRun).toHaveBeenCalledWith("run-b");
    });
  });

  it("dialog disappears after confirm", async () => {
    render(<ActivityBar />);
    openPanel();

    fireEvent.click(screen.getByTestId("activity-cancel-all"));
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => {
      expect(screen.queryByTestId("confirm-dialog")).toBeNull();
    });
  });

  it("cancelling the dialog does NOT call cancelRun", () => {
    render(<ActivityBar />);
    openPanel();

    fireEvent.click(screen.getByTestId("activity-cancel-all"));
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));

    expect(mockActivityState.cancelRun).not.toHaveBeenCalled();
  });

  it("dialog disappears after cancel without calling cancelRun", () => {
    render(<ActivityBar />);
    openPanel();

    fireEvent.click(screen.getByTestId("activity-cancel-all"));
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));

    expect(screen.queryByTestId("confirm-dialog")).toBeNull();
    expect(mockActivityState.cancelRun).not.toHaveBeenCalled();
  });
});
