/**
 * ingest-cancel-store.test.ts — unit tests for ingestStore.cancelRun (R13-3).
 *
 * Covers:
 *   - Optimistic update to "cancelling" before the API call completes.
 *   - Final update to "cancelling" (202) or "cancelled" (200) from the response.
 *   - Revert to "running" on 404/409 ApiError + re-throws so UI can toast.
 *   - runningCount is updated correctly after each state change.
 *
 * ingestClient is mocked — no real HTTP.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ApiError } from "../api/graphClient";
import type { IngestRunItem } from "../api/types";

// ─── Mock ingestClient ────────────────────────────────────────────────────────

const mockCancelIngestRun = vi.fn();
const mockFetchIngestRuns = vi.fn();

vi.mock("../api/ingestClient", () => ({
  cancelIngestRun: (...args: unknown[]) => mockCancelIngestRun(...args),
  fetchIngestRuns: (...args: unknown[]) => mockFetchIngestRuns(...args),
}));

// ─── Mock isTauri (notification path) ────────────────────────────────────────

vi.mock("../api/base", () => ({
  apiBase: () => "",
  isTauri: () => false,
}));

// ─── Import store AFTER mocks ─────────────────────────────────────────────────

import { useIngestStore } from "../store/ingestStore";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeRun(overrides: Partial<IngestRunItem> = {}): IngestRunItem {
  return {
    id: "run-001",
    vault_id: "default",
    status: "running",
    provider_type: "local",
    pages_created: 0,
    iterations_used: 0,
    total_cost_usd: 0,
    started_at: "2026-07-04T10:00:00Z",
    completed_at: null,
    error_message: null,
    ...overrides,
  };
}

/** Reset the store to a known state with the given run in the list. */
function seedStore(run: IngestRunItem): void {
  useIngestStore.setState({
    runs: [run],
    total: 1,
    offset: 0,
    loading: false,
    error: null,
    selectedRunId: null,
    runningCount: run.status === "running" ? 1 : 0,
  });
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("ingestStore.cancelRun — optimistic update to 'cancelling'", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedStore(makeRun({ status: "running" }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("immediately sets status to 'cancelling' before the API resolves", async () => {
    // API promise is held open so we can inspect state mid-flight.
    let resolveCancel!: (value: unknown) => void;
    mockCancelIngestRun.mockReturnValue(
      new Promise((resolve) => {
        resolveCancel = resolve;
      }),
    );

    const { cancelRun } = useIngestStore.getState();
    const cancelPromise = cancelRun("run-001");

    // State should already be "cancelling" (optimistic, synchronous set)
    const { runs, runningCount } = useIngestStore.getState();
    expect(runs[0]?.status).toBe("cancelling");
    expect(runningCount).toBe(0); // "cancelling" not counted as running

    // Resolve the mock so the test can clean up
    resolveCancel({ run_id: "run-001", status: "cancelling", cleaned_pages: 0 });
    await cancelPromise;
  });
});

describe("ingestStore.cancelRun — 202 response (running run)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedStore(makeRun({ status: "running" }));
    mockCancelIngestRun.mockResolvedValue({
      run_id: "run-001",
      status: "cancelling",
      cleaned_pages: 1,
    });
  });

  it("leaves status as 'cancelling' after 202 response", async () => {
    const { cancelRun } = useIngestStore.getState();
    await cancelRun("run-001");

    const { runs } = useIngestStore.getState();
    expect(runs[0]?.status).toBe("cancelling");
  });

  it("runningCount is 0 after cancelling", async () => {
    const { cancelRun } = useIngestStore.getState();
    await cancelRun("run-001");

    expect(useIngestStore.getState().runningCount).toBe(0);
  });
});

describe("ingestStore.cancelRun — 200 response (queued run)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedStore(makeRun({ status: "running" }));
    mockCancelIngestRun.mockResolvedValue({
      run_id: "run-001",
      status: "cancelled",
    });
  });

  it("sets status to 'cancelled' after 200 response", async () => {
    const { cancelRun } = useIngestStore.getState();
    await cancelRun("run-001");

    const { runs } = useIngestStore.getState();
    expect(runs[0]?.status).toBe("cancelled");
  });

  it("runningCount is 0 after cancelled", async () => {
    const { cancelRun } = useIngestStore.getState();
    await cancelRun("run-001");

    expect(useIngestStore.getState().runningCount).toBe(0);
  });
});

describe("ingestStore.cancelRun — 404 error (unknown run)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedStore(makeRun({ status: "running" }));
    mockCancelIngestRun.mockRejectedValue(new ApiError(404, "404 Not Found"));
  });

  it("reverts status back to 'running' on 404 error", async () => {
    const { cancelRun } = useIngestStore.getState();

    await expect(cancelRun("run-001")).rejects.toBeInstanceOf(ApiError);

    const { runs } = useIngestStore.getState();
    expect(runs[0]?.status).toBe("running");
  });

  it("re-throws ApiError so callers can show a toast", async () => {
    const { cancelRun } = useIngestStore.getState();

    const err = await cancelRun("run-001").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
  });

  it("runningCount is restored to 1 after revert", async () => {
    const { cancelRun } = useIngestStore.getState();
    await cancelRun("run-001").catch(() => {});

    expect(useIngestStore.getState().runningCount).toBe(1);
  });
});

describe("ingestStore.cancelRun — 409 error (already terminal)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedStore(makeRun({ status: "running" }));
    mockCancelIngestRun.mockRejectedValue(new ApiError(409, "409 Conflict"));
  });

  it("re-throws ApiError with status 409", async () => {
    const { cancelRun } = useIngestStore.getState();

    const err = await cancelRun("run-001").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
  });

  it("reverts status to 'running' on 409", async () => {
    const { cancelRun } = useIngestStore.getState();
    await cancelRun("run-001").catch(() => {});

    const { runs } = useIngestStore.getState();
    expect(runs[0]?.status).toBe("running");
  });
});

describe("ingestStore.cancelRun — only targets the matching run", () => {
  const run1 = makeRun({ id: "run-001", status: "running" });
  const run2 = makeRun({ id: "run-002", status: "running" });

  beforeEach(() => {
    vi.clearAllMocks();
    useIngestStore.setState({
      runs: [run1, run2],
      total: 2,
      offset: 0,
      loading: false,
      error: null,
      selectedRunId: null,
      runningCount: 2,
    });
    mockCancelIngestRun.mockResolvedValue({
      run_id: "run-001",
      status: "cancelled",
    });
  });

  it("only updates the cancelled run's status, not others", async () => {
    const { cancelRun } = useIngestStore.getState();
    await cancelRun("run-001");

    const { runs } = useIngestStore.getState();
    expect(runs.find((r) => r.id === "run-001")?.status).toBe("cancelled");
    expect(runs.find((r) => r.id === "run-002")?.status).toBe("running");
  });

  it("runningCount decreases by 1 when one of two running runs is cancelled", async () => {
    const { cancelRun } = useIngestStore.getState();
    await cancelRun("run-001");

    expect(useIngestStore.getState().runningCount).toBe(1);
  });
});
