/**
 * IngestCancelButton.test.tsx — component tests for the cancel button in IngestRunList (R13-3).
 *
 * Covers:
 *   - Cancel button renders for "running" runs.
 *   - Cancel button renders for "cancelling" runs (disabled/spinner state).
 *   - Cancel button does NOT render for terminal runs: "completed", "failed",
 *     "cancelled", "converged_false".
 *   - Clicking the cancel button fires cancelRun from the ingestStore.
 *   - A 404/409 ApiError shows a toast (not throws to the user).
 *
 * Mocking strategy:
 *   - ingestStore: mocked via vi.mock with a mutable state object.
 *   - showToast: mocked to capture calls.
 *   - react-i18next: key passthrough.
 *   - zustand/react/shallow: passthrough.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { IngestRunItem } from "../api/types";

// ─── Mock react-i18next ───────────────────────────────────────────────────────

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
    i18n: { language: "en" },
  }),
}));

// ─── Mock zustand/react/shallow ───────────────────────────────────────────────

vi.mock("zustand/react/shallow", () => ({
  useShallow: (fn: unknown) => fn,
}));

// ─── Mock showToast ───────────────────────────────────────────────────────────

const mockShowToast = vi.fn();
vi.mock("../components/common/Toast", () => ({
  showToast: (...args: unknown[]) => mockShowToast(...args),
}));

// ─── Mock StatusBadge ─────────────────────────────────────────────────────────

vi.mock("../components/ingest/StatusBadge", () => ({
  StatusBadge: ({ status }: { status: string }) => <span data-testid="status-badge">{status}</span>,
}));

// ─── Mock @tanstack/react-virtual ─────────────────────────────────────────────

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: (opts: { count: number; estimateSize: () => number }) => ({
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, i) => ({
        index: i,
        start: i * opts.estimateSize(),
        key: String(i),
      })),
    getTotalSize: () => opts.count * opts.estimateSize(),
  }),
}));

// ─── Mock ingestStore ─────────────────────────────────────────────────────────

const mockCancelRun = vi.fn();
const mockSetSelectedRunId = vi.fn();

let mockRuns: IngestRunItem[] = [];

vi.mock("../store/ingestStore", () => ({
  useIngestStore: (selector: (s: Record<string, unknown>) => unknown) => {
    const state = {
      runs: mockRuns,
      total: mockRuns.length,
      loading: false,
      selectedRunId: null,
      setSelectedRunId: mockSetSelectedRunId,
      fetchMore: vi.fn(),
      cancelRun: mockCancelRun,
    };
    return typeof selector === "function" ? selector(state) : state;
  },
  selectRuns: (s: { runs: IngestRunItem[] }) => s.runs,
  selectIngestTotal: (s: { total: number }) => s.total,
  selectIngestLoading: (s: { loading: boolean }) => s.loading,
  selectSelectedRunId: (s: { selectedRunId: string | null }) => s.selectedRunId,
  selectSetSelectedRunId: (s: { setSelectedRunId: unknown }) => s.setSelectedRunId,
  selectFetchMore: (s: { fetchMore: unknown }) => s.fetchMore,
  selectCancelRun: (s: { cancelRun: unknown }) => s.cancelRun,
}));

// ─── Import after mocks ───────────────────────────────────────────────────────

import { IngestRunList } from "../components/ingest/IngestRunList";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

function makeRun(overrides: Partial<IngestRunItem> = {}): IngestRunItem {
  return {
    id: "run-001",
    vault_id: "default",
    status: "running",
    provider_type: "local",
    pages_created: 2,
    iterations_used: 1,
    total_cost_usd: 0.0012,
    started_at: "2026-07-04T10:00:00Z",
    completed_at: null,
    error_message: null,
    ...overrides,
  };
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("IngestRunList — cancel button visibility", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCancelRun.mockResolvedValue(undefined);
  });

  it("renders cancel button for 'running' run", () => {
    mockRuns = [makeRun({ status: "running" })];
    render(<IngestRunList vaultId="default" />);
    expect(screen.getByTestId("ingest-run-cancel")).toBeTruthy();
  });

  it("renders cancel button for 'cancelling' run (disabled)", () => {
    mockRuns = [makeRun({ status: "cancelling" })];
    render(<IngestRunList vaultId="default" />);
    const btn = screen.getByTestId("ingest-run-cancel") as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(true);
  });

  it("does NOT render cancel button for 'completed' run", () => {
    mockRuns = [makeRun({ status: "completed", completed_at: "2026-07-04T10:01:00Z" })];
    render(<IngestRunList vaultId="default" />);
    expect(screen.queryByTestId("ingest-run-cancel")).toBeNull();
  });

  it("does NOT render cancel button for 'failed' run", () => {
    mockRuns = [makeRun({ status: "failed", error_message: "parse error" })];
    render(<IngestRunList vaultId="default" />);
    expect(screen.queryByTestId("ingest-run-cancel")).toBeNull();
  });

  it("does NOT render cancel button for 'cancelled' run (terminal)", () => {
    mockRuns = [makeRun({ status: "cancelled", completed_at: "2026-07-04T10:01:00Z" })];
    render(<IngestRunList vaultId="default" />);
    expect(screen.queryByTestId("ingest-run-cancel")).toBeNull();
  });

  it("does NOT render cancel button for 'converged_false' run", () => {
    mockRuns = [makeRun({ status: "converged_false", completed_at: "2026-07-04T10:01:00Z" })];
    render(<IngestRunList vaultId="default" />);
    expect(screen.queryByTestId("ingest-run-cancel")).toBeNull();
  });
});

describe("IngestRunList — cancel button fires cancelRun", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCancelRun.mockResolvedValue(undefined);
    mockRuns = [makeRun({ status: "running" })];
  });

  it("clicking the cancel button calls cancelRun with the run id", async () => {
    render(<IngestRunList vaultId="default" />);
    const btn = screen.getByTestId("ingest-run-cancel");
    fireEvent.click(btn);

    await waitFor(() => {
      expect(mockCancelRun).toHaveBeenCalledWith("run-001");
    });
  });

  it("clicking the cancel button does not trigger row selection (stopPropagation)", async () => {
    render(<IngestRunList vaultId="default" />);
    const btn = screen.getByTestId("ingest-run-cancel");

    // Reset the selection spy before clicking the cancel button
    mockSetSelectedRunId.mockClear();
    fireEvent.click(btn);

    await waitFor(() => {
      expect(mockCancelRun).toHaveBeenCalled();
    });
    // Row selection must NOT have been triggered by the cancel click
    expect(mockSetSelectedRunId).not.toHaveBeenCalled();
  });
});

describe("IngestRunList — cancel 404/409 shows toast", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRuns = [makeRun({ status: "running" })];
  });

  it("shows a toast when cancelRun rejects with 404-style ApiError", async () => {
    const { ApiError } = await import("../api/graphClient");
    mockCancelRun.mockRejectedValue(new ApiError(404, "404 Not Found"));

    render(<IngestRunList vaultId="default" />);
    const btn = screen.getByTestId("ingest-run-cancel");
    fireEvent.click(btn);

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith(
        expect.stringContaining("ingest.toastCancelError"),
        "error",
      );
    });
  });

  it("shows a toast when cancelRun rejects with 409-style ApiError", async () => {
    const { ApiError } = await import("../api/graphClient");
    mockCancelRun.mockRejectedValue(new ApiError(409, "409 Conflict"));

    render(<IngestRunList vaultId="default" />);
    const btn = screen.getByTestId("ingest-run-cancel");
    fireEvent.click(btn);

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith(
        expect.stringContaining("ingest.toastCancelError"),
        "error",
      );
    });
  });
});
