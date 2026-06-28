/**
 * import-schedule-store.test.ts — unit tests for importScheduleStore (ADR-0020 §5 / Feature S).
 *
 * AC-S1: fetchSchedule populates schedule and clears loading
 * AC-S2: saveSchedule returns ImportSchedulePutResponse and updates dirOk/dirMessage
 * AC-S3: saveSchedule handles AbortError gracefully (returns null)
 * AC-S4: runNow calls POST /import-schedule/run-now (via mocked client)
 * AC-S5: startPollingIfRunning stops when status !== "running"
 * AC-S6: all typed selectors return correct slices (I3)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act } from "@testing-library/react";

// ─── Mock the import schedule client ─────────────────────────────────────────

vi.mock("../api/importScheduleClient", () => ({
  getImportSchedule: vi.fn(),
  putImportSchedule: vi.fn(),
  runImportNow: vi.fn(),
}));

import {
  useImportScheduleStore,
  selectImportSchedule,
  selectImportLoading,
  selectImportSaving,
  selectImportRunning,
  selectImportError,
  selectImportSaveError,
  selectDirOk,
  selectDirMessage,
  selectFetchSchedule,
  selectSaveSchedule,
  selectRunNow,
} from "../store/importScheduleStore";
import {
  getImportSchedule,
  putImportSchedule,
  runImportNow,
} from "../api/importScheduleClient";
import type { ImportSchedule, ImportSchedulePutResponse } from "../api/types";

const mockGet = getImportSchedule as ReturnType<typeof vi.fn>;
const mockPut = putImportSchedule as ReturnType<typeof vi.fn>;
const mockRunNow = runImportNow as ReturnType<typeof vi.fn>;

// ─── Sample data ─────────────────────────────────────────────────────────────

const BASE_SCHEDULE: ImportSchedule = {
  enabled: true,
  source_dir: "/data/sources",
  frequency: "1h",
  last_run_at: "2026-06-28T10:00:00Z",
  last_status: "ok",
  last_imported_count: 3,
  last_error: null,
};

const PUT_RESPONSE: ImportSchedulePutResponse = {
  ...BASE_SCHEDULE,
  dir_ok: true,
  dir_message: null,
};

const PUT_RESPONSE_DIR_MISSING: ImportSchedulePutResponse = {
  ...BASE_SCHEDULE,
  dir_ok: false,
  dir_message: "/data/missing-dir",
};

// ─── Reset store state between tests ─────────────────────────────────────────

function getStore() {
  return useImportScheduleStore.getState();
}

function resetStore() {
  useImportScheduleStore.setState({
    schedule: null,
    loading: false,
    saving: false,
    running: false,
    error: null,
    saveError: null,
    dirOk: null,
    dirMessage: null,
  });
}

beforeEach(() => {
  resetStore();
  vi.clearAllMocks();
});

// ─── AC-S1: fetchSchedule ─────────────────────────────────────────────────────

describe("fetchSchedule (AC-S1)", () => {
  it("sets loading:true during fetch then false after", async () => {
    let resolveGet!: (v: ImportSchedule) => void;
    mockGet.mockReturnValue(new Promise<ImportSchedule>((r) => { resolveGet = r; }));

    const fetchFn = getStore().fetchSchedule;
    const promise = fetchFn();

    expect(getStore().loading).toBe(true);

    await act(async () => {
      resolveGet(BASE_SCHEDULE);
      await promise;
    });

    expect(getStore().loading).toBe(false);
  });

  it("populates schedule on success", async () => {
    mockGet.mockResolvedValue(BASE_SCHEDULE);

    await act(async () => {
      await getStore().fetchSchedule();
    });

    expect(getStore().schedule).toEqual(BASE_SCHEDULE);
    expect(getStore().error).toBeNull();
  });

  it("sets error on failure", async () => {
    mockGet.mockRejectedValue(new Error("Network error"));

    await act(async () => {
      await getStore().fetchSchedule();
    });

    expect(getStore().error).toBe("Network error");
    expect(getStore().schedule).toBeNull();
  });

  it("does not set error on AbortError", async () => {
    const abortErr = new Error("aborted");
    abortErr.name = "AbortError";
    mockGet.mockRejectedValue(abortErr);

    await act(async () => {
      await getStore().fetchSchedule();
    });

    expect(getStore().error).toBeNull();
  });
});

// ─── AC-S2: saveSchedule ─────────────────────────────────────────────────────

describe("saveSchedule (AC-S2)", () => {
  it("updates schedule + dirOk/dirMessage on success", async () => {
    mockPut.mockResolvedValue(PUT_RESPONSE);

    let result!: ImportSchedulePutResponse | null;
    await act(async () => {
      result = await getStore().saveSchedule({ enabled: true, frequency: "1h" });
    });

    expect(result).toEqual(PUT_RESPONSE);
    expect(getStore().schedule).toEqual(PUT_RESPONSE);
    expect(getStore().dirOk).toBe(true);
    expect(getStore().dirMessage).toBeNull();
    expect(getStore().saving).toBe(false);
  });

  it("sets dirOk:false + dirMessage when directory is missing", async () => {
    mockPut.mockResolvedValue(PUT_RESPONSE_DIR_MISSING);

    await act(async () => {
      await getStore().saveSchedule({ source_dir: "/data/missing-dir" });
    });

    expect(getStore().dirOk).toBe(false);
    expect(getStore().dirMessage).toBe("/data/missing-dir");
  });

  it("sets saveError on failure and returns null", async () => {
    mockPut.mockRejectedValue(new Error("PUT failed"));

    let result!: ImportSchedulePutResponse | null;
    await act(async () => {
      result = await getStore().saveSchedule({ enabled: false });
    });

    expect(result).toBeNull();
    expect(getStore().saveError).toBe("PUT failed");
    expect(getStore().saving).toBe(false);
  });
});

// ─── AC-S3: saveSchedule AbortError ──────────────────────────────────────────

describe("saveSchedule AbortError (AC-S3)", () => {
  it("returns null without setting saveError on AbortError", async () => {
    const abortErr = new Error("aborted");
    abortErr.name = "AbortError";
    mockPut.mockRejectedValue(abortErr);

    let result!: ImportSchedulePutResponse | null;
    await act(async () => {
      result = await getStore().saveSchedule({ enabled: true });
    });

    expect(result).toBeNull();
    expect(getStore().saveError).toBeNull();
  });
});

// ─── AC-S4: runNow ───────────────────────────────────────────────────────────

describe("runNow (AC-S4)", () => {
  it("calls runImportNow and then fetchSchedule", async () => {
    mockRunNow.mockResolvedValue(undefined);
    mockGet.mockResolvedValue({ ...BASE_SCHEDULE, last_status: "running" });

    await act(async () => {
      await getStore().runNow();
    });

    expect(mockRunNow).toHaveBeenCalledTimes(1);
    // After runNow, fetchSchedule is called to refresh
    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(getStore().running).toBe(false);
  });

  it("sets error on failure", async () => {
    mockRunNow.mockRejectedValue(new Error("409 Conflict"));
    mockGet.mockResolvedValue(BASE_SCHEDULE);

    await act(async () => {
      await getStore().runNow();
    });

    expect(getStore().error).toBe("409 Conflict");
    expect(getStore().running).toBe(false);
  });
});

// ─── AC-S5: polling stops when status is not "running" ───────────────────────

describe("startPollingIfRunning (AC-S5)", () => {
  it("does not poll when last_status is not 'running'", async () => {
    // Set a non-running schedule
    useImportScheduleStore.setState({ schedule: { ...BASE_SCHEDULE, last_status: "ok" } });
    mockGet.mockResolvedValue({ ...BASE_SCHEDULE, last_status: "ok" });

    const cleanup = getStore().startPollingIfRunning();

    // Give it time to potentially call getImportSchedule
    await new Promise((r) => setTimeout(r, 20));
    cleanup();

    // Should NOT have polled because status was not "running"
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("cleanup function returns a no-throw callable", () => {
    // startPollingIfRunning returns a cleanup fn that aborts the controller.
    // We verify it is callable without throwing (the AbortController internals
    // are tested transitively by AC-S1 AbortError handling).
    useImportScheduleStore.setState({ schedule: { ...BASE_SCHEDULE, last_status: "ok" } });
    mockGet.mockResolvedValue(BASE_SCHEDULE);

    const cleanup = getStore().startPollingIfRunning();
    expect(() => cleanup()).not.toThrow();
  });
});

// ─── AC-S6: typed selectors (I3) ─────────────────────────────────────────────

describe("typed selectors — I3 compliance (AC-S6)", () => {
  beforeEach(() => {
    useImportScheduleStore.setState({
      schedule: BASE_SCHEDULE,
      loading: true,
      saving: true,
      running: true,
      error: "err",
      saveError: "saveerr",
      dirOk: false,
      dirMessage: "/missing",
    });
  });

  it("selectImportSchedule returns schedule", () => {
    expect(selectImportSchedule(getStore())).toEqual(BASE_SCHEDULE);
  });

  it("selectImportLoading returns loading flag", () => {
    expect(selectImportLoading(getStore())).toBe(true);
  });

  it("selectImportSaving returns saving flag", () => {
    expect(selectImportSaving(getStore())).toBe(true);
  });

  it("selectImportRunning returns running flag", () => {
    expect(selectImportRunning(getStore())).toBe(true);
  });

  it("selectImportError returns error", () => {
    expect(selectImportError(getStore())).toBe("err");
  });

  it("selectImportSaveError returns saveError", () => {
    expect(selectImportSaveError(getStore())).toBe("saveerr");
  });

  it("selectDirOk returns dirOk", () => {
    expect(selectDirOk(getStore())).toBe(false);
  });

  it("selectDirMessage returns dirMessage", () => {
    expect(selectDirMessage(getStore())).toBe("/missing");
  });

  it("selectFetchSchedule returns fetchSchedule action", () => {
    expect(selectFetchSchedule(getStore())).toBe(getStore().fetchSchedule);
  });

  it("selectSaveSchedule returns saveSchedule action", () => {
    expect(selectSaveSchedule(getStore())).toBe(getStore().saveSchedule);
  });

  it("selectRunNow returns runNow action", () => {
    expect(selectRunNow(getStore())).toBe(getStore().runNow);
  });
});
